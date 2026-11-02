#!/usr/bin/env python3
"""Launch the installed IDA CLI against the copied executable, never a process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse", action="store_true", help="Analyze the existing research IDB")
    parser.add_argument("--database", default="ida/RobloxPlayer-0.745-arm64.i64", help="Project-relative analysis database")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / "outputs/provenance.json").read_text())
    source = Path(provenance["source"])
    target = Path(provenance["copy"])
    expected = provenance["sha256"]
    if digest(source) != expected or digest(target) != expected:
        raise SystemExit("Binary provenance changed; establish a new baseline before analysis")
    executable = Path("/Applications/IDA Professional 9.4.app/Contents/MacOS/idat")
    database = (root / args.database).resolve()
    if not database.is_relative_to(root / "ida") or database.suffix != ".i64":
        raise SystemExit("Analysis database must be an .i64 file inside this project's ida directory")
    if not args.reuse and database.exists():
        raise SystemExit("Database exists; use --reuse or choose a new research copy")
    command = [str(executable), "-A", "-a", "-L" + str(root / "outputs/ida.log"),
               '-S"' + str(root / "scripts/ida_extract.py") + '" "' + str(root) + '"']
    if not args.reuse:
        command += ["-o" + str(database)]
    command += [str(database if args.reuse else target)]
    then = time.monotonic()
    env = os.environ.copy()
    env["TVHEADLESS"] = "1"
    timed_out = False
    with (root / "outputs/ida-launch.log").open("w") as output:
        try:
            process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=600, env=env)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            returncode, timed_out = -1, True
    manifest = {"command": command, "exit_code": returncode, "timed_out": timed_out,
                "seconds": round(time.monotonic() - then, 3), "source_sha256_after": digest(source),
                "copy_sha256_after": digest(target), "expected_sha256": expected,
                "source_unchanged": digest(source) == expected, "copy_unchanged": digest(target) == expected}
    (root / "outputs/run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    if returncode != 0 or not manifest["source_unchanged"] or not manifest["copy_unchanged"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
