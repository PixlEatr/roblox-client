//! Data-only private-capture inspection: no VM execution and no network.
use headless_luau::{
    bytecode_format, capture_replay,
    program_check::{ProgramCheck, PublicTrust},
};
use serde_json::json;
use std::{
    fs::File,
    io::{BufReader, Read},
    path::Path,
};
fn main() {
    let result = (|| -> Result<serde_json::Value, String> {
        let args: Vec<_> = std::env::args_os().collect();
        if args.len() != 2 {
            return Err("expected a private capture file".into());
        }
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let trust = PublicTrust::from_files(
            &root.join("inputs/bytecode-signature-v1-public.bin"),
            &root.join("inputs/bytecode-signature-v1-public.json"),
        )
        .map_err(|e| e.to_string())?;
        let file = File::open(&args[1]).map_err(|_| "capture unavailable")?;
        if !file
            .metadata()
            .map_err(|_| "capture metadata unavailable")?
            .is_file()
        {
            return Err("capture is not a regular file".into());
        }
        let mut programs = Vec::new();
        let count = capture_replay::read_program_checks(
            BufReader::new(file.take(512 * 1024 * 1024 + 1)),
            |packet, _| {
                if programs.len() >= 16 {
                    return Err("program check inspection limit reached".into());
                }
                let verified = ProgramCheck::parse(packet)
                    .and_then(|p| p.verify(&trust))
                    .map_err(|e| e.to_string())?;
                let normalized = bytecode_format::normalize(verified.original_bytes(), None)
                    .map_err(|e| e.to_string())?;
                let mut summary = verified.summary();
                summary["format"] = normalized.metadata;
                programs.push(summary);
                Ok(())
            },
        )?;
        if count == 0 {
            return Err("capture contains no received program check".into());
        }
        Ok(
            json!({"ok":true,"mode":"offline","python_processes":0,"programs":programs,
            "checks_verified":count,"programs_executed":0,"network_used":false,"replies_sent":0}),
        )
    })();
    match result {
        Ok(value) => println!("{value}"),
        Err(error) => {
            println!("{}", json!({"ok":false,"error":error}));
            std::process::exit(1);
        }
    }
}
