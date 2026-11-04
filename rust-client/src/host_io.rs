//! Explicit platform clipboard writes; no shell or protocol-stdin access.
use serde_json::json;
use std::{
    io::Write,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

fn clipboard_command() -> Result<Command, String> {
    #[cfg(target_os="macos")]
    { let mut c=Command::new("/usr/bin/pbcopy");c.env("LC_CTYPE","en_US.UTF-8");Ok(c) }
    #[cfg(target_os="windows")]
    {
        let mut c=Command::new("powershell.exe");
        c.args(["-NoProfile","-NonInteractive","-Command",
            "[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false); Set-Clipboard -Value ([Console]::In.ReadToEnd())"]);
        Ok(c)
    }
    #[cfg(target_os="linux")]
    {
        if std::env::var_os("WAYLAND_DISPLAY").is_some() {
            let mut c=Command::new("wl-copy");c.args(["--type","text/plain;charset=utf-8"]);return Ok(c);
        }
        if std::env::var_os("DISPLAY").is_some() {
            let mut c=Command::new("xclip");c.args(["-selection","clipboard","-in"]);return Ok(c);
        }
        Err("clipboard needs a graphical session (wl-copy on Wayland or xclip on X11)".into())
    }
}

fn copy(text: &[u8]) -> Result<(), String> {
    if text.len() > 128 * 1024 {
        return Err("clipboard text exceeds 128 KiB".into());
    }
    std::str::from_utf8(text).map_err(|_| "clipboard text must be UTF-8")?;
    let mut child = clipboard_command()?
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut input = child.stdin.take().ok_or("clipboard stdin unavailable")?;
    let bytes = text.to_vec();
    let writer = thread::spawn(move || input.write_all(&bytes));
    let deadline = Instant::now() + Duration::from_secs(5);
    let result = loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                break if status.success() {
                    Ok(())
                } else {
                    Err("platform rejected clipboard write".into())
                }
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(2)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err("clipboard write timed out".into());
            }
            Err(e) => {
                let _ = child.kill();
                let _ = child.wait();
                break Err(e.to_string());
            }
        }
    };
    let written = writer
        .join()
        .map_err(|_| "clipboard writer failed".to_string())?
        .map_err(|e| e.to_string());
    result.and(written)
}
pub fn set_clipboard(text: &[u8]) -> Vec<u8> {
    let result = match copy(text) {
        Ok(()) => json!({"ok":true}),
        Err(error) => json!({"error":error}),
    };
    serde_json::to_vec(&result).expect("clipboard response serialization")
}
