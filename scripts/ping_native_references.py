"""Static ARM64 candidate scan for ping telemetry; does not execute the client.

Aligned instructions only. Results are candidates, not a complete data-flow
analysis: indirect aliases and addresses materialized without ADRP are outside
this scan. Source hash and function boundaries accompany each output.
"""
import bisect
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())
    binary = (root / 'inputs/RobloxPlayer-0.745-arm64').read_bytes()
    section = layout['text']
    code = memoryview(binary)[section['offset']:section['offset'] + section['size']].cast('I')
    starts = layout['functions']
    target = 0x10722f4d0
    global_refs, field_refs = [], []

    def location(index):
        address = section['address'] + 4 * index
        function_index = bisect.bisect_right(starts, address) - 1
        return {'at': hex(address), 'function': hex(starts[function_index]),
                'end': hex(starts[function_index + 1])}

    for index, insn in enumerate(code):
        if insn & 0x9f000000 == 0x90000000:  # ADRP
            immediate = ((insn >> 5) & 0x7ffff) << 2 | ((insn >> 29) & 3)
            if immediate & (1 << 20):
                immediate -= 1 << 21
            pc = section['address'] + 4 * index
            if (pc & ~4095) + (immediate << 12) != target & ~4095:
                continue
            register = insn & 31
            for following in range(index + 1, min(index + 17, len(code))):
                word = code[following]
                if (word >> 5) & 31 != register:
                    continue
                match = None
                if word & 0xffc00000 == 0x91000000 and (word >> 10) & 4095 == target & 4095:
                    match = 'ADRP+ADD address'
                elif word & 0x3b000000 == 0x39000000 and not word & 0x04000000:
                    if (((word >> 10) & 4095) << (word >> 30)) == target & 4095:
                        match = 'ADRP+unsigned-load/store'
                if match:
                    global_refs.append({**location(following), 'base_at': hex(pc), 'kind': match})
        # Direct scalar load/store references to the three Replicator offsets.
        # These offsets occur in unrelated objects too; inspect each owner.
        if insn & 0x3b000000 == 0x39000000 and not insn & 0x04000000:
            offset = ((insn >> 10) & 4095) << (insn >> 30)
            if offset in (5964, 9736, 9740):
                field_refs.append({**location(index), 'offset': offset,
                                   'access': 'load' if insn & (1 << 22) else 'store'})
    result = {'sha256': hashlib.sha256(binary).hexdigest(),
              'complete_data_flow_analysis': False,
              'global_flag_references': global_refs,
              'replicator_field_candidates': field_refs}
    output = root / 'outputs/ping-analysis/aligned-native-references.json'
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'global_reference_count': len(global_refs),
                      'field_candidate_count': len(field_refs)}))


if __name__ == '__main__':
    main()
