"""Run with IDA's -S switch. Static, bounded analysis of selected functions."""
import json
from pathlib import Path
import time
import traceback

import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_lines
import ida_loader
import ida_name
import ida_pro
import ida_ua
import idautils
import idc


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    root = Path(idc.ARGV[1])
    evidence = json.loads((root / "outputs/static-evidence.json").read_text())
    out = root / "outputs/ida"
    out.mkdir(parents=True, exist_ok=True)
    previous_manifest = out / "manifest.json"
    cache_allowed = previous_manifest.exists() and json.loads(previous_manifest.read_text()).get("sha256") == evidence["sha256"]
    selected = {}
    for marker in evidence["marker_results"]:
        for ref in marker["candidate_references"]:
            entry = selected.setdefault(ref["function_start"], {"start": ref["function_start"], "end": ref["function_end"], "markers": []})
            entry["markers"].append({"string_address": marker["address"], "instruction_address": ref["instruction_address"], "base_instruction": ref["base_instruction"], "text": marker["text"], "category": marker["category"]})
    # Optional additional function ranges for a bounded follow-up pass.
    extra = root / "scripts/followup-functions.json"
    if extra.exists():
        for entry in json.loads(extra.read_text()):
            selected.setdefault(entry["start"], {**entry, "markers": []})
    manifest = {"sha256": evidence["sha256"], "hexrays_available": bool(ida_hexrays.init_hexrays_plugin()),
                "database": ida_loader.get_path(ida_loader.PATH_TYPE_IDB), "functions": [], "static_only": True}
    write_json(out / "manifest.json", manifest)
    for index, (start, entry) in enumerate(sorted(selected.items()), 1):
        then = time.monotonic()
        result = dict(entry)
        result["address_hex"] = hex(start)
        cached_path = out / ("%x.json" % start)
        cached = json.loads(cached_path.read_text()) if cache_allowed and cached_path.exists() else {}
        # A rebuilt IDB need not reanalyze already exported, hash-matched
        # evidence. Cached files retain their original provenance and labels.
        if cached.get('analysis_completed') and cached.get('decompiled') and (out / ("%x.c" % start)).exists():
            manifest['functions'].append({k: v for k, v in cached.items()
                                          if k not in ('direct_calls_and_tail_branches', 'referenced_strings', 'traceback')})
            continue
        print("RESEARCH %d/%d %s (%d bytes)" % (index, len(selected), hex(start), entry["end"] - start), flush=True)
        try:
            if entry["end"] - start > 65536:
                raise ValueError("Function exceeds 64 KiB bounded-analysis limit")
            ida_ua.create_insn(start)
            function = ida_funcs.get_func(start)
            if function is None:
                ida_funcs.add_func(start, entry["end"])
            if cached.get("analysis_completed") and cached.get("decompiled") and function is not None and function.start_ea == start:
                result["analysis_completed"] = True
                result["reused_analysis"] = True
            else:
                result["analysis_completed"] = bool(ida_auto.plan_and_wait(start, entry["end"]))
            function = ida_funcs.get_func(start)
            if function is None or function.start_ea != start:
                raise ValueError("IDA function start does not match LC_FUNCTION_STARTS")
            result["ida_end"] = function.end_ea
            label = entry.get("label", entry["markers"][0]["category"] if entry["markers"] else "followup")
            inferred_name = "re_%s_%x" % (label, start)
            if idc.get_func_name(start).startswith("sub_"):
                ida_name.set_name(start, inferred_name, ida_name.SN_CHECK)
            result["name"] = idc.get_func_name(start)
            ida_funcs.set_func_cmt(function, "Static research label, not an original symbol. Source: ARM64 LC_FUNCTION_STARTS and the marker references saved in outputs/ida. No runtime reachability established.", True)
            for marker in result["markers"]:
                refs = set(idautils.DataRefsTo(marker["string_address"]))
                # ARM64's ADRP+ADD is frequently represented by one ADRL macro.
                # IDA attaches the data xref to the macro head, not its ADD tail.
                expected = {marker["instruction_address"], marker["base_instruction"], ida_bytes.get_item_head(marker["instruction_address"])}
                marker["ida_reference_addresses"] = sorted(refs & expected)
                marker["confirmed"] = bool(marker["ida_reference_addresses"])
            instructions, calls, strings = [], [], []
            for ea in idautils.FuncItems(start):
                line = ida_lines.tag_remove(idc.generate_disasm_line(ea, 0) or "")
                instructions.append("%016x  %s" % (ea, line))
                if idc.print_insn_mnem(ea) in ("BL", "B"):
                    for target in idautils.CodeRefsFrom(ea, False):
                        if not start <= target < function.end_ea:
                            calls.append({"instruction": ea, "target": target, "name": idc.get_name(target)})
                for target in idautils.DataRefsFrom(ea):
                    value = idc.get_strlit_contents(target)
                    if value and len(value) <= 4096:
                        strings.append({"instruction": ea, "target": target, "text": value.decode("utf-8", "replace")})
            result["direct_calls_and_tail_branches"] = calls
            result["referenced_strings"] = strings
            result["code_references_to_start"] = list(idautils.CodeRefsTo(start, False))
            (out / ("%x.asm" % start)).write_text("\n".join(instructions) + "\n")
            if cached.get("decompiled") and (out / ("%x.c" % start)).exists():
                result["decompiled"] = True
                result["reused_pseudocode"] = True
            elif manifest["hexrays_available"]:
                try:
                    cfunc = ida_hexrays.decompile(start)
                    if cfunc is None:
                        raise ValueError("Hex-Rays returned no pseudocode")
                    (out / ("%x.c" % start)).write_text("// Hex-Rays pseudocode; inferred types/names require verification.\n" + str(cfunc) + "\n")
                    result["decompiled"] = True
                except Exception as error:
                    result["decompiled"] = False
                    result["decompiler_error"] = str(error)
        except Exception as error:
            result["error"] = str(error)
            result["traceback"] = traceback.format_exc()
        result["seconds"] = round(time.monotonic() - then, 3)
        write_json(out / ("%x.json" % start), result)
        manifest["functions"].append({k: v for k, v in result.items() if k not in ("direct_calls_and_tail_branches", "referenced_strings", "traceback")})
        write_json(out / "manifest.json", manifest)
    manifest["database_saved"] = bool(ida_loader.save_database(ida_loader.get_path(ida_loader.PATH_TYPE_IDB), ida_loader.DBFL_COMP))
    write_json(out / "manifest.json", manifest)
    print("RESEARCH COMPLETE: %d functions; Hex-Rays=%s" % (len(selected), manifest["hexrays_available"]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        traceback.print_exc()
        ida_pro.qexit(1)
    ida_pro.qexit(0)
