"""Local, bounded Tovek adapter for standard, already verified Luau bytecode."""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT/'vendor/Tovek/target/fastrel/luau-lifter'
MAX_BYTECODE = 512*1024
MAX_SOURCE = 4*1024*1024


@lru_cache(maxsize=2)
def verify_installation(binary):
    manifest = json.loads((ROOT/'vendor/decompiler-installation.json').read_text())
    if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest['binary_sha256']:
        raise RuntimeError('Decompiler checksum mismatch; rebuild with scripts/build_decompiler.py')


def decompile_bytecode(bytecode, *, binary=BINARY, timeout=10):
    if not isinstance(bytecode, bytes) or not 2 <= len(bytecode) <= MAX_BYTECODE:
        raise ValueError('Bytecode is empty or exceeds 512 KiB')
    if bytecode[0] not in (13, 14) or bytecode[1] not in (1, 2, 3):
        raise ValueError('Unsupported bytecode/type version')
    binary = Path(binary).resolve()
    if not binary.is_file():
        raise RuntimeError('Build Tovek first: python3 scripts/build_decompiler.py')
    verify_installation(binary)
    with tempfile.TemporaryDirectory(prefix='rove-decompile-') as folder:
        path = Path(folder)/'script.luac'
        path.write_bytes(bytecode)
        with (Path(folder)/'source.luau').open('w+b') as output, (Path(folder)/'error.log').open('w+b') as errors:
            try:
                result = subprocess.run([str(binary), str(path), '--strict-no-synthetic-control'],
                                        stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                        timeout=timeout, check=False)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError('Decompiler timed out') from error
            if result.returncode:
                raise RuntimeError('Decompiler rejected this script')
            if output.tell() > MAX_SOURCE:
                raise ValueError('Decompiled source exceeds 4 MiB')
            output.seek(0)
            source = output.read().decode('utf-8', 'strict')
    if not source.strip() or '\0' in source:
        raise ValueError('Decompiler returned empty or invalid source')
    return '-- Reconstructed locally by Tovek V2.7; original source may differ.\n' + source


def prepare_sources(snapshot, validate_source, *, object_path=None, deadline_seconds=120):
    """Do not replace executable script catalogs; produce an export-only cache."""
    nodes = {node['id']: node for node in snapshot.get('nodes', [])}
    selected = None
    if object_path:
        current = next((n['id'] for n in nodes.values() if n['class_name']=='DataModel'), None)
        game_id = current
        service_ids = set(snapshot.get('services', {}).values())
        for segment in object_path.split('/'):
            matches = [n['id'] for n in nodes.values() if n['name']==segment and (
                n.get('parent')==current or (current==game_id and n['id'] in service_ids
                                           and n.get('parent') in (None, 'game')))]
            if len(matches) != 1:
                raise ValueError('Export object path is missing or ambiguous: ' + segment)
            current = matches[0]
        children = {}
        for node in nodes.values():
            children.setdefault(node.get('parent'), []).append(node['id'])
        selected, pending = set(), [current]
        while pending:
            identifier = pending.pop()
            if identifier not in selected:
                selected.add(identifier)
                pending.extend(children.get(identifier, []))
    sources, failures, seen = {}, [], {}
    used_bytes = 0
    end = time.monotonic()+deadline_seconds
    report = {'backend':'Tovek V2.7', 'recovered':0, 'preserved_source':0, 'unavailable':0, 'failed':failures}
    for identifier, entry in snapshot.get('script_sources', {}).items():
        if selected is not None and identifier not in selected:
            continue
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get('source'), str) and entry['source']:
            report['preserved_source'] += 1
            continue
        if not entry.get('bytecode_hex') or entry.get('signature_verified') is not True:
            report['unavailable'] += 1
            continue
        try:
            if len(entry['bytecode_hex']) > MAX_BYTECODE*2:
                raise ValueError('Bytecode exceeds 512 KiB')
            bytecode = bytes.fromhex(entry['bytecode_hex'])
            digest = hashlib.sha256(bytecode).hexdigest()
            if digest not in seen:
                remaining = end-time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Export decompilation time budget exceeded')
                source = decompile_bytecode(bytecode, timeout=min(10, remaining))
                validate_source(source)  # Compile only. Never execute recovered code here.
                cache_key = base64.b64encode(bytecode).decode("ascii")
                size = len(cache_key) + len(source.encode())
                if used_bytes + size > 8*1024*1024:
                    raise ValueError('Recovered-source cache exceeds 8 MiB')
                sources[cache_key] = source
                used_bytes += size
                seen[digest] = source
            report['recovered'] += 1
        except (ValueError, RuntimeError, OSError, UnicodeError) as error:
            failures.append({'instance_id':identifier, 'error':str(error)[:200]})
    return sources, report
