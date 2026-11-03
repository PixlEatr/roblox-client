#!/usr/bin/env python3
"""Resolve RTTI-named class vtables and report their byte-offset +56 target.

This is static triage for a copied ARM64 Mach-O. It uses the image's dyld
rebase stream to map raw pointers; it does not attach to or modify a process.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from physics_macho_pointers import Image


DEFAULT_TYPES = (
    b"N3RBX7Network16RakNetConnectionE",
    b"N3RBX7Network22RakNetClientConnectionE",
    b"N3RBX11NetworkBase22RbxTransportConnectionE",
    b"N3RBX12RbxTransport8Protocol7ChannelE",
)


def scan(image_path: Path, type_names: tuple[bytes, ...] = DEFAULT_TYPES) -> dict:
    image = Image.parse(image_path.read_bytes())
    data = image.data
    segments = image.segments

    def vm_for_fileoff(off: int) -> int:
        seg = next((s for s in segments if s.fileoff <= off < s.fileoff + s.filesize), None)
        if seg is None:
            raise ValueError(f"file offset {off:#x} is not in a mapped segment")
        return seg.vmaddr + off - seg.fileoff

    def raw_pointer_at_vm(address: int) -> int | None:
        seg = next((s for s in segments if s.vmaddr <= address < s.vmaddr + s.filesize), None)
        if seg is None or address + 8 > seg.vmaddr + seg.filesize:
            return None
        return struct.unpack_from("<Q", data, seg.fileoff + address - seg.vmaddr)[0]

    text = next((s for s in segments if s.name == "__TEXT"), None)
    if text is None:
        raise ValueError("image has no __TEXT segment")
    code_ranges = []
    _, _, _, _, ncmds, _, _, _ = struct.unpack_from("<IiiIIIII", data, 0)
    cmd_off = 32
    code_sections = {"__text", "__stubs", "__stub_helper", "__objc_stubs"}
    for _ in range(ncmds):
        cmd, cmd_size = struct.unpack_from("<II", data, cmd_off)
        if cmd == 0x19 and cmd_size >= 72:
            nsects = struct.unpack_from("<I", data, cmd_off + 64)[0]
            sec_off = cmd_off + 72
            for _ in range(nsects):
                sectname = data[sec_off:sec_off + 16].split(b"\0", 1)[0].decode("ascii", "replace")
                addr, size = struct.unpack_from("<QQ", data, sec_off + 32)
                if sectname in code_sections:
                    code_ranges.append((addr, addr + size))
                sec_off += 80
        cmd_off += cmd_size

    def is_code(address: int | None) -> bool:
        return address is not None and any(lo <= address < hi for lo, hi in code_ranges)
    fixups = image.rebase_fixups()
    by_target: dict[int, list[int]] = {}
    by_location: dict[int, int] = {}
    for fixup in fixups:
        target = struct.unpack_from("<Q", data, fixup.fileoff)[0]
        by_target.setdefault(target, []).append(fixup.fileoff)
        by_location[fixup.vmaddr] = target

    results = []
    for mangled in type_names:
        name_off = data.find(mangled)
        if name_off < 0:
            results.append({"type_name": mangled.decode("ascii"), "found": False})
            continue
        name_vm = vm_for_fileoff(name_off)
        name_fields = by_target.get(name_vm, [])
        candidates = []
        for name_field_off in name_fields:
            # Itanium ABI type_info stores its vptr at +0 and name pointer at +8.
            typeinfo_off = name_field_off - 8
            typeinfo_vm = vm_for_fileoff(typeinfo_off)
            for typeinfo_field_off in by_target.get(typeinfo_vm, []):
                # At a vtable, the type_info pointer is immediately before the
                # address point; virtual slot 7 is address point + 56.
                addresspoint_off = typeinfo_field_off + 8
                addresspoint_vm = vm_for_fileoff(addresspoint_off)
                slot_targets = [raw_pointer_at_vm(addresspoint_vm + 8 * i) for i in range(8)]
                target = raw_pointer_at_vm(addresspoint_vm + 56)
                if target is None:
                    continue
                # Require the target slot and adjacent interface methods to be executable.
                if not is_code(target):
                    continue
                related = [raw_pointer_at_vm(addresspoint_vm + offset) for offset in (80, 128, 168, 176)]
                if not all(is_code(value) for value in related):
                    continue
                candidates.append({
                    "typeinfo_vm": hex(typeinfo_vm),
                    "typeinfo_name_field_fileoff": hex(name_field_off),
                    "vtable_typeinfo_field_fileoff": hex(typeinfo_field_off),
                    "vtable_addresspoint_vm": hex(addresspoint_vm),
                    "slot_index": 7,
                    "slot_offset_bytes": 56,
                    "slot_target_vm": hex(target),
                    "related_slot_targets": {
                        str(offset): (hex(raw_pointer_at_vm(addresspoint_vm + offset))
                                      if raw_pointer_at_vm(addresspoint_vm + offset) is not None else None)
                        for offset in (80, 128, 168, 176, 192)
                    },
                    "first_eight_slots": [hex(t) for t in slot_targets],
                    "evidence": "dyld rebase fixups at RTTI-name, typeinfo reference, and first eight vtable slots",
                })
        results.append({"type_name": mangled.decode("ascii"), "found": True,
                        "type_name_vm": hex(name_vm), "candidates": candidates})
    return {
        "image": str(image_path),
        "static_only": True,
        "rebase_fixup_count": len(fixups),
        "slot_offset_bytes": 56,
        "classes": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "inputs/RobloxPlayer-0.745-arm64")
    parser.add_argument("--type", action="append", dest="types",
                        help="additional exact mangled RTTI name bytes as ASCII")
    args = parser.parse_args()
    names = DEFAULT_TYPES + tuple(x.encode("ascii") for x in (args.types or []))
    print(json.dumps(scan(args.image, names), indent=2))


if __name__ == "__main__":
    main()
