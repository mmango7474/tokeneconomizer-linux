use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use te_history::{identity, scan, Host, HEADER_LIMIT};

static NEXT: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "te-history-test-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn log(&self, name: &str, bytes: &[u8]) {
        let path = self.0.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn claude() -> &'static [u8] {
    b"{\"cwd\":\"/repo\",\"sessionId\":\"s1\"}\n"
}

#[test]
fn malformed_nested_and_incomplete_records_do_not_become_identity() {
    let header = b"garbage\n[]\n{\"tool_result\":{\"cwd\":\"/fake\",\"sessionId\":\"fake\"}}\n{\"cwd\":9,\"sessionId\":\"wrong\"}\n{\"cwd\":\"/valid\",\"sessionId\":\"s\"}\n{\"cwd\":";
    assert_eq!(
        identity(header, Host::Claude),
        Some(("/valid".into(), "s".into()))
    );
    assert_eq!(
        identity(b"{\"cwd\":\"/repo\",\"sessionId\":\"s\"}", Host::Claude),
        None
    );
    assert_eq!(
        identity(
            b"{\"cwd\":\"relative\",\"sessionId\":\"s\"}\n",
            Host::Claude
        ),
        None
    );
}

#[test]
fn codex_requires_session_meta() {
    let header = b"{\"type\":\"tool_result\",\"payload\":{\"cwd\":\"/fake\",\"id\":\"fake\"}}\n{\"type\":\"session_meta\",\"payload\":{\"cwd\":\"/real\",\"id\":\"s1\"}}\n";
    assert_eq!(
        identity(header, Host::Codex),
        Some(("/real".into(), "s1".into()))
    );
    assert_eq!(identity(header, Host::Claude), None);
}

#[test]
fn recursive_scan_preserves_unicode_and_skips_subagents() {
    let temp = Temp::new();
    temp.log(
        "project/日本語.jsonl",
        "{\"cwd\":\"/日本語\",\"sessionId\":\"✓\"}\n".as_bytes(),
    );
    temp.log("project/subagents/child.jsonl", claude());
    temp.log("project/agent-old.jsonl", claude());
    temp.log("project/ignored.txt", claude());
    temp.log(
        "project/sidechain.jsonl",
        b"{\"isSidechain\":true,\"cwd\":\"/repo\",\"sessionId\":\"child\"}\n",
    );
    let sessions = scan(&temp.0, Host::Claude, 10).unwrap();
    assert_eq!(sessions.len(), 1);
    assert_eq!(sessions[0].cwd, "/日本語");
    assert_eq!(sessions[0].session_id, "✓");
    assert!(sessions[0].path.ends_with("日本語.jsonl"));
    assert_eq!(sessions, scan(&temp.0, Host::Claude, 10).unwrap());
}

#[test]
fn bounds_header_and_fails_file_limit_explicitly() {
    let temp = Temp::new();
    let mut bytes = vec![b' '; HEADER_LIMIT];
    bytes.push(b'\n');
    bytes.extend_from_slice(claude());
    temp.log("huge.jsonl", &bytes);
    assert!(scan(&temp.0, Host::Claude, 1).unwrap().is_empty());
    temp.log("second.jsonl", claude());
    assert!(scan(&temp.0, Host::Claude, 1)
        .unwrap_err()
        .to_string()
        .contains("max-files"));
    assert!(scan(&temp.0, Host::Claude, 0).is_err());
}

#[test]
fn missing_root_is_empty_and_relative_root_is_rejected() {
    let temp = Temp::new();
    assert!(scan(&temp.0.join("absent"), Host::Claude, 10)
        .unwrap()
        .is_empty());
    assert!(scan(std::path::Path::new("relative"), Host::Claude, 10).is_err());
}

#[cfg(unix)]
#[test]
fn skips_file_and_directory_symlinks_and_rejects_link_root() {
    use std::os::unix::fs::symlink;
    let temp = Temp::new();
    let outside = Temp::new();
    outside.log("private.jsonl", claude());
    symlink(outside.0.join("private.jsonl"), temp.0.join("file.jsonl")).unwrap();
    symlink(&outside.0, temp.0.join("directory")).unwrap();
    symlink(&temp.0, temp.0.join("cycle")).unwrap();
    assert!(scan(&temp.0, Host::Claude, 10).unwrap().is_empty());
    assert!(scan(&temp.0.join("directory"), Host::Claude, 10).is_err());
}
