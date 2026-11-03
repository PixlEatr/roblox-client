#!/usr/bin/env python3
"""Open the ordinary terminal client in the requested sUNC test game."""
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
PLACE_ID='90441122676618'


def main():
    worker=ROOT/'runtime/build/unc-headless-luau'
    if not worker.is_file():raise SystemExit('Build/install the compatibility worker first')
    os.environ['RCR_LUAU_LARGE_TEST']='1'
    os.environ['ROBLOX_LUAU_RUST_HOST']=str(worker)
    # Import after selecting the worker so all adapters use the same binary.
    import terminal_client
    if not any(arg=='--place' or arg.startswith('--place=') for arg in sys.argv[1:]):
        sys.argv.extend(['--place',PLACE_ID])
    print('Runtime: 100 MiB script files; 512 MiB VM; HTTP bodies up to 16 MiB.',flush=True)
    print('Long source jobs resume across short ticks, bounded to 30 seconds of uninterrupted work.',flush=True)
    print('After joining, paste your sUNC loadstring(game:HttpGet(...))() line at luau>.',flush=True)
    print('Results and failures will appear in this terminal; a passing score is not guaranteed.',flush=True)
    return terminal_client.main()


if __name__=='__main__':raise SystemExit(main())
