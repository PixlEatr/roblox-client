#!/usr/bin/env python3
"""Set up the Windows terminal runtime from an ordinary command prompt."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def find_native_prefix(root, env):
    candidates=[]
    try:
        config=json.loads((Path(root)/'.rove-platform.json').read_text(encoding='utf-8'))
        if isinstance(config,dict):candidates.append(config.get('native_prefix'))
    except (OSError,ValueError): pass
    for variable in ('VCPKG_ROOT','VCPKG_INSTALLATION_ROOT'):
        if env.get(variable):candidates.append(str(Path(env[variable])/'installed/x64-windows'))
    candidates.append('C:/vcpkg/installed/x64-windows')
    for candidate in candidates:
        if not candidate:continue
        path=Path(candidate)
        if (list((path/'bin').glob('libcrypto-3*.dll')) and
                any((path/'bin'/dll).is_file() for dll in ('zstd.dll','libzstd.dll'))):
            return path
    return None


def compiler_environment(env):
    env=env.copy()
    cargo=Path.home()/'.cargo/bin'
    env['PATH']=str(cargo)+os.pathsep+env.get('PATH','')
    if shutil.which('cl.exe',path=env['PATH']):return env
    installer=Path(env.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not installer.is_file():
        raise ValueError('Install Visual Studio Build Tools with Desktop development with C++, then run setup again.')
    result=subprocess.run([str(installer),'-latest','-products','*','-requires',
        'Microsoft.VisualStudio.Component.VC.Tools.x86.x64','-property','installationPath','-utf8'],
        capture_output=True,check=True,env=env)
    location=result.stdout.decode('utf-8-sig').strip()
    script=Path(location)/'VC/Auxiliary/Build/vcvars64.bat'
    if not location or not script.is_file():raise ValueError('The Visual Studio C++ x64 build tools are missing.')
    # Only the installer-discovered batch file goes into this shell command;
    # user arguments are passed directly to Python in main(), never to cmd.
    if any(c in str(script) for c in '"%\r\n'):raise ValueError('Unsupported Visual Studio installation path.')
    result=subprocess.run([env.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/u','/s','/c',
        f'call "{script}" >nul && set'],capture_output=True,check=True,env=env)
    for line in result.stdout.decode('utf-16-le').splitlines():
        name,separator,value=line.partition('=')
        if separator and name:
            # Windows environment names are case insensitive; avoid duplicate
            # Path/PATH entries when merging the compiler environment.
            for existing in list(env):
                if existing.lower()==name.lower():del env[existing]
            env[name.upper() if name.lower()=='path' else name]=value
    return env


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-prefix',type=Path)
    parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--with-ui', action='store_true')
    args=parser.parse_args()
    if os.name!='nt':parser.error('This helper runs on Windows. Use setup_terminal.py on other systems.')
    if sys.version_info<(3,12):parser.error('Install Python 3.12 or newer first.')
    if args.jobs<1:parser.error('--jobs must be positive')
    prefix=args.native_prefix or find_native_prefix(ROOT,os.environ)
    if prefix is None:
        parser.error('OpenSSL 3 and zstd were not found. See docs/setup.md, or supply --native-prefix.')
    try:
        env=compiler_environment(os.environ)
        return subprocess.call([sys.executable,str(ROOT/'scripts/setup_terminal.py'),
            '--native-prefix',str(prefix),'--jobs',str(args.jobs),
            *(['--with-ui'] if args.with_ui else [])],cwd=ROOT,env=env)
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        message=str(error) if isinstance(error,ValueError) else 'Could not initialize the C++ build tools. Try a Developer PowerShell for VS.'
        print(message,file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
