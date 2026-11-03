#!/usr/bin/env python3
"""Build the bundled Luau runtime."""
import argparse
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=HERE / 'build')
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 8))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.output_dir.resolve()
    subprocess.run(['cmake', '-S', str(HERE), '-B', str(out),
                    '-DCMAKE_BUILD_TYPE=Release'], check=True)
    subprocess.run(['cmake', '--build', str(out), '--parallel', str(args.jobs)], check=True)
    print(out / 'luau-host')


if __name__ == '__main__':
    main()
