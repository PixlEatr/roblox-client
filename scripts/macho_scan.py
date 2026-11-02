#!/usr/bin/env python3
"""Map ARM64 Mach-O strings/functions and candidate ADRP+ADD references.

Reference candidates must subsequently be checked in IDA. This script only
reads bytes; it never loads or executes the target image.
"""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import struct


def name(raw):
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


def parse_macho(data):
    magic, cpu, subtype, kind, ncmds, sizeofcmds, flags, reserved = struct.unpack_from("<8I", data)
    if magic != 0xfeedfacf or cpu != 0x100000c or 32 + sizeofcmds > len(data):
        raise ValueError("Expected bounded thin ARM64 Mach-O")
    pos, sections, segments, function_command, libraries = 32, [], [], None, []
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", data, pos)
        if size < 8 or pos + size > 32 + sizeofcmds:
            raise ValueError("Invalid load-command bounds")
        if command == 0x19:
            _, _, rawname, address, vmsize, offset, filesize, maxprot, initprot, count, segflags = struct.unpack_from("<II16sQQQQIIII", data, pos)
            if 72 + count * 80 > size or offset + filesize > len(data):
                raise ValueError("Invalid segment bounds")
            segments.append({"name": name(rawname), "address": address, "size": vmsize, "offset": offset, "file_size": filesize})
            for i in range(count):
                rawsection, rawsegment, address, section_size, offset, align, reloff, nreloc, sectionflags, r1, r2, r3 = struct.unpack_from("<16s16sQQ8I", data, pos + 72 + i * 80)
                sections.append({"name": name(rawsection), "segment": name(rawsegment), "address": address, "size": section_size, "offset": offset, "flags": sectionflags})
        elif command == 0x26:
            function_command = struct.unpack_from("<II", data, pos + 8)
        elif command in (0xc, 0x80000018, 0x8000001f, 0x80000023):
            stringoff = struct.unpack_from("<I", data, pos + 8)[0]
            if stringoff >= size:
                raise ValueError("Invalid dylib name offset")
            libraries.append(name(data[pos + stringoff:pos + size]))
        pos += size
    text_segment = next(s for s in segments if s["name"] == "__TEXT")
    text = next(s for s in sections if s["name"] == "__text")
    if text["offset"] + text["size"] > len(data) or text["size"] % 4:
        raise ValueError("Invalid ARM64 code section")
    if function_command is None:
        raise ValueError("No LC_FUNCTION_STARTS")
    offset, size = function_command
    if offset + size > len(data):
        raise ValueError("Function-start data outside file")
    starts, address, pos, end = [], text_segment["address"], offset, offset + size
    while pos < end:
        delta = 0
        for shift in range(0, 64, 7):
            if pos >= end:
                raise ValueError("Truncated function-start varint")
            value = data[pos]
            pos += 1
            delta |= (value & 127) << shift
            if not value & 128:
                break
        else:
            raise ValueError("Invalid function-start varint")
        if delta == 0:
            break
        address += delta
        if not text["address"] <= address < text["address"] + text["size"] or address % 4:
            raise ValueError("Function address outside code section")
        starts.append(address)
    return {"segments": segments, "sections": sections, "functions": starts, "libraries": libraries, "text": text}


def extract_strings(data, layout):
    result = []
    for section in layout["sections"]:
        if section["name"] not in ("__cstring", "__const", "__oslogstring"):
            continue
        if section["offset"] + section["size"] > len(data):
            continue
        content = data[section["offset"]:section["offset"] + section["size"]]
        for match in re.finditer(rb"[\x20-\x7e]{5,}(?=\x00)", content):
            if len(match.group()) > 4096:
                continue
            result.append({"address": section["address"] + match.start(),
                           "file_offset": section["offset"] + match.start(),
                           "section": section["name"], "text": match.group().decode("ascii")})
    return result


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def references(data, layout, targets):
    text, starts = layout["text"], layout["functions"]
    words = [row[0] for row in struct.iter_unpack("<I", data[text["offset"]:text["offset"] + text["size"]])]
    target_pages = {target & ~4095 for target in targets}
    found = []

    def add(ea, target, method, base_instruction):
        index = bisect.bisect_right(starts, ea) - 1
        if index < 0:
            return
        found.append({"instruction_address": ea, "string_address": target,
                      "function_start": starts[index],
                      "function_end": starts[index + 1] if index + 1 < len(starts) else text["address"] + text["size"],
                      "method": method, "base_instruction": base_instruction})

    for i, word in enumerate(words):
        kind = word & 0x9f000000
        if kind not in (0x90000000, 0x10000000):
            continue
        ea = text["address"] + i * 4
        immediate = signed(((word >> 29) & 3) | (((word >> 5) & 0x7ffff) << 2), 21)
        if kind == 0x10000000:
            target = ea + immediate
            if target in targets:
                add(ea, target, "ADR", ea)
            continue
        page = (ea & ~4095) + (immediate << 12)
        if page not in target_pages:
            continue
        register = word & 31
        # This is candidate discovery. Intervening register writes are not modeled;
        # IDA must independently confirm each resolved data reference.
        for j in range(i + 1, min(i + 9, len(words))):
            following = words[j]
            if following & 0xff000000 == 0x91000000 and (following >> 5) & 31 == register:
                offset = ((following >> 10) & 4095) << (12 if following & (1 << 22) else 0)
                target = page + offset
                if target in targets:
                    add(text["address"] + j * 4, target, "ADRP+ADD candidate", ea)
    # A later ADRP can reuse the same register/page inside the lookahead window.
    # Keep the nearest base instruction for each actual reference site; otherwise
    # one ADD could be reported twice with a stale earlier register definition.
    unique = {}
    for reference in found:
        key = (reference["instruction_address"], reference["string_address"])
        previous = unique.get(key)
        if previous is None or reference["base_instruction"] > previous["base_instruction"]:
            unique[key] = reference
    return list(unique.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--queries", type=Path)
    args = parser.parse_args()
    data = args.binary.read_bytes()
    layout = parse_macho(data)
    strings = extract_strings(data, layout)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "macho-layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    (args.out / "strings.json").write_text(json.dumps(strings, indent=2) + "\n")
    if args.queries:
        queries = json.loads(args.queries.read_text())
        selected = []
        for query in queries:
            matches = [row for row in strings if query["text"] in row["text"]]
            for row in matches:
                selected.append({**row, "category": query["category"], "query": query["text"]})
        refs = references(data, layout, {row["address"] for row in selected})
        for row in selected:
            row["candidate_references"] = [r for r in refs if r["string_address"] == row["address"]]
        result = {"sha256": hashlib.sha256(data).hexdigest(), "marker_results": selected,
                  "candidate_reference_count": len(refs), "unique_candidate_functions": len({r["function_start"] for r in refs}),
                  "candidate_only": True}
        (args.out / "static-evidence.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "marker_results"}, indent=2))
    print(json.dumps({"functions": len(layout["functions"]), "strings": len(strings), "code_bytes": layout["text"]["size"]}))


if __name__ == "__main__":
    main()
