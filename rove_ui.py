#!/usr/bin/env python3
"""Start Rove, running first-time setup when needed."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent


def main():
    try:
        if sys.platform == 'darwin':
            return subprocess.call(['/bin/zsh',str(ROOT/'Start.command'),*sys.argv[1:]],cwd=ROOT)
        sys.path.insert(0,str(ROOT/'scripts'))
        from start_ui import main as start
        return start()
    except KeyboardInterrupt: return 130


if __name__=='__main__':raise SystemExit(main())
