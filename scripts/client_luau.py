#!/usr/bin/env python3
"""Submit a Luau source file to the running custom client's local console."""
import argparse
import json

from roblox_session import ROOT
from run_world_luau import read_source
from script_console import send_source, send_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--script')
    inputs.add_argument('--job',type=int,help='Query a persistent script job')
    parser.add_argument('--timeout-ms', type=int, default=1000)
    args = parser.parse_args()
    result = (send_source(ROOT/'secrets/client-script.sock', read_source(args.script), args.timeout_ms)
              if args.script else send_request(ROOT/'secrets/client-script.sock',{'op':'status','job_id':args.job}))
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
