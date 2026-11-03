#!/usr/bin/env python3
"""Verify selected peer/connection vptr stores and sender callsite in the copied Mach-O."""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = ROOT / "inputs/RobloxPlayer-0.745-arm64"
VTABLE192 = ROOT / "outputs/physics-final/vtable192-candidates.json"
VTABLE56 = ROOT / "outputs/physics-final/vtable56-candidates.json"

TARGETS = {
    "rakpeer": (0x1068C1120, 0x102AAE57C),
    "raknet_connection": (0x1069C5590, 0x104CC31B8),
    "raknet_client_connection": (0x1069C5680, 0x104CC31B8),
}


class MachO:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        if self.data[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("expected little-endian 64-bit Mach-O")
        ncmds, sizeofcmds = struct.unpack_from("<II", self.data, 16)
        pos, self.segments = 32, []
        end = 32 + sizeofcmds
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", self.data, pos)
            if cmd == 0x19:
                name = self.data[pos + 8:pos + 24].split(b"\0", 1)[0].decode("ascii", "replace")
                vmaddr, vmsize, fileoff, filesize = struct.unpack_from("<QQQQ", self.data, pos + 24)
                self.segments.append((name, vmaddr, vmsize, fileoff, filesize))
            pos += size
        if pos != end:
            raise ValueError("invalid Mach-O load commands")
        self.md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

    def disassemble(self, start: int, end: int) -> list[dict]:
        for _name, vmaddr, _vmsize, fileoff, filesize in self.segments:
            if vmaddr <= start < vmaddr + filesize and end <= vmaddr + filesize:
                off = fileoff + (start - vmaddr)
                return [
                    {"address": ins.address, "mnemonic": ins.mnemonic, "op_str": ins.op_str}
                    for ins in self.md.disasm(self.data[off:off + end - start], start)
                ]
        raise ValueError(f"address range outside file-backed segment: {start:#x}..{end:#x}")


def verify_vptr_store(image: MachO, adrp: int, add: int, store: int, expected: int) -> dict:
    lines = image.disassemble(adrp, store + 4)
    text = [(x["address"], x["mnemonic"], x["op_str"]) for x in lines]
    if not any(a == adrp and m == "adrp" for a, m, _ in text):
        raise AssertionError(f"missing ADRP at {adrp:#x}")
    add_line = next((op for a, m, op in text if a == add and m == "add"), None)
    if add_line is None:
        raise AssertionError(f"missing ADD at {add:#x}")
    store_line = next((op for a, m, op in text if a == store and m == "str"), None)
    if store_line is None:
        raise AssertionError(f"missing STR at {store:#x}")
    page_op = next(op for a, m, op in text if a == adrp and m == "adrp")
    page_match = re.search(r"#(0x[0-9a-fA-F]+)", page_op)
    add_match = re.search(r"#(0x[0-9a-fA-F]+|[0-9]+)$", add_line)
    add_regs = [x.strip() for x in add_line.split(",")[:2]]
    store_reg = store_line.split(",", 1)[0].strip()
    if not page_match or not add_match or len(add_regs) != 2:
        raise AssertionError(f"could not resolve ADRP+ADD at {adrp:#x}")
    resolved = int(page_match.group(1), 0) + int(add_match.group(1), 0)
    if resolved != expected or add_regs[0] != add_regs[1] or store_reg != add_regs[0]:
        raise AssertionError(f"vptr store at {store:#x} resolves {resolved:#x}, expected {expected:#x}")
    return {"adrp": adrp, "add": add, "store": store, "expected_vtable": expected,
            "resolved_vtable": resolved,
            "instructions": [{"address": a, "mnemonic": m, "op_str": op} for a, m, op in text]}


def run(image_path: Path = DEFAULT_IMAGE) -> dict:
    image = MachO(image_path)
    candidates = json.loads(VTABLE192.read_text())["candidate_vtables"]
    connection_candidates = json.loads(VTABLE56.read_text())["classes"]
    names = {x.get("rtti_name_mangled"): x for x in candidates}
    checks = {}
    for key, mangled in (("rakpeer", "N6RakNet7RakPeerE"),
                         ("raknet_connection", "N3RBX7Network16RakNetConnectionE"),
                         ("raknet_client_connection", "N3RBX7Network22RakNetClientConnectionE")):
        cand = names.get(mangled)
        if not cand or cand["address"] != TARGETS[key][0]:
            raise AssertionError(f"RTTI candidate mismatch for {key}")
        checks[key] = {"rtti": mangled, "addresspoint": cand["address"],
                       "slot192_target": cand["slot_target"]}
    slot56 = {x["type_name"]: x for x in connection_candidates}
    for key, mangled in (("raknet_connection", "N3RBX7Network16RakNetConnectionE"),
                         ("raknet_client_connection", "N3RBX7Network22RakNetClientConnectionE")):
        cand = slot56[mangled]["candidates"][0]
        if cand["slot_target_vm"] != "0x104cc31b8":
            raise AssertionError(f"unexpected +56 target for {mangled}")
        checks[key]["slot56_target"] = int(cand["slot_target_vm"], 16)

    checks["rakpeer_primary_vptr_init"] = verify_vptr_store(image, 0x102AA9928, 0x102AA992C, 0x102AA9934, 0x1068C1120)
    checks["rakpeer_destructor_vptr_reset"] = verify_vptr_store(image, 0x102AAA354, 0x102AAA358, 0x102AAA35C, 0x1068C1120)
    checks["raknet_connection_vptr_init"] = verify_vptr_store(image, 0x104CC2F04, 0x104CC2F08, 0x104CC2F0C, 0x1069C5590)
    checks["raknet_client_connection_vptr_init"] = verify_vptr_store(image, 0x104CC38D4, 0x104CC38D8, 0x104CC38DC, 0x1069C5680)
    checks["physics_context_fields_callsite"] = image.disassemble(0x104D4C7A0, 0x104D4C848)
    checks["rakpeer_send_method"] = image.disassemble(0x102AAE57C, 0x102AAE6D8)
    checks["raknet_connection_inner_peer_constructor"] = image.disassemble(0x104CC2F4C, 0x104CC2FB0)
    checks["raknet_connection_inner_peer_dispatch"] = image.disassemble(0x104CC3358, 0x104CC33BC)
    return {"binary": str(image_path), "status": "static constructor/callsite instructions verified",
            "context_field_association": "not proven by these constructor stores",
            "checks": checks}


if __name__ == "__main__":
    out = run()
    print(json.dumps(out, indent=2))
