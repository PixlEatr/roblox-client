#!/usr/bin/env python3
"""Build the local macOS Rove.app with the same browser UI and backend."""
from pathlib import Path
import plistlib
import subprocess
import shutil

ROOT=Path(__file__).resolve().parents[1]
# Build outside iCloud: FileProvider adds FinderInfo to .app directories and
# invalidates bundle signing in Documents. Keep a project shortcut as well.
APP=Path.home()/'Applications/Rove.app'


def main():
    contents=APP/'Contents';binary=contents/'MacOS/Rove'
    binary.parent.mkdir(parents=True,exist_ok=True)
    (contents/'Resources').mkdir(exist_ok=True)
    plist={'CFBundleExecutable':'Rove','CFBundleIdentifier':'local.rove.client',
        'CFBundleName':'Rove','CFBundleDisplayName':'Rove','CFBundleVersion':'1',
        'CFBundleShortVersionString':'0.1.0','CFBundlePackageType':'APPL',
        'LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,
        'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'RoveProjectRoot':str(ROOT)}
    (contents/'Info.plist').write_bytes(plistlib.dumps(plist))
    old=ROOT/'dist/Rove.app/Contents/MacOS/Rove'
    if old.is_file() and old.stat().st_mtime>(ROOT/'native/Rove.swift').stat().st_mtime:
        if old.resolve()!=binary.resolve():shutil.copyfile(old,binary)
        binary.chmod(0o755)
    else:
        subprocess.run(['xcrun','swiftc','-O','-target','arm64-apple-macosx13.0',
            '-framework','AppKit','-framework','WebKit',str(ROOT/'native/Rove.swift'),'-o',str(binary)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(APP)],check=True)
    shortcut=ROOT/'dist/Rove.app'
    if shortcut.is_dir() and not shortcut.is_symlink():shutil.rmtree(shortcut)
    if not shortcut.exists():shortcut.symlink_to(APP,target_is_directory=True)
    print(APP)


if __name__=='__main__':main()
