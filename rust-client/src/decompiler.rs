//! Bounded local bytecode reconstruction using the project's pinned Tovek.
//! Recovered source is returned as text; this adapter never executes it.
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf, process::{Command, Stdio}, sync::atomic::{AtomicU64, Ordering},
          thread, time::{Duration, Instant}};

const MAX_BYTECODE: usize = 512 * 1024;
const MAX_SOURCE: u64 = 4 * 1024 * 1024;
struct Temporary(PathBuf);
impl Drop for Temporary { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

fn temporary() -> Result<Temporary, String> {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    for _ in 0..100 {
        let path = std::env::temp_dir().join(format!("rove-decompile-{}-{}",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)] {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        match builder.create(&path) {
            Ok(()) => return Ok(Temporary(path)),
            Err(e) if e.kind()==std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(format!("decompiler workspace: {e}")),
        }
    }
    Err("could not allocate decompiler workspace".into())
}

pub fn decompile(value: &Value) -> Result<Value, String> {
    let encoded = value["BytecodeBase64"].as_str().ok_or("bytecode must be Base64 text")?;
    if encoded.len() > MAX_BYTECODE.div_ceil(3)*4 { return Err("bytecode exceeds 512 KiB".into()); }
    let bytecode = STANDARD.decode(encoded).map_err(|_|"invalid bytecode Base64")?;
    if bytecode.len()<2 || bytecode.len()>MAX_BYTECODE || ![13,14].contains(&bytecode[0]) || ![1,2,3].contains(&bytecode[1]) {
        return Err("unsupported or oversized Luau bytecode".into());
    }
    // Installed workers live under runtime/build; find their project first so
    // removing a staging build directory does not break this runtime API.
    let installed = std::env::current_exe().ok().and_then(|exe|
        exe.ancestors().skip(1).take(4)
            .find(|p|p.join("vendor/decompiler-installation.json").is_file())
            .map(|p|p.to_path_buf()));
    let root = installed.unwrap_or_else(||PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().to_owned());
    let manifest: Value = serde_json::from_slice(&fs::read(root.join("vendor/decompiler-installation.json"))
        .map_err(|_|"install the pinned Tovek backend first")?).map_err(|_|"invalid decompiler manifest")?;
    let binary = root.join("vendor/Tovek/target/fastrel/luau-lifter");
    let contents = fs::read(&binary).map_err(|_|"pinned Tovek binary is unavailable")?;
    if format!("{:x}",Sha256::digest(&contents)) != manifest["binary_sha256"].as_str().ok_or("missing decompiler checksum")? {
        return Err("decompiler checksum mismatch".into());
    }
    let temp = temporary()?;
    let input = temp.0.join("input.luac");let output = temp.0.join("output.luau");let errors = temp.0.join("errors.txt");
    fs::write(&input,bytecode).map_err(|e|e.to_string())?;
    let mut child = Command::new(binary).arg(input).arg("--strict-no-synthetic-control")
        .stdin(Stdio::null()).stdout(fs::File::create(&output).map_err(|e|e.to_string())?)
        .stderr(fs::File::create(&errors).map_err(|e|e.to_string())?).spawn().map_err(|e|e.to_string())?;
    let started = Instant::now();
    loop {
        let over_limit = fs::metadata(&output).map(|v|v.len()>MAX_SOURCE).unwrap_or(false)
            ||fs::metadata(&errors).map(|v|v.len()>65536).unwrap_or(false);
        if over_limit || started.elapsed()>Duration::from_secs(10) {
            let _=child.kill();let _=child.wait();
            return Err(if over_limit {"decompiler output exceeds limit"} else {"decompiler timed out"}.into());
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                if !status.success() { return Err("decompiler rejected this script".into()); }
                break;
            },
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(e) => {let _=child.kill();let _=child.wait();return Err(e.to_string());},
        }
    }
    let source=fs::read_to_string(&output).map_err(|_|"decompiler output is not UTF-8")?;
    if source.len() as u64>MAX_SOURCE || source.trim().is_empty() || source.contains('\0') {
        return Err("decompiler returned invalid or oversized source".into());
    }
    Ok(json!({"Source":format!("-- Reconstructed locally by Tovek V2.7; original source may differ.\n{source}")}))
}
