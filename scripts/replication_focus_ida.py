"""Extract native replication-focus item wire readers."""
import json
from pathlib import Path
import traceback

import ida_funcs
import ida_hexrays
import ida_idaapi
import ida_lines
import ida_pro
import idautils
import idc


def extract(address):
    function = ida_funcs.get_func(address)
    if function is None:
        ida_funcs.add_func(address, ida_idaapi.BADADDR)
        function = ida_funcs.get_func(address)
    if function is None:
        return {'address': hex(address), 'function': None}
    try:
        pseudocode = str(ida_hexrays.decompile(function.start_ea))
    except Exception as error:
        pseudocode = '%s: %s' % (type(error).__name__, error)
    return {
        'address': hex(address),
        'function': hex(function.start_ea),
        'end': hex(function.end_ea),
        'disassembly': [
            {'address': hex(ea), 'instruction': ida_lines.tag_remove(
                idc.generate_disasm_line(ea, 0) or '')}
            for ea in idautils.FuncItems(function.start_ea)
        ],
        'pseudocode': pseudocode,
    }


def main():
    root = Path(idc.ARGV[1])
    result = {
        'database': idc.get_idb_path(),
        'functions': [extract(address) for address in (
            0x104EAAAEC, 0x104EA9B50, 0x104CD1F58,
            0x1015F9DCC, 0x1015F9D10, 0x104F20368,
            0x1015FA328, 0x1015FA354, 0x1015FA01C,
            0x1015FA410, 0x1015F8F40, 0x1015FA0D8,
            0x104EAB160, 0x104EAA9B8, 0x104F1D310,
            0x104F1C91C, 0x104EAB61C, 0x104ED03EC,
            0x104E7AEA0, 0x104E78BA4, 0x104CAD090,
            0x104D9ACB8, 0x104D9ABC8, 0x104D99ED0, 0x104D9A8A8,
        )],
    }
    output = root / 'outputs/gamejoin/replication-focus-native.json'
    output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        traceback.print_exc()
        ida_pro.qexit(1)
    ida_pro.qexit(0)
