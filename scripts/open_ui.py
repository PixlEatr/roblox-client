#!/usr/bin/env python3
"""Open the shared Rove UI using the macOS app or the default browser."""
import argparse
from pathlib import Path
import plistlib
import subprocess
import sys
import webbrowser

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',action='store_true',help='Use the browser even when the macOS app is installed')
    parser.add_argument('--url-only',action='store_true',help='Start the backend and print its URL')
    args=parser.parse_args()
    if sys.platform=='darwin' and not (args.browser or args.url_only):
        app=Path.home()/'Applications/Rove.app'
        try:
            info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            if info.get('RoveProjectRoot')==str(ROOT) and info.get('RoveLauncherVersion')==2:
                return subprocess.call(['open',str(app)])
        except (OSError,ValueError):pass
    try:
        from desktop_backend import ensure_backend
        url=ensure_backend()
    except ImportError:
        print('Rove needs its Python dependencies. Run Setup Rove again.',file=sys.stderr);return 1
    except (OSError,SystemExit) as error:
        print(str(error),file=sys.stderr);return 1
    print(url,flush=True)
    if not args.url_only and not webbrowser.open(url):
        print('Paste the address above into your browser to open Rove.')
    return 0


if __name__=='__main__':raise SystemExit(main())
