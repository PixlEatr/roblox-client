"""Bounded static chat analysis in its own disposable IDA database."""
import json
import bisect
from pathlib import Path
import traceback

import ida_bytes
import ida_auto
import ida_funcs
import ida_hexrays
import ida_pro
import ida_ua
import idautils
import idc


def main():
    root = Path(idc.ARGV[1])
    # Optional explicit paths let concurrent investigations use separate
    # requests and output directories with their own disposable databases.
    request_path = (Path(idc.ARGV[2]) if len(idc.ARGV) > 2
                    else root / 'outputs/chat-analysis/request.json')
    out = (Path(idc.ARGV[3]) if len(idc.ARGV) > 3
           else root / 'outputs/chat-analysis')
    request = json.loads(request_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    ida_hexrays.init_hexrays_plugin()
    tables = []
    starts = json.loads((root / 'outputs/macho-layout.json').read_text())['functions']
    for address in request.get('tables', []):
        values = [ida_bytes.get_qword(address + offset) for offset in range(0, 96, 8)]
        tables.append({'address':hex(address),'pointers':list(map(hex,values))})
        for value in values:
            if value in starts:
                request.setdefault('functions', []).append(value)
                request.setdefault('ranges', {})[str(value)] = starts[bisect.bisect_right(starts, value)]
    for address, end in request.get('ranges', {}).items():
        address = int(address)
        if end-address > 65536:
            continue
        ida_ua.create_insn(address)
        ida_funcs.add_func(address, end)
        ida_auto.plan_and_wait(address, end)
    found, functions = [], set(request.get('functions', []))
    for address in request.get('addresses', []):
        refs = []
        for ref in idautils.XrefsTo(address):
            function = ida_funcs.get_func(ref.frm)
            row = {'from': hex(ref.frm), 'type': ref.type}
            if function:
                row['function'] = hex(function.start_ea)
                functions.add(function.start_ea)
            else:
                row['nearby_pointers'] = [hex(ida_bytes.get_qword(ref.frm + offset)) for offset in range(-32, 65, 8)]
            refs.append(row)
        found.append({'address': hex(address), 'refs': refs})
    if len(functions) > 50:
        raise ValueError('Chat extraction exceeds bounded function limit')
    results = []
    for address in sorted(functions):
        function = ida_funcs.get_func(address)
        if not function or function.end_ea-function.start_ea > 65536:
            continue
        row = {'address': hex(address)}
        try:
            output = ida_hexrays.decompile(address)
            if output is not None:
                (out / ('%x.c' % address)).write_text(str(output)+'\n')
                row['decompiled'] = True
        except Exception as error:
            row['error'] = str(error)
        results.append(row)
    (out/'references.json').write_text(json.dumps({'references': found, 'functions': results, 'tables':tables}, indent=2)+'\n')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        traceback.print_exc()
        ida_pro.qexit(1)
    ida_pro.qexit(0)
