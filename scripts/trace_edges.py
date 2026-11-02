#!/usr/bin/env python3
"""Collect exact ARM64 direct B/BL edges to selected static function starts."""
import bisect
import json
from pathlib import Path
import struct

root = Path(__file__).resolve().parent.parent
layout = json.loads((root / 'outputs/macho-layout.json').read_text())
provenance = json.loads((root / 'outputs/provenance.json').read_text())
selected = {int(p.stem, 16) for p in (root / 'outputs/ida').glob('*.json') if p.stem != 'manifest'}
extra = root / 'scripts/followup-functions.json'
if extra.exists():
    selected.update(x['start'] for x in json.loads(extra.read_text()))
data = Path(provenance['copy']).read_bytes()
text = layout['text']
starts = layout['functions']
edges = []
for i, (word,) in enumerate(struct.iter_unpack('<I', data[text['offset']:text['offset'] + text['size']])):
    if word & 0x7c000000 != 0x14000000:
        continue
    immediate = word & 0x3ffffff
    if immediate & 0x2000000:
        immediate -= 0x4000000
    address = text['address'] + 4 * i
    target = address + immediate * 4
    if target not in selected:
        continue
    index = bisect.bisect_right(starts, address) - 1
    edges.append({'instruction': address, 'target': target, 'kind': 'BL' if word & 0x80000000 else 'B',
                  'caller_start': starts[index] if index >= 0 else None,
                  'caller_end': starts[index + 1] if index + 1 < len(starts) else text['address'] + text['size']})
(root / 'outputs/direct-edges.json').write_text(json.dumps(edges, indent=2) + '\n')
print(json.dumps({'selected_functions': len(selected), 'direct_edges': len(edges), 'callers': len({x['caller_start'] for x in edges})}))
