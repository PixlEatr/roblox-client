#!/usr/bin/env python3
"""Run the original UNC suite locally and report its actual assertions.

The default offline integration world uses a synthetic hierarchy, an unchanged
archived Roblox CoreScript module, and previously captured Animate bytecode.
It is not a live game. HTTP/WebSocket assertions use actual external services.
Drawing exports SVG; file APIs use a bounded per-VM memory workspace.
Use --snapshot runtime/tests/world_snapshot.json to test the minimal world.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SUITE_HOST=ROOT/'runtime/build/unc-headless-luau'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',type=Path,default=SUITE_HOST if SUITE_HOST.is_file() else ROOT/'rust-client/target/release/headless-luau',
                        help='Native worker; defaults to the separately validated UNC worker when present')
    parser.add_argument('--snapshot',type=Path,default=ROOT/'runtime/tests/unc_integration_snapshot.json',
                        help='World fixture; defaults to the offline integration world with real script dependencies')
    parser.add_argument('--source',type=Path,default=ROOT/'runtime/tests/unc_environment.luau')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/runtime-analysis/unc/latest.json')
    args=parser.parse_args()
    completed=subprocess.run([str(args.host.resolve()),'--source',str(args.source.resolve()),
        '--world-snapshot',str(args.snapshot.resolve()),'--timeout-ms','2000','--memory-mb','128'],
        capture_output=True,timeout=15)
    result=json.loads(completed.stdout)
    output=result.get('output','');print(output,end='')
    match=re.search(r'Tested with a (\d+)% success rate \((\d+) out of (\d+)\)',output)
    summary=None if match is None else dict(percent=int(match[1]),passed=int(match[2]),total=int(match[3]),failed=int(match[3])-int(match[2]))
    snapshot=json.loads(args.snapshot.read_text())
    report={'summary':summary,'suite_completed':match is not None,'exit_code':completed.returncode,
            'host':str(args.host.resolve()),'source_sha256':hashlib.sha256(args.source.read_bytes()).hexdigest(),
            'host_sha256':hashlib.sha256(args.host.read_bytes()).hexdigest(),
            'snapshot_sha256':hashlib.sha256(args.snapshot.read_bytes()).hexdigest(),
            'fixture_provenance':snapshot.get('fixture_provenance'),
            'snapshot':str(args.snapshot.resolve()),'result':result,'filesystem':'bounded_per_vm_memory'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    if result.get('error'):print(result['error'])
    print(f'Report: {args.output.resolve()}')
    # Completion and compatibility are different: failures retain a nonzero exit.
    return 0 if result.get('ok') and summary and not summary['failed'] else 1


if __name__=='__main__':raise SystemExit(main())
