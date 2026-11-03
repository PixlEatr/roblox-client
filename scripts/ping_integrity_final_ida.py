"""Bounded final native integrity evidence extraction for application ping."""
import json
from pathlib import Path
import struct
import traceback

import ida_bytes
import ida_funcs
import ida_lines
import ida_pro
import idautils
import idc


DATA = {
    0x106AA8AC0: (24, 'world_bounds_configuration'),
    0x105E4D620: (8, 'physics_average_upper_bound'),
    0x105E4D628: (8, 'physics_average_lower_bound'),
    0x105E4D630: (16, 'world_bounds_order_mask'),
    0x105E4D640: (4, 'world_bounds_component_lower_bound'),
    0x1059BD8BC: (4, 'world_bounds_component_upper_bound'),
}


def disassembly(address, count):
    rows = []
    for offset in range(count):
        at = address + 4 * offset
        rows.append({'address': hex(at), 'instruction': ida_lines.tag_remove(
            idc.generate_disasm_line(at, 0) or '')})
    return rows


def main():
    root = Path(idc.ARGV[1])
    constants = {}
    for address, (size, purpose) in DATA.items():
        raw = ida_bytes.get_bytes(address, size)
        if raw is None or len(raw) != size:
            raise ValueError('Unable to read native constant at %x' % address)
        constants[purpose] = {
            'address': hex(address), 'hex': raw.hex(),
            'float32': list(struct.unpack('<' + 'f' * (size // 4), raw)),
        }
        if size % 8 == 0:
            constants[purpose]['float64'] = list(struct.unpack('<' + 'd' * (size // 8), raw))
    references = []
    for xref in idautils.XrefsTo(0x10722F4D0):
        function = ida_funcs.get_func(xref.frm)
        references.append({
            'address': hex(xref.frm), 'type': xref.type,
            'function': hex(function.start_ea) if function else None,
            'instruction': ida_lines.tag_remove(idc.generate_disasm_line(xref.frm, 0) or ''),
            'nearby': disassembly(xref.frm - 8, 6),
        })
    result = {
        'database': idc.get_idb_path(),
        'constants': constants,
        'global_integrity_references': references,
        'bounds_transform_10069ac70': disassembly(0x10069AC70, 24),
    }
    output = root / 'outputs/ping-analysis/integrity-final-extraction.json'
    output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        traceback.print_exc()
        ida_pro.qexit(1)
    ida_pro.qexit(0)
