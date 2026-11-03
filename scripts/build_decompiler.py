#!/usr/bin/env python3
"""Build the pinned local Tovek checkout and record installation hashes."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'a70720174f61028af9e38e8f6761d18c422c0bc7'
SOURCE = ROOT/'vendor/Tovek'
REPOSITORY = 'https://github.com/Kiet1308/Tovek'


def fetch_source():
    if SOURCE.exists():
        return
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout',
                    REPOSITORY, str(SOURCE)], check=True)
    subprocess.run(['git', '-C', str(SOURCE), 'checkout', '--detach', COMMIT], check=True)


def record_installation():
    commit = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != COMMIT:
        raise RuntimeError('Tovek checkout does not match the pinned revision')
    subprocess.run(['git', '-C', str(SOURCE), 'diff', '--quiet', 'HEAD', '--'], check=True)
    binary = SOURCE/'target/fastrel/luau-lifter'
    manifest = {'repository':'https://github.com/Kiet1308/Tovek', 'commit':commit,
                'version':'2.7.0', 'profile':'fastrel', 'license':'vendor/Tovek/LICENSE.txt',
                'binary':str(binary.relative_to(ROOT)),
                'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'cargo_lock_sha256':hashlib.sha256((SOURCE/'Cargo.lock').read_bytes()).hexdigest(),
                'rustc':subprocess.check_output(['rustc', '+nightly', '--version'], text=True).strip()}
    (ROOT/'vendor/decompiler-installation.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    fetch_source()
    commit = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != COMMIT:
        raise SystemExit('Tovek revision mismatch; expected '+COMMIT)
    subprocess.run(['cargo', '+nightly', 'build', '--manifest-path', str(SOURCE/'Cargo.toml'),
                    '--locked', '--profile', 'fastrel', '-p', 'luau-lifter'], check=True)
    print(json.dumps(record_installation()))
