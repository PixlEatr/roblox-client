#!/usr/bin/env python3
"""Find candidate ARM64 C++ vtables whose byte-offset +192 entry targets __text.

Reads LC_DYLD_INFO rebase opcodes from the locally copied Mach-O. This is a
static triage tool: results are candidates, not semantic identification of the
connection class or its virtual method.
"""
from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from pathlib import Path


def uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = data[pos]
        pos += 1
        value |= (b & 0x7f) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ValueError("invalid ULEB128")


def parse_macho(path: Path):
    data = path.read_bytes()
    if data[:4] != b"\xcf\xfa\xed\xfe":
        raise ValueError("expected little-endian 64-bit Mach-O")
    _, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data, 0)
    pos, segments, rebase = 32, [], None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, pos)
        if cmd == 0x19:  # LC_SEGMENT_64
            name = data[pos + 8:pos + 24].split(b"\0", 1)[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", data, pos + 24)
            segments.append((name, vmaddr, vmsize, fileoff, filesize))
        elif cmd in (0x22, 0x80000022):  # LC_DYLD_INFO[_ONLY]
            off, rebase_size = struct.unpack_from("<II", data, pos + 8)
            rebase = (off, rebase_size)
        pos += cmdsize
    if rebase is None:
        raise ValueError("no LC_DYLD_INFO rebase stream")
    return data, segments, data[rebase[0]:rebase[0] + rebase[1]]


def rebase_locations(stream: bytes, segments):
    locs, typ, segidx, offset, p = [], 1, 0, 0, 0
    def one():
        if segidx >= len(segments):
            raise ValueError("rebase segment index out of range")
        locs.append((segments[segidx][1] + offset, segments[segidx][3] + offset))
    while p < len(stream):
        opbyte = stream[p]; p += 1
        op, imm = opbyte & 0xf0, opbyte & 0x0f
        if op == 0x00: break
        if op == 0x10: typ = imm
        elif op == 0x20:
            segidx = imm
            offset, p = uleb(stream, p)
        elif op == 0x30:
            n, p = uleb(stream, p); offset += n
        elif op == 0x40: offset += imm * 8
        elif op == 0x50:
            for _ in range(imm): one(); offset += 8
        elif op == 0x60:
            n, p = uleb(stream, p)
            for _ in range(n): one(); offset += 8
        elif op == 0x70:
            n, p = uleb(stream, p); one(); offset += 8 + n
        elif op == 0x80:
            n, p = uleb(stream, p); skip, p = uleb(stream, p)
            for _ in range(n): one(); offset += 8 + skip
        else:
            raise ValueError(f"unsupported rebase opcode {opbyte:#x}")
    return locs


def scan(path: Path, slot: int = 24, min_entries: int = 28):
    data, segments, stream = parse_macho(path)
    text = next(s for s in segments if s[0] == "__TEXT")
    text_lo, text_hi = text[1], text[1] + text[2]
    locations = {}
    for vmaddr, fileoff in rebase_locations(stream, segments):
        val = struct.unpack_from("<Q", data, fileoff)[0]
        if text_lo <= val < text_hi and val % 4 == 0:
            locations[vmaddr] = val
    # Candidate vtables are runs of pointer-sized rebased code addresses. The
    # slot offset is fixed by the call at 0x104CC0320 (vtable + 0xC0).
    runs = []
    for addr in sorted(locations):
        if addr - 8 not in locations:
            run = [addr]
            nxt = addr + 8
            while nxt in locations:
                run.append(nxt); nxt += 8
            if len(run) >= min_entries:
                runs.append(run)
    candidates = []
    for run in runs:
        target_slot = run[slot] if len(run) > slot else None
        if target_slot:
            candidate = {"address": run[0], "entries": len(run),
                               "slot_index": slot, "slot_offset": slot * 8,
                               "slot_target": locations[target_slot],
                               "first_targets": [locations[a] for a in run[:min(len(run), 4)]],
                               "last_targets": [locations[a] for a in run[-min(len(run), 4):]]}
            # A conventional Itanium C++ vtable address point is preceded by
            # offset-to-top and a typeinfo pointer. Preserve the name only if
            # the pointers fall in this image and the name has a mangled RTTI
            # prefix; a random adjacent run therefore stays explicitly null.
            address = run[0]
            image_base = text[1]
            info_off = address - 8 - image_base
            if 0 <= info_off <= len(data) - 8:
                info = struct.unpack_from("<Q", data, info_off)[0]
                name_ptr_off = info + 8 - image_base
                if 0 <= name_ptr_off <= len(data) - 8:
                    name_ptr = struct.unpack_from("<Q", data, name_ptr_off)[0]
                    name_off = name_ptr - image_base
                    if 0 <= name_off < len(data):
                        raw_name = data[name_off:name_off + 512].split(b"\0", 1)[0]
                        if raw_name.startswith(b"N") and raw_name[1:2].isdigit():
                            candidate["rtti_name_mangled"] = raw_name.decode("ascii", "replace")
            candidates.append(candidate)
    return {"binary": str(path), "text_range": [text_lo, text_hi],
            "rebased_code_pointer_count": len(locations), "candidate_runs": len(runs),
            "candidate_vtables": candidates}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("binary", nargs="?", type=Path,
                    default=Path(__file__).resolve().parents[1] / "inputs/RobloxPlayer-0.745-arm64")
    ap.add_argument("--slot", type=int, default=24)
    ap.add_argument("--min-entries", type=int, default=28)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    result = scan(args.binary, args.slot, args.min_entries)
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
