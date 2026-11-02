#!/usr/bin/env python3
"""Run a Luau file against a local world snapshot."""
import argparse
import json
from pathlib import Path

from local_runtime import MAX_SOURCE_BYTES, run_source
from roblox_session import ROOT
from world_snapshot import make_world_snapshot, replay_world


def read_source(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError('script exceeds 128 KiB')
    return data.decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script', required=True, type=Path)
    parser.add_argument('--report', type=Path,
                        default=ROOT/'outputs/gamejoin/private-server-bytecode-auth-visible.json')
    parser.add_argument('--timeout-ms', type=int, default=1000)
    args = parser.parse_args()
    # Resolve the local identity privately; values never enter the public report.
    user = json.loads((ROOT/'secrets/last-join.json').read_text())['body']['joinScript']['UserId']
    world = replay_world(args.report, user)
    snapshot = make_world_snapshot(world, user)
    result = run_source(read_source(args.script), timeout_ms=args.timeout_ms,
                        memory_mb=64, world_snapshot=snapshot)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
