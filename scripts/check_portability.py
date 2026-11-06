#!/usr/bin/env python3
"""Offline terminal/runtime checks, on the operating system executing this file."""
import os
import argparse
from pathlib import Path
import subprocess
import sys
from platform_environment import runtime_environment

ROOT = Path(__file__).resolve().parents[1]


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    env = runtime_environment(ROOT)
    env['PYTHONPATH'] = os.pathsep.join(str(ROOT/p) for p in ('scripts', 'runtime', 'tests/scripts', 'tests/runtime'))
    env['PYTHONUTF8'] = '1'
    # Defaults resolve .exe names on Windows. Missing binaries are failures,
    # rather than allowing the runtime test classes to silently skip.
    from local_runtime import HOST, RUST_HOST
    for path in (HOST, RUST_HOST):
        if not path.is_file(): raise SystemExit(f'Build required: {path}')
    env['CFRAME_TEST_HOSTS'] = os.pathsep.join(map(str, (HOST, RUST_HOST)))
    env['CFRAME_PERSISTENT_HOST'] = str(RUST_HOST)
    subprocess.run([sys.executable, '-m', 'unittest', '-v',
        'test_rove_entrypoint', 'test_platform_portability', 'test_windows_setup', 'test_extended_compat', 'test_rust_host',
        'test_platform_standing', 'test_move_to_completion', 'test_world_action_journal'],
        cwd=ROOT, env=env, check=True)
    subprocess.run([sys.executable, str(ROOT/'scripts/runtime_preflight.py')],
                   cwd=ROOT, env=env, check=True)


if __name__ == '__main__': main()
