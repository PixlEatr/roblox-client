#!/usr/bin/env python3
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description='Offline project checks')
parser.add_argument('suite',choices=['scripts','runtime','ui','all'],default='all',nargs='?')
args=parser.parse_args()
for folder in ('scripts','runtime','tests/scripts','tests/runtime'):
    sys.path.insert(0,str(ROOT/folder))
import unittest
ok=True
for suite in (['scripts','runtime','ui'] if args.suite=='all' else [args.suite]):
    if suite=='ui':
        ok=(subprocess.run(['npm','--prefix',str(ROOT/'ui'),'test']).returncode==0) and ok
    else:
        tests=unittest.defaultTestLoader.discover(str(ROOT/'tests'/suite),top_level_dir=str(ROOT/'tests'/suite))
        ok=unittest.TextTestRunner(verbosity=1).run(tests).wasSuccessful() and ok
raise SystemExit(0 if ok else 1)
