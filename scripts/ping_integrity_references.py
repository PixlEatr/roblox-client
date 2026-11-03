"""Bounded static reference inventory for native ping integrity producers.

This scans aligned ARM64 instructions, including functions IDA has not yet
analyzed. Direct branches and nearby page-relative address materializations
are candidates, not an exhaustive indirect data-flow or producer inventory.
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
    call_targets = {
        0x100925f54: 'integer_set_insert_used_for_datamodel_flags',
        0x1036d0b98: 'protected_pair_from_strings',
        0x1036d0f98: 'protected_pair_copy',
        0x1011ff8fc: 'workspace_float_getter',
        0x10482ce08: 'datamodel_flag_reduction',
        0x10482a4e4: 'datamodel_thread_owner_check',
        0x1046e7bdc: 'physics_mode_getter',
        0x1046e7bec: 'physics_mode_setter',
    }
    data_targets = {
        0x106ac1fa0: 'physics_checked_feature_byte',
        0x106aa8ac0: 'physics_bounds_constant',
        0x106aa5030: 'workspace_getter_selector',
        0x10722f4d0: 'global_integrity_flags',
        0x1046e7bdc: 'physics_mode_getter_address',
        0x1046e7bec: 'physics_mode_setter_address',
        0x1011ff8fc: 'workspace_float_getter_address',
    }
    calls, data = [], []

    def location(index):
        address = section['address'] + index * 4
        owner = bisect.bisect_right(starts, address) - 1
        return {'at': hex(address), 'function': hex(starts[owner]),
                'end': hex(starts[owner + 1])}

    for index, insn in enumerate(code):
        pc = section['address'] + index * 4
        if insn & 0x7c000000 == 0x14000000:
            immediate = insn & 0x3ffffff
            if immediate & 0x2000000:
                immediate -= 0x4000000
            target = pc + immediate * 4
            if target in call_targets:
                calls.append({**location(index), 'target': hex(target),
                              'purpose': call_targets[target],
                              'kind': 'BL' if insn & 0x80000000 else 'B'})
        if insn & 0x9f000000 != 0x90000000:
            continue
        immediate = ((insn >> 5) & 0x7ffff) << 2 | ((insn >> 29) & 3)
        if immediate & (1 << 20):
            immediate -= 1 << 21
        page = (pc & ~4095) + (immediate << 12)
        relevant = {target: purpose for target, purpose in data_targets.items()
                    if target & ~4095 == page}
        if not relevant:
            continue
        register = insn & 31
        for next_index in range(index + 1, min(index + 17, len(code))):
            word = code[next_index]
            if (word >> 5) & 31 != register:
                continue
            target = None
            if word & 0xffc00000 == 0x91000000:
                target = page + ((word >> 10) & 4095)
            elif word & 0x3b000000 == 0x39000000 and not word & 0x04000000:
                target = page + (((word >> 10) & 4095) << (word >> 30))
            if target in relevant:
                data.append({**location(next_index), 'base_at': hex(pc),
                             'target': hex(target), 'purpose': relevant[target]})
    result = {'sha256': hashlib.sha256(binary).hexdigest(),
              'complete_producer_inventory': False, 'direct_branches': calls,
              'page_relative_candidates': data}
    (root / 'outputs/ping-analysis/integrity-reference-inventory.json').write_text(
        json.dumps(result, indent=2) + '\n')
    print(json.dumps({'direct_branch_count': len(calls), 'data_candidate_count': len(data),
                      'calls_by_target': {purpose: sum(row['target'] == hex(target) for row in calls)
                                          for target, purpose in call_targets.items()}}))


if __name__ == '__main__':
    main()
