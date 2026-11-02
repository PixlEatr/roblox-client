#!/usr/bin/env python3
"""Build reviewable indices and recover simple RTTI names from static vtables."""
import csv
import json
from pathlib import Path
import re
import struct

root = Path(__file__).resolve().parent.parent
layout = json.loads((root / "outputs/macho-layout.json").read_text())
manifest = json.loads((root / "outputs/ida/manifest.json").read_text())
data = (root / "inputs/RobloxPlayer-0.745-arm64").read_bytes()


def read(address, count):
    for segment in layout["segments"]:
        if segment["address"] <= address and address + count <= segment["address"] + segment["file_size"]:
            offset = segment["offset"] + address - segment["address"]
            return data[offset:offset + count]
    return b""


def pointer(address):
    raw = read(address, 8)
    return struct.unpack("<Q", raw)[0] if raw else 0


def simple_name(raw):
    if not raw.startswith("N") or not raw.endswith("E"):
        return raw
    pos, parts = 1, []
    while pos < len(raw) - 1:
        match = re.match(r"\d+", raw[pos:])
        if not match:
            return raw
        pos += len(match.group())
        length = int(match.group())
        parts.append(raw[pos:pos + length])
        pos += length
    return "::".join(parts) if pos == len(raw) - 1 else raw


types = {}
for function in manifest["functions"]:
    start = function["start"]
    path = root / ("outputs/ida/%x.c" % start)
    found = []
    if path.exists():
        for address in set(int(h, 16) for h in re.findall(r"&off_([0-9A-Fa-f]+)", path.read_text())):
            typeinfo = pointer(address - 8)
            name_address = pointer(typeinfo + 8)
            raw = read(name_address, 256).split(b"\0", 1)[0]
            if raw.startswith(b"N3RBX7Network") and raw.endswith(b"E"):
                text = raw.decode("ascii")
                found.append({"vtable": address, "typeinfo": typeinfo, "name_address": name_address,
                              "mangled": text, "name": simple_name(text)})
    types[start] = found
(root / "outputs/rtti-evidence.json").write_text(json.dumps({hex(k): v for k, v in types.items() if v}, indent=2) + "\n")

with (root / "outputs/function-index.csv").open("w", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(["address", "rva", "bytes", "research_name", "confirmed_markers", "observed_RTTI", "pseudocode", "assembly"])
    for f in manifest["functions"]:
        start = f["start"]
        writer.writerow([hex(start), hex(start - 0x100000000), f["end"] - start, f.get("name", ""),
                         sum(m.get("confirmed", False) for m in f["markers"]), "; ".join(t["name"] for t in types[start]),
                         "ida/%x.c" % start, "ida/%x.asm" % start])

# Manually transcribed branch values, checked against the paired IDA pseudocode.
# These are dispatcher arguments, not a claim about live wire IDs or selection.
cases = [
    (9, 14, 0x104f1ebf8), (11, 214, 0x104e78ba4), (13, 119, 0x104e7af84),
    (16, 133, 0x104da323c), (17, 171, 0x104d9b938), (20, 208, 0x104eaa84c),
    (22, 22, 0x104dd5ccc), (24, 13, 0x104eab160), (25, 11, 0x104eaa9b8),
    (26, 218, 0x104eaaaec), (27, 211, 0x104e3a3a0), (29, 165, 0x104eab334),
    (30, 49, 0x104eab498), (31, 20, 0x104f1d310), (32, 249, 0x104f1c91c),
    (36, 236, 0x104eaaf2c), (37, 126, 0x104e7b12c), (39, 109, 0x104eabb10),
    (40, 74, 0x104eab9b4), (41, 128, 0x104eab61c)]
type_source = {0x104e78ba4: 0x104cc539c, 0x104eaa84c: 0x104e7a53c,
               0x104eaaaec: 0x104eaab84, 0x104e3a3a0: 0x104d7f198, 0x104dd5ccc: 0x104d87e84}
with (root / "outputs/replication-dispatch.csv").open("w", newline="") as output:
    writer = csv.writer(output)
    writer.writerow(["case_at_104ca7600", "case_at_104ca715c", "target", "observed_RTTI_names", "RTTI_source_function"])
    for ordered, alternate, target in cases:
        source = type_source.get(target, target)
        writer.writerow([ordered, alternate, hex(target), "; ".join(t["name"] for t in types.get(source, [])), hex(source)])
print(json.dumps({"indexed_functions": len(manifest["functions"]), "paired_dispatch_cases": len(cases),
                  "functions_with_network_RTTI": sum(bool(v) for v in types.values())}))
