"""Export selected physics serializer functions from a copied IDA database.

Run with IDA's idat CLI and an already analyzed research .i64. This script is
read-only with respect to the database and binary: it writes pseudocode,
disassembly, and xref metadata beneath outputs/physics-analysis/.
"""
import json
from pathlib import Path
import traceback

import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_idaapi
import ida_lines
import ida_loader
import ida_pro
import ida_ua
import idautils
import idc


TARGETS = [
    0x104D4AEF8,  # higher-level avatar physics sender
    0x104D4BF90,  # per-record sender wrapper
    0x104D4C9C8,  # timestamp / packet header
    0x104D4CBAC,  # object reference and body gate
    0x104D4C650,  # record forwarding/flush path
    0x104D4BDC0, 0x104D4BF34, 0x104D4C0D0, 0x104D4C32C,
    0x104D4FC48, 0x104D4FCD8, 0x104D50BC4, 0x104D4B8C0,
    0x104DC34AC, 0x104CC0320, 0x104CC06BC,  # body helpers and vtable-dispatch call
    0x104D3A324,  # serializer state/delta gate
    0x104D3A370,  # state/delta accounting
    0x104D4D76C,  # outbound per-root record writer
    0x104D4DBB0,  # root/datagram wrapper
    0x104D4DCEC,  # collection traversal
    0x104D4DE2C,  # outgoing pending body setup
    0x104D4DEB0,  # alternate body writer
    0x104D4E704,  # primary body writer
    0x104D4EF84,  # root transform serializer
    0x104D4F18C,  # transform flags/selector
    0x104D4F2CC,  # position/status serializer
    0x104D4F45C,  # rotation serializer
    0x104D4F5A8,  # companion transform codec
    0x104CBE7B4, 0x104CBEAE0, 0x104CBEC4C, 0x104CBEE10,
    0x104CBF25C, 0x104CBF400, 0x104CBF4AC, 0x104CBF634,
    0x102E8F40C, 0x102E85A34, 0x105140010,  # selector, axis lookup, lazy init
]


def main():
    root = Path(idc.ARGV[1]).resolve()
    out = root / "outputs/physics-analysis"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB),
        "static_only": True,
        "database_saved": False,
        "hexrays_available": bool(ida_hexrays.init_hexrays_plugin()),
        "functions": [],
    }
    for start in TARGETS:
        item = {"start": hex(start), "name": idc.get_name(start)}
        f = ida_funcs.get_func(start)
        # Existing copied databases can contain the mapped image but lack
        # auto-created functions at the selected LC_FUNCTION_STARTS. Recreate
        # only these bounded function boundaries from the prior static export.
        cached_path = root / "outputs/ida" / ("%x.json" % start)
        cached = json.loads(cached_path.read_text()) if cached_path.exists() else {}
        cached_end = cached.get("ida_end") or cached.get("end")
        if f is None and cached_end:
            end = int(cached_end)
            ida_ua.create_insn(start)
            ida_funcs.add_func(start, end)
            ida_auto.plan_and_wait(start, end)
            f = ida_funcs.get_func(start)
        if f is None:
            # For direct call targets without a cached LC_FUNCTION_STARTS
            # boundary, let IDA infer the end from a fresh instruction head.
            ida_ua.create_insn(start)
            ida_funcs.add_func(start, ida_idaapi.BADADDR)
            ida_auto.plan_and_wait(start, start + 0x200)
            f = ida_funcs.get_func(start)
        if f is None or f.start_ea != start:
            item["error"] = "No analyzed function at requested address"
            manifest["functions"].append(item)
            continue
        item["end"] = hex(f.end_ea)
        item["callers"] = [hex(x) for x in idautils.CodeRefsTo(start, False)]
        item["data_xrefs_to"] = [hex(x) for x in idautils.DataRefsTo(start)]
        lines = []
        calls = []
        for ea in idautils.FuncItems(start):
            asm = ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")
            lines.append("%016x  %s" % (ea, asm))
            if idc.print_insn_mnem(ea) in ("BL", "B"):
                for target in idautils.CodeRefsFrom(ea, False):
                    if not start <= target < f.end_ea:
                        calls.append({"at": hex(ea), "target": hex(target), "name": idc.get_name(target)})
        item["calls"] = calls
        (out / ("serializer-%x.asm" % start)).write_text("\n".join(lines) + "\n")
        if manifest["hexrays_available"]:
            try:
                cfunc = ida_hexrays.decompile(start)
                if cfunc is not None:
                    (out / ("serializer-%x.c" % start)).write_text(str(cfunc) + "\n")
                    item["decompiled"] = True
                else:
                    item["decompiled"] = False
            except Exception as e:
                item["decompiled"] = False
                item["decompiler_error"] = str(e)
        manifest["functions"].append(item)
    (out / "serializer-ida.json").write_text(json.dumps(manifest, indent=2) + "\n")
    refs = []
    for address in (0x105FFB9CC, 0x105FFD296, 0x1059EA4E7,
                    0x105B4732E, 0x105B51845, 0x105B5187A):
        item = {"string_address": hex(address), "references": []}
        for ea in idautils.DataRefsTo(address):
            f = ida_funcs.get_func(ea)
            item["references"].append({"instruction": hex(ea),
                                       "function_start": hex(f.start_ea) if f else None,
                                       "function_name": idc.get_func_name(ea),
                                       "text": ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")})
        refs.append(item)
    table_data = []
    for address, size in ((0x105EAA3E3, 61), (0x106DF42F0, 88)):
        raw = ida_bytes.get_bytes(address, size)
        table_data.append({"address": hex(address), "size": size,
                           "bytes_hex": raw.hex() if raw else None,
                           "data_refs_to": [hex(x) for x in idautils.DataRefsTo(address)],
                           "code_refs_to": [hex(x) for x in idautils.CodeRefsTo(address, False)]})
    (out / "serializer-reference-xrefs.json").write_text(json.dumps({"static_only": True,
        "strings": refs, "tables": table_data}, indent=2) + "\n")
    print("Physics serializer exports written: %d functions" % len(manifest["functions"]), flush=True)


try:
    main()
except BaseException:
    traceback.print_exc()
    ida_pro.qexit(1)
ida_pro.qexit(0)
