"""Pinned, unmodified client player scripts for the headless engine.

These are client-owned defaults. Game-provided StarterPlayerScripts take
precedence, and the native scheduler runs the scripts in ordinary game scopes.
"""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'vendor' / 'roblox-player-scripts-745'


@lru_cache(maxsize=1)
def player_scripts():
    manifest = json.loads((BUNDLE / 'provenance.json').read_text())
    files = {}
    for entry in manifest['files']:
        path = entry['path']
        if Path(path).is_absolute() or '..' in Path(path).parts:
            raise ValueError('Invalid builtin script path')
        raw = (BUNDLE / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Builtin script differs from its pinned source: {path}')
        files[path] = raw.decode('utf-8')

    # rbxp explicitly declares the two LocalScript roots; files below a
    # .module directory and files with matching children are ModuleScripts.
    root_types = {}
    for name in ('PlayerScriptsCommon.rbxp', 'PlayerScriptsLoader.rbxp'):
        topology = json.loads(re.sub(r',\s*([}\]])', r'\1', files[name]))
        roots = topology['instanceHierarchy']['tree']['StarterPlayer']['StarterPlayerScripts']
        for name, node in roots.items():
            root_types[name] = node['$className']

    nodes = {}
    for path, source in files.items():
        if not path.endswith('.lua'):
            continue
        parts = Path(path).parts[1:]
        names = tuple(part.removesuffix('.lua').removesuffix('.module') for part in parts)
        for depth in range(1, len(names)):
            nodes.setdefault(names[:depth], {'class_name': 'Folder'})
        nodes[names] = {
            'class_name': root_types.get(names[0], 'ModuleScript') if len(names) == 1 else 'ModuleScript',
            'source': source,
            'repository_path': 'scripts/PlayerScripts/' + path,
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        }
    return tuple(dict(node, path=list(path)) for path, node in sorted(nodes.items(), key=lambda item: (len(item[0]), item[0])))
