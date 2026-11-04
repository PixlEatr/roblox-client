#!/usr/bin/env python3
"""Build the terminal client on macOS, Linux, or native Windows."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-prefix', type=Path,
        help='Windows dependency prefix, e.g. C:\\vcpkg\\installed\\x64-windows')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 4))
    parser.add_argument('--with-ui', action='store_true', help='Also build the optional browser UI (needs Node.js)')
    args = parser.parse_args()
    if sys.version_info < (3, 12): parser.error('Python 3.12 or newer is required')
    if args.jobs < 1: parser.error('--jobs must be positive')
    env = os.environ.copy()
    prefix = args.native_prefix.resolve() if args.native_prefix else None
    if os.name == 'nt':
        if prefix is None or not (prefix/'bin').is_dir():
            parser.error('--native-prefix must contain installed OpenSSL 3 and zstd DLLs')
        if not list((prefix/'bin').glob('libcrypto-3*.dll')) or not any(
                (prefix/'bin'/name).is_file() for name in ('zstd.dll','libzstd.dll')):
            parser.error('Missing OpenSSL 3 or zstd DLLs in --native-prefix/bin')
        env['CMAKE_PREFIX_PATH'] = str(prefix)
        env['PATH'] = str(prefix/'bin') + os.pathsep + env['PATH']
        env['OPENSSL_DIR'] = str(prefix)
    for program in ('cmake', 'cargo'):
        if not shutil.which(program, path=env['PATH']): parser.error(f'{program} is required on PATH')
    npm = shutil.which('npm', path=env['PATH']) if args.with_ui else None
    if args.with_ui and not npm: parser.error('Node.js and npm are required for --with-ui. See docs/setup.md.')
    python = ROOT/('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    if not python.is_file(): venv.EnvBuilder(with_pip=True).create(ROOT/'.venv')
    def run(command):
        print('Running: ' + ' '.join(str(v) for v in command[:4]), flush=True)
        subprocess.run([str(v) for v in command], cwd=ROOT, env=env, check=True)
    run([python, '-m', 'pip', 'install', '-r', ROOT/'requirements-session.txt'])
    run([python, ROOT/'runtime/build.py', '--jobs', args.jobs])
    for crate in ('rust-client', 'animation-codec'):
        run(['cargo', 'build', '--release', '--locked', '--jobs', args.jobs,
             '--manifest-path', ROOT/crate/'Cargo.toml'])
    if args.with_ui:
        run([npm, '--prefix', ROOT/'ui', 'ci'])
        run([npm, '--prefix', ROOT/'ui', 'run', 'build'])
    # Store paths only, never credentials. rove.py also supplies DLL search paths
    # to worker children when launched from a fresh Windows terminal.
    (ROOT/'.rove-platform.json').write_text(json.dumps({'native_prefix':str(prefix) if prefix else None})+'\n')
    # The setup interpreter may be outside the virtualenv containing pywin32.
    run([python, '-c', 'from pathlib import Path; import os,sys; sys.path.insert(0,"scripts"); '
        'from platform_files import protect_private,open_nofollow; p=Path("secrets"); p.mkdir(exist_ok=True); '
        'protect_private(p); c=p/"roblox-cookie.txt"; '
        'os.close(open_nofollow(c,os.O_CREAT|os.O_RDWR)); protect_private(c)'])
    run([python, ROOT/'scripts/runtime_preflight.py'])
    print('Setup complete. Add your account session to secrets/roblox-cookie.txt, then run python rove.py.\n'
          'Run python rove.py check for offline checks. See docs/setup.md for help.')
    return


if __name__ == '__main__': main()
