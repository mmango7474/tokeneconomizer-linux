//! Read only transcript identity headers. No transcript text is emitted or executed.
use serde::Serialize;
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

pub const HEADER_LIMIT: usize = 262_144;
pub const DEFAULT_MAX_FILES: usize = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Host {
    Claude,
    Codex,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Session {
    pub path: String,
    pub cwd: String,
    pub session_id: String,
    pub mtime_ns: u64,
    pub size: u64,
}

fn nonempty_string(value: Option<&Value>) -> Option<&str> {
    value?
        .as_str()
        .filter(|s| !s.is_empty() && !s.contains('\0'))
}

/// Only complete newline-delimited records within the header bound are considered.
/// Identity must occur in one record; user/tool text is never searched recursively.
pub fn identity(header: &[u8], host: Host) -> Option<(String, String)> {
    for raw in header.split_inclusive(|b| *b == b'\n') {
        if !raw.ends_with(b"\n") {
            continue;
        }
        let Ok(record) = serde_json::from_slice::<Value>(raw) else {
            continue;
        };
        let fields = match host {
            Host::Claude => {
                // Sidechains are child agents rather than independent evidence sessions.
                if record.get("isSidechain").and_then(Value::as_bool) == Some(true) {
                    return None;
                }
                &record
            }
            Host::Codex => {
                if record.get("type").and_then(Value::as_str) != Some("session_meta") {
                    continue;
                }
                let Some(payload) = record.get("payload") else {
                    continue;
                };
                payload
            }
        };
        let id_key = if host == Host::Claude {
            "sessionId"
        } else {
            "id"
        };
        if let (Some(cwd), Some(id)) = (
            nonempty_string(fields.get("cwd")),
            nonempty_string(fields.get(id_key)),
        ) {
            if Path::new(cwd).is_absolute() {
                return Some((cwd.into(), id.into()));
            }
        }
    }
    None
}

fn read_session(path: &Path, host: Host) -> io::Result<Option<Session>> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let mut file = match options.open(path) {
        Ok(file) => file,
        // A log removed or replaced by a symlink during discovery is not a candidate.
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        #[cfg(unix)]
        Err(e) if e.raw_os_error() == Some(libc::ELOOP) => return Ok(None),
        Err(e) => return Err(e),
    };
    let before = file.metadata()?;
    if !before.is_file() {
        return Ok(None);
    }
    let mut header = Vec::with_capacity(HEADER_LIMIT.min(before.len() as usize));
    (&mut file)
        .take(HEADER_LIMIT as u64)
        .read_to_end(&mut header)?;
    let after = file.metadata()?;
    if before.len() != after.len() || before.modified()? != after.modified()? {
        return Ok(None);
    }
    let Some((cwd, session_id)) = identity(&header, host) else {
        return Ok(None);
    };
    // Reject non-Unicode paths instead of lossy conversion to a different filename.
    let path = path.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            "history path is not valid Unicode",
        )
    })?;
    let mtime_ns = after
        .modified()?
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|d| u64::try_from(d.as_nanos()).ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "unsupported mtime"))?;
    Ok(Some(Session {
        path: path.into(),
        cwd,
        session_id,
        mtime_ns,
        size: after.len(),
    }))
}

/// Recursively discover JSONL logs without following directory or file symlinks.
/// A missing root is an empty history. Limits fail explicitly, never truncate evidence.
pub fn scan(root: &Path, host: Host, max_files: usize) -> io::Result<Vec<Session>> {
    if !root.is_absolute() || max_files == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "root must be absolute and max-files must be positive",
        ));
    }
    let meta = match fs::symlink_metadata(root) {
        Ok(meta) => meta,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    if meta.file_type().is_symlink() || !meta.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "history root must be a directory, not a symlink",
        ));
    }
    let mut pending: Vec<PathBuf> = vec![root.into()];
    let mut sessions = Vec::new();
    let mut count = 0usize;
    while let Some(dir) = pending.pop() {
        // Recheck directories queued earlier; read_dir must not follow a substituted link.
        let meta = match fs::symlink_metadata(&dir) {
            Ok(meta) => meta,
            Err(e) if e.kind() == io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e),
        };
        if meta.file_type().is_symlink() || !meta.is_dir() {
            continue;
        }
        for entry in fs::read_dir(dir)? {
            let entry = entry?;
            let kind = entry.file_type()?;
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if kind.is_symlink()
                || name == "subagents"
                || (host == Host::Claude && name.starts_with("agent-"))
            {
                continue;
            }
            let path = entry.path();
            if kind.is_dir() {
                pending.push(path);
            } else if kind.is_file() && path.extension().is_some_and(|ext| ext == "jsonl") {
                count += 1;
                if count > max_files {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "history scan exceeded --max-files; narrow --root or raise the limit",
                    ));
                }
                if let Some(session) = read_session(&path, host)? {
                    sessions.push(session);
                }
            }
        }
    }
    sessions.sort_by(|a, b| {
        a.mtime_ns
            .cmp(&b.mtime_ns)
            .then_with(|| a.path.cmp(&b.path))
    });
    Ok(sessions)
}
