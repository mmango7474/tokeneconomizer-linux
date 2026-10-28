use std::path::PathBuf;
use te_history::{scan, Host, DEFAULT_MAX_FILES};

const USAGE: &str = "te-history scan --root ABSOLUTE_PATH --host claude|codex [--max-files 10000]";

fn run() -> Result<(), String> {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--help" | "-h") | None => {
            println!("{USAGE}");
            return Ok(());
        }
        Some("--version") => {
            println!("te-history {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        Some("scan") => {}
        _ => return Err(format!("expected scan\n{USAGE}")),
    }
    let mut root = None;
    let mut host = None;
    let mut max_files = DEFAULT_MAX_FILES;
    while let Some(arg) = args.next() {
        let value = args
            .next()
            .ok_or_else(|| format!("missing value for {arg}"))?;
        match arg.as_str() {
            "--root" if root.is_none() => root = Some(PathBuf::from(value)),
            "--host" if host.is_none() => {
                host = Some(match value.as_str() {
                    "claude" => Host::Claude,
                    "codex" => Host::Codex,
                    _ => return Err("host must be claude or codex".into()),
                })
            }
            "--max-files" => {
                max_files = value
                    .parse::<usize>()
                    .map_err(|_| "max-files must be a positive integer")?
            }
            _ => return Err(format!("unknown or repeated option: {arg}")),
        }
    }
    let root = root.ok_or("--root is required")?;
    let host = host.ok_or("--host is required")?;
    let sessions = scan(&root, host, max_files).map_err(|e| e.to_string())?;
    serde_json::to_writer(std::io::stdout().lock(), &sessions).map_err(|e| e.to_string())?;
    println!();
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("te-history: {error}");
        std::process::exit(1);
    }
}
