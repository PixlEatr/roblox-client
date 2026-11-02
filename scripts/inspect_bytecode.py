#!/usr/bin/env python3
"""Statically inspect raw/hex Luau v13/v14 bytecode without loading it into a VM.

This is a bounded format reader and instruction listing, not a source decompiler
or a guarantee that a blob is safe to execute. See inspection.json for limits.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import struct

from opcode_metadata import opcode_metadata

MAX_FILE = 16 * 1024 * 1024
MAX_ITEMS = 1_000_000
JUMP_D = set("JUMP JUMPBACK JUMPIF JUMPIFNOT JUMPIFEQ JUMPIFLE JUMPIFLT JUMPIFNOTEQ JUMPIFNOTLE JUMPIFNOTLT FORNPREP FORNLOOP FORGPREP FORGLOOP FORGPREP_INEXT FORGPREP_NEXT JUMPXEQKNIL JUMPXEQKB JUMPXEQKN JUMPXEQKS CMPPROTO".split())
FASTCALL = set("FASTCALL FASTCALL1 FASTCALL2 FASTCALL2K FASTCALL3 FASTPCALL FINCONSTRUCT".split())
CLASS_OPS = set("NEWCLASS CONSTRUCT FINCONSTRUCT GETPRIVATEFIELD SETPRIVATEFIELD".split())


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data, start=0, end=None):
        self.data = data
        self.pos = start
        self.end = len(data) if end is None else end
        if not 0 <= start <= self.end <= len(data):
            raise FormatError("Invalid reader bounds")

    def fail(self, message):
        raise FormatError(f"At byte {self.pos}: {message}")

    def take(self, size):
        if size < 0 or size > self.end - self.pos:
            self.fail(f"Truncated field of {size} bytes")
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def u8(self):
        return self.take(1)[0]

    def fixed(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def var(self, bits=32):
        result = 0
        for shift in range(0, bits, 7):
            byte = self.u8()
            result |= (byte & 127) << shift
            if result >= 1 << bits:
                self.fail(f"Varint exceeds {bits} bits")
            if not byte & 128:
                return result
        self.fail("Unterminated varint")

    def count(self, minimum_bytes=1):
        result = self.var()
        if result > MAX_ITEMS or result * minimum_bytes > self.end - self.pos:
            self.fail(f"Unreasonable count {result}")
        return result

    def flag(self):
        result = self.u8()
        if result not in (0, 1):
            self.fail("Expected boolean byte")
        return result


def decode_hex(text):
    if len(text) > MAX_FILE * 3:
        raise FormatError("Hex input too large")
    compact = re.sub(r"\s+", "", text)
    if len(compact) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", compact):
        raise FormatError("Input must contain complete hexadecimal byte pairs")
    data = bytes.fromhex(compact)
    if len(data) > MAX_FILE:
        raise FormatError("Bytecode exceeds file size limit")
    return data


def string_ref(reader, strings, optional=False):
    index = reader.var()
    if index > len(strings) or (index == 0 and not optional):
        reader.fail(f"Invalid string reference {index}")
    return index


def parse(data):
    if not data or len(data) > MAX_FILE:
        raise FormatError("Empty or oversized bytecode")
    reader = Reader(data)
    version, type_version = reader.u8(), reader.u8()
    if version not in (13, 14) or type_version not in (1, 2, 3):
        raise FormatError(f"Unsupported bytecode/type version {version}/{type_version}")
    strings = []
    for _ in range(reader.count()):
        value = reader.take(reader.var())
        strings.append({"text": value.decode("utf-8", "backslashreplace"), "hex": value.hex()})
    remapping = []
    if type_version == 3:
        used = set()
        while True:
            index = reader.u8()
            if index == 0:
                break
            if index in used:
                reader.fail("Duplicate userdata mapping")
            used.add(index)
            remapping.append({"index": index, "string": string_ref(reader, strings)})

    prototypes = []
    for pid in range(reader.count()):
        size = reader.var()
        start = reader.pos
        if size > reader.end - start:
            reader.fail("Prototype exceeds file")
        section = Reader(data, start, start + size)
        proto = {"id": pid, "offset": start, "size": size}
        for name in ("stack", "parameters", "upvalues"):
            proto[name] = section.u8()
        proto["vararg"] = section.flag()
        proto["flags"] = section.u8()
        if proto["parameters"] > proto["stack"] or proto["upvalues"] > 200:
            section.fail("Invalid function register/upvalue counts")
        proto["type_info_hex"] = section.take(section.var()).hex()
        code_count = section.count(4)
        if code_count == 0:
            section.fail("Empty instruction array")
        proto["code_offset"] = section.pos
        proto["code"] = [section.fixed("I") for _ in range(code_count)]
        constants = []
        for _ in range(section.count()):
            tag = section.u8()
            constant = {"tag": tag}
            if tag == 0:
                constant.update(kind="nil", value=None)
            elif tag == 1:
                constant.update(kind="boolean", value=bool(section.flag()))
            elif tag == 2:
                raw = section.take(8)
                value = struct.unpack("<d", raw)[0]
                constant.update(kind="number", value=value if math.isfinite(value) else repr(value), bits=raw.hex())
            elif tag == 3:
                constant.update(kind="string", string=string_ref(section, strings))
            elif tag == 4:
                constant.update(kind="import", value=section.fixed("I"))
            elif tag in (5, 8):
                entries = []
                for _ in range(section.count(1 if tag == 5 else 5)):
                    key = section.var()
                    value = section.fixed("i") if tag == 8 else -1
                    if key >= len(constants) or value >= len(constants) or value < -1:
                        section.fail("Table template references unavailable constant")
                    entries.append({"key": key, "value": value})
                constant.update(kind="table", entries=entries)
            elif tag == 6:
                child = section.var()
                if child >= pid:
                    section.fail("Closure constant references unavailable prototype")
                constant.update(kind="closure", proto=child)
            elif tag in (7, 11):
                width, fmt = (4, "f") if tag == 7 else (8, "d")
                raw = section.take(width * 4)
                values = struct.unpack("<4" + fmt, raw)
                constant.update(kind="vector", values=[v if math.isfinite(v) else repr(v) for v in values], bits=raw.hex())
            elif tag == 9:
                negative, magnitude = section.flag(), section.var(64)
                if magnitude > (1 << 63) - (not negative):
                    section.fail("Integer magnitude outside int64")
                constant.update(kind="integer", value=-magnitude if negative else magnitude)
            elif tag == 10:
                section.fail("Class constants are outside this inspector's supported subset")
            else:
                section.fail(f"Unknown constant tag {tag}")
            constants.append(constant)
        proto["constants"] = constants
        children = [section.var() for _ in range(section.count())]
        if any(child >= pid for child in children):
            section.fail("Child references unavailable prototype")
        proto["children"] = children
        proto["line_defined"] = section.var()
        proto["name_string"] = string_ref(section, strings, optional=True)
        proto["name"] = strings[proto["name_string"] - 1]["text"] if proto["name_string"] else None
        proto["lines"] = []
        if section.flag():
            gap = section.u8()
            if gap > 31:
                section.fail("Invalid line interval")
            offsets, running = [], 0
            for delta in section.take(code_count):
                running = (running + delta) & 255
                offsets.append(running)
            baselines, running = [], 0
            for _ in range(((code_count - 1) >> gap) + 1):
                running += section.fixed("i")
                baselines.append(running)
            proto["lines"] = [baselines[pc >> gap] + offsets[pc] for pc in range(code_count)]
        proto["locals"], proto["upvalue_names"] = [], []
        if section.flag():
            for _ in range(section.count(4)):
                name = string_ref(section, strings)
                first, last, register = section.var(), section.var(), section.u8()
                if not 0 <= first <= last <= code_count or register >= proto["stack"]:
                    section.fail("Invalid debug-local lifetime/register")
                proto["locals"].append({"name": strings[name - 1]["text"], "start": first, "end": last, "register": register})
            names_count = section.count()
            if names_count != proto["upvalues"]:
                section.fail("Upvalue name count mismatch")
            proto["upvalue_names"] = [strings[string_ref(section, strings) - 1]["text"] for _ in range(names_count)]
        feedback = []
        for _ in range(section.count(2)):
            kind, value = section.u8(), section.var()
            if kind not in (0, 1) or (kind == 0 and value >= code_count) or (kind == 1 and value >= len(constants)):
                section.fail("Invalid feedback entry")
            feedback.append({"kind": kind, "value": value})
        proto["feedback"] = feedback
        proto["cost"] = section.var(64) if proto["flags"] & 8 else None
        proto["extension_hex"] = section.take(section.end - section.pos).hex()
        prototypes.append(proto)
        reader.pos = section.end
    main = reader.var()
    if main >= len(prototypes):
        reader.fail("Invalid main prototype")
    return {"version": version, "type_version": type_version, "strings": strings,
            "userdata_mapping": remapping, "prototypes": prototypes, "main_proto": main,
            "parsed_bytes": reader.pos, "trailer_hex": data[reader.pos:].hex()}


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def decode_instructions(module, multiplier, catalogue):
    functions = []
    for proto in module["prototypes"]:
        words, instructions, pc = proto["code"], [], 0
        while pc < len(words):
            word = words[pc]
            opcode = ((word & 255) * multiplier) & 255
            if opcode >= len(catalogue):
                raise FormatError(f"P{proto['id']} PC{pc}: unknown opcode {opcode}")
            meta = catalogue[opcode]
            name, length = meta["name"], meta["words"]
            if name in CLASS_OPS or (module["version"] < 14 and name == "FASTPCALL"):
                raise FormatError("Opcode outside version/subset")
            if pc + length > len(words):
                raise FormatError("Missing AUX word")
            a, b, c = (word >> 8) & 255, (word >> 16) & 255, word >> 24
            ins = {"pc": pc, "opcode": opcode, "op": name, "a": a, "b": b, "c": c,
                   "d": signed(word >> 16, 16), "e": signed(word >> 8, 24), "words": length}
            if length == 2:
                ins["aux"] = words[pc + 1]
            if name in JUMP_D:
                ins["target"] = pc + 1 + ins["d"]
            elif name in FASTCALL:
                ins["target"] = pc + c + 2
            elif name == "LOADB" and c:
                ins["target"] = pc + c + 1
            elif name == "JUMPX":
                ins["target"] = pc + 1 + ins["e"]
            instructions.append(ins)
            pc += length
        starts = {ins["pc"] for ins in instructions}
        by_pc = {ins["pc"]: ins for ins in instructions}
        captures = set()
        for ins in instructions:
            name, a, b, c, d, aux = ins["op"], ins["a"], ins["b"], ins["c"], ins["d"], ins.get("aux", 0)
            if "target" in ins and ins["target"] not in starts:
                raise FormatError(f"P{proto['id']} PC{ins['pc']}: branch outside instruction boundaries")
            constant = None
            if name in {"LOADK", "DUPTABLE", "DUPCLOSURE", "GETIMPORT"}:
                constant = d
            elif name in {"GETGLOBAL", "SETGLOBAL", "GETTABLEKS", "SETTABLEKS", "NAMECALL", "LOADKX", "FASTCALL2K"}:
                constant = aux
            elif name in {"GETUDATAKS", "SETUDATAKS", "NAMECALLUDATA"}:
                constant = aux & 65535
            elif name in {"JUMPXEQKN", "JUMPXEQKS"}:
                constant = aux & 0xffffff
            elif name in {"ADDK", "SUBK", "MULK", "DIVK", "MODK", "POWK", "ANDK", "ORK", "IDIVK"}:
                constant = c
            elif name in {"SUBRK", "DIVRK"}:
                constant = b
            if constant is not None:
                if not 0 <= constant < len(proto["constants"]):
                    raise FormatError("Constant operand out of range")
                ins["constant"] = constant
            if name in {"GETUPVAL", "SETUPVAL"} and b >= proto["upvalues"]:
                raise FormatError("Upvalue operand out of range")
            if name == "NEWCLOSURE":
                if not 0 <= d < len(proto["children"]):
                    raise FormatError("Child operand out of range")
                child = proto["children"][d]
            elif name == "DUPCLOSURE":
                value = proto["constants"][d]
                if value["kind"] != "closure":
                    raise FormatError("DUPCLOSURE requires closure constant")
                child = value["proto"]
            else:
                child = None
            if child is not None:
                ins["child_proto"] = child
                for index in range(module["prototypes"][child]["upvalues"]):
                    capture = by_pc.get(ins["pc"] + 1 + index)
                    if not capture or capture["op"] != "CAPTURE" or capture["a"] not in (0, 1, 2):
                        raise FormatError("Invalid closure capture sequence")
                    limit = proto["upvalues"] if capture["a"] == 2 else proto["stack"]
                    if capture["b"] >= limit or (name == "DUPCLOSURE" and capture["a"] == 1):
                        raise FormatError("Invalid capture source")
                    captures.add(capture["pc"])
            if name in FASTCALL:
                call = by_pc.get(ins["target"] - 1)
                if not call or call["op"] != "CALL":
                    raise FormatError("Fastcall fallback must terminate at CALL")
        for ins in instructions:
            if ins.get("target") in captures:
                raise FormatError("Branch into closure capture sequence")
            if ins["op"] == "CAPTURE" and ins["pc"] not in captures:
                raise FormatError("Orphan capture")
        functions.append(instructions)
    return functions


def resolve_encoding(module, requested="auto"):
    catalogue = opcode_metadata()["opcodes"]
    if requested != "auto":
        multiplier = int(requested)
        if not 1 <= multiplier <= 255 or multiplier % 2 == 0:
            raise FormatError("Opcode multiplier must be odd and in 1..255")
        return multiplier, decode_instructions(module, multiplier, catalogue)
    try:
        return 1, decode_instructions(module, 1, catalogue)
    except FormatError:
        pass
    candidates = []
    for multiplier in range(3, 256, 2):
        try:
            candidates.append((multiplier, decode_instructions(module, multiplier, catalogue)))
        except FormatError:
            continue
    if len(candidates) != 1:
        raise FormatError(f"Cannot uniquely infer opcode multiplier; candidates: {[m for m, _ in candidates]}")
    return candidates[0]


def constant_text(module, proto, index, depth=0):
    if depth > 4:
        return f"K{index}"
    value = proto["constants"][index]
    kind = value["kind"]
    if kind == "string":
        return json.dumps(module["strings"][value["string"] - 1]["text"], ensure_ascii=True)
    if kind == "import":
        packed = value["value"]
        ids = [(packed >> shift) & 1023 for shift in (20, 10, 0)][:packed >> 30]
        return "import(" + ", ".join(constant_text(module, proto, i, depth + 1) if i < len(proto["constants"]) else f"invalid K{i}" for i in ids) + ")"
    if kind == "closure":
        return f"closure P{value['proto']}"
    if kind == "table":
        return "table " + json.dumps(value["entries"])
    if kind == "vector":
        return "vector " + repr(value["values"])
    return repr(value.get("value"))


def disassemble(module):
    result = ["Static Luau instruction listing; not recovered source.", "PCs count 32-bit words; AUX words are included in instruction lengths.", ""]
    for proto in module["prototypes"]:
        result.append(f"FUNCTION P{proto['id']} {proto['name']!r} line={proto['line_defined']} params={proto['parameters']} upvalues={proto['upvalues']} stack={proto['stack']}")
        for i in range(len(proto["constants"])):
            result.append(f"  K{i}: {constant_text(module, proto, i)}")
        for ins in proto["instructions"]:
            tail = []
            if "aux" in ins:
                tail.append(f"AUX=0x{ins['aux']:08x}")
            if "target" in ins:
                tail.append(f"target={ins['target']}")
            if "constant" in ins:
                tail.append(f"K{ins['constant']}={constant_text(module, proto, ins['constant'])}")
            if "child_proto" in ins:
                tail.append(f"child=P{ins['child_proto']}")
            line = proto["lines"][ins["pc"]] if proto["lines"] else "?"
            result.append(f"  {ins['pc']:05d} line {str(line):>5}: {ins['op']:<16} A={ins['a']} B={ins['b']} C={ins['c']} D={ins['d']} E={ins['e']} " + " ".join(tail))
        result.append("")
    return "\n".join(result)


def inspect(data, requested="auto"):
    module = parse(data)
    multiplier, functions = resolve_encoding(module, requested)
    for proto, instructions in zip(module["prototypes"], functions):
        proto["instructions"] = instructions
    module["opcode_decode_multiplier"] = multiplier
    counts = collections.Counter(ins["op"] for group in functions for ins in group)
    summary = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "bytecode_version": module["version"], "type_version": module["type_version"],
               "string_count": len(module["strings"]), "prototype_count": len(functions),
               "main_proto": module["main_proto"], "instruction_count": sum(counts.values()),
               "opcode_counts": dict(sorted(counts.items())), "opcode_decode_multiplier": multiplier,
               "parsed_bytes": module["parsed_bytes"], "trailer_bytes": len(module["trailer_hex"]) // 2,
               "named_functions": sum(p["name"] is not None for p in module["prototypes"]),
               "local_name_entries": sum(len(p["locals"]) for p in module["prototypes"]),
               "upvalue_name_entries": sum(len(p["upvalue_names"]) for p in module["prototypes"]),
               "prototype_extension_bytes": sum(len(p["extension_hex"]) // 2 for p in module["prototypes"]),
               "functions": [{k: p[k] for k in ("id", "name", "line_defined", "parameters", "upvalues")} for p in module["prototypes"]],
               "limits": ["Static structural inspection only; no VM loading/execution or source reconstruction.",
                          "Type-info blobs, unknown prototype extensions, and file trailers are preserved but not semantically validated.",
                          "Opcode multiplier inference checks instruction layout, selected references, branches, and captures; it is not a full bytecode verifier."]}
    return module, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--hex", action="store_true", help="Input contains hexadecimal text")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--opcode-multiplier", default="auto")
    args = parser.parse_args()
    if args.input.stat().st_size > MAX_FILE * (3 if args.hex else 1):
        parser.error("Input exceeds size limit")
    try:
        data = decode_hex(args.input.read_text()) if args.hex else args.input.read_bytes()
        module, summary = inspect(data, args.opcode_multiplier)
    except (FormatError, UnicodeError) as error:
        parser.exit(1, f"Inspection failed: {error}\n")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bytecode.luac").write_bytes(data)
    (args.out / "inspection.json").write_text(json.dumps(module, indent=2, allow_nan=False) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    (args.out / "disassembly.txt").write_text(disassemble(module))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("functions", "opcode_counts")}, indent=2))


if __name__ == "__main__":
    main()
