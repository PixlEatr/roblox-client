#!/usr/bin/env python3
"""Build Rove once, including the UI; subsequent launches need no build tools."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def find_prefix():
    candidates=[]
    settings=ROOT/'.rove-platform.json'
    if settings.is_file():
        try:candidates.append(json.loads(settings.read_text()).get('native_prefix'))
        except (OSError,ValueError):pass
    if os.environ.get('VCPKG_ROOT'):candidates.append(str(Path(os.environ['VCPKG_ROOT'])/'installed/x64-windows'))
    if os.environ.get('VCPKG_INSTALLATION_ROOT'):candidates.append(str(Path(os.environ['VCPKG_INSTALLATION_ROOT'])/'installed/x64-windows'))
    candidates.append('C:/vcpkg/installed/x64-windows')
    return next((Path(p) for p in candidates if p and (Path(p)/'bin').is_dir()),None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-prefix',type=Path)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--no-app',action='store_true',help='Skip macOS app installation; use the browser launcher')
    args=parser.parse_args()
    if sys.version_info<(3,12):parser.error('Install Python 3.12 or newer, then reopen Setup Rove.')
    if args.jobs<1:parser.error('--jobs must be positive')
    missing=[p for p in ('cargo','cmake') if not shutil.which(p)]
    if missing:parser.error('Install '+', '.join(missing)+' first. See README.md in this folder.')
    prefix=args.native_prefix or (find_prefix() if os.name=='nt' else None)
    if os.name=='nt' and prefix is None:
        parser.error('Install the Windows dependencies in README.md, or supply --native-prefix.')
    build=[sys.executable,str(ROOT/'scripts/setup_terminal.py'),'--jobs',str(args.jobs)]
    if prefix:build+=['--native-prefix',str(prefix)]
    print('Building Rove. This first setup can take several minutes.',flush=True)
    subprocess.run(build,cwd=ROOT,check=True)
    # Published source bundles already contain the built viewer assets.
    if not all((ROOT/p).is_file() for p in ('ui/avatar-viewer.js','ui/world-viewer.js')):
        npm=shutil.which('npm.cmd' if os.name=='nt' else 'npm')
        if not npm:parser.error('Install Node.js to build the missing preview files, then run setup again.')
        subprocess.run([npm,'ci'],cwd=ROOT/'ui',check=True)
        subprocess.run([npm,'run','build'],cwd=ROOT/'ui',check=True)
    if sys.platform=='darwin' and not args.no_app:
        subprocess.run([sys.executable,str(ROOT/'scripts/build_desktop.py')],cwd=ROOT,check=True)
    print('\nReady. Open Rove UI.cmd on Windows or Rove UI.command on macOS.\nConnect your account in the Setup screen. No account-file editing is needed.',flush=True)


if __name__=='__main__':
    try:main()
    except subprocess.CalledProcessError:
        raise SystemExit('Setup stopped. Fix the error shown above, then run Setup Rove again.') from None
