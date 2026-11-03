"""Bounded native ping source extraction in a disposable analysis database."""
import bisect
import json
from pathlib import Path
import traceback

import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_pro
import ida_ua
import idautils
import idc


def main():
    root = Path(idc.ARGV[1])
    out = root / 'outputs/ping-analysis'
    request = json.loads((out / 'native-request.json').read_text())
    starts = json.loads((root / 'outputs/macho-layout.json').read_text())['functions']
    ida_hexrays.init_hexrays_plugin()
    addresses = set(request['functions'])
    refs = []
    for target in request.get('references', []):
        rows = []
        for ref in idautils.XrefsTo(target):
            row = {'from': hex(ref.frm), 'type': ref.type}
            if starts[0] <= ref.frm < starts[-1]:
                address = starts[bisect.bisect_right(starts, ref.frm) - 1]
                row['function'] = hex(address)
                if request.get('include_callers', False):
                    addresses.add(address)
            else:
                row['nearby_qwords'] = [hex(ida_bytes.get_qword(ref.frm + i))
                                        for i in range(-16, 33, 8)]
            rows.append(row)
        refs.append({'target': hex(target), 'references': rows})
    if len(addresses) > 40:
        raise ValueError('bounded extraction exceeds 40 functions')
    functions = []
    for address in sorted(addresses):
        end = starts[bisect.bisect_right(starts, address)]
        row = {'start': hex(address), 'end': hex(end)}
        if end - address > 65536:
            row['skipped'] = 'function_size_limit'
        else:
            if ida_funcs.get_func(address) is None:
                ida_ua.create_insn(address)
                ida_funcs.add_func(address, end)
                ida_auto.plan_and_wait(address, end)
            try:
                function = ida_hexrays.decompile(address)
                if function is not None:
                    (out / ('%x.c' % address)).write_text(str(function) + '\n')
                    row['decompiled'] = True
            except Exception as error:
                row['error'] = str(error)
        functions.append(row)
    (out / 'native-extraction.json').write_text(json.dumps(
        {'functions': functions, 'references': refs}, indent=2) + '\n')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        traceback.print_exc()
        ida_pro.qexit(1)
    ida_pro.qexit(0)
