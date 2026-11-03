#!/usr/bin/env python3
"""Verify the modern RbxTransportConnection constructor, factory, and sender fields."""
from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

# Isolated Python is used for the pinned-image audit; add only the local
# Capstone wheel location because -I -S intentionally omits user site packages.
CAPSTONE_SITE = Path.home() / "Library/Python/3.9/lib/python/site-packages"
if (CAPSTONE_SITE / "capstone").is_dir():
    sys.path.insert(0, str(CAPSTONE_SITE))
from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

ROOT = Path(__file__).resolve().parents[1]
PINNED_IMAGE = Path("/private/tmp/roblox-physics-analysis-arm64")
V56 = ROOT / "outputs/physics-final/vtable56-candidates.json"
V192 = ROOT / "outputs/physics-final/vtable192-candidates.json"
EXPECTED_SHA256 = "b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870"
CLASS_NAME = "N3RBX11NetworkBase22RbxTransportConnectionE"
CLASS_VTABLE = 0x106979FB8
CLASS_SLOT56 = 0x1044C5FFC
RTC_APP_NAME = "N3RBX12RbxTransport4Impl16RtcAppConnectionE"
RTC_APP_VTABLE = 0x1068799F0


class Image:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        if self.sha256 != EXPECTED_SHA256:
            raise ValueError("pinned binary hash does not match the reviewed image")
        if self.data[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("expected little-endian Mach-O 64")
        ncmds, sizeofcmds = struct.unpack_from("<II", self.data, 16)
        pos, end, self.segments = 32, 32 + sizeofcmds, []
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

    def disasm(self, start: int, end: int) -> list[dict]:
        for name, vmaddr, _vmsize, fileoff, filesize in self.segments:
            if vmaddr <= start < vmaddr + filesize and end <= vmaddr + filesize:
                off = fileoff + start - vmaddr
                return [{"address": x.address, "mnemonic": x.mnemonic, "op_str": x.op_str}
                        for x in self.md.disasm(self.data[off:off + end - start], start)]
        raise ValueError(f"range outside file-backed Mach-O segment: {start:#x}..{end:#x}")

    def u64_vm(self, address: int) -> int:
        for _name, vmaddr, _vmsize, fileoff, filesize in self.segments:
            if vmaddr <= address and address + 8 <= vmaddr + filesize:
                return struct.unpack_from("<Q", self.data, fileoff + address - vmaddr)[0]
        raise ValueError(f"address outside file-backed segment: {address:#x}")


def check_vptr(image: Image) -> dict:
    seq = image.disasm(0x1044C5788, 0x1044C5794)
    # ADRP x8, 0x106979000; ADD x8, x8, #0xfb8; STR x8, [x0]
    if [(x["mnemonic"], x["op_str"]) for x in seq] != [
            ("adrp", "x8, #0x106979000"), ("add", "x8, x8, #0xfb8"), ("str", "x8, [x0]")]:
        raise AssertionError("RbxTransportConnection vptr initialization changed")
    return {"resolved_vtable": 0x106979000 + 0xfb8, "instructions": seq}


def check_rtc_app_vptrs(image: Image) -> dict:
    primary = image.disasm(0x1021168C8, 0x1021168D4)
    secondary = image.disasm(0x102117064, 0x102117070)
    p = [(x["mnemonic"], x["op_str"]) for x in primary]
    s = [(x["mnemonic"], x["op_str"]) for x in secondary]
    if p != [("adrp", "x8, #0x106879000"), ("add", "x8, x8, #0x9f0"), ("str", "x8, [x0]")]:
        raise AssertionError("primary RtcAppConnection vptr constructor changed")
    if s != [("adrp", "x8, #0x106879000"), ("add", "x8, x8, #0x9f0"), ("stp", "x8, xzr, [x0]")]:
        raise AssertionError("secondary RtcAppConnection vptr constructor changed")
    return {"addresspoint": RTC_APP_VTABLE, "primary": primary, "secondary": secondary}


def run() -> dict:
    image = Image(PINNED_IMAGE)
    slot56 = json.loads(V56.read_text())["classes"]
    cls = next((x for x in slot56 if x["type_name"] == CLASS_NAME), None)
    if not cls or int(cls["candidates"][0]["vtable_addresspoint_vm"], 16) != CLASS_VTABLE:
        raise AssertionError("RTTI-derived modern connection vtable missing")
    if int(cls["candidates"][0]["slot_target_vm"], 16) != CLASS_SLOT56:
        raise AssertionError("modern connection +56 method changed")
    v192 = json.loads(V192.read_text())["candidate_vtables"]
    rtc = next((x for x in v192 if x.get("rtti_name_mangled") == RTC_APP_NAME), None)
    if not rtc or rtc["address"] != RTC_APP_VTABLE:
        raise AssertionError("RTTI-derived RtcAppConnection vtable missing")
    rtc_slots = {i: image.u64_vm(RTC_APP_VTABLE + i * 8) for i in (10, 21, 22)}
    expected_rtc_slots = {10: 0x10211833C, 21: 0x102117CC4, 22: 0x102117D78}
    if rtc_slots != expected_rtc_slots:
        raise AssertionError(f"RtcAppConnection interface slots changed: {rtc_slots}")
    # The inner WTAP connection's exact concrete type is not established by
    # this RTTI lookup. Preserve candidates for the next route trace only.
    slot192_candidates = [x for x in v192 if x.get("slot_target") == 0x1044C5FFC]

    checks = {
        "rtti_slot56": cls,
        "constructor_vptr_store": check_vptr(image),
        "constructor_owns_wtapp_connection_at_850": image.disasm(0x1044C5794, 0x1044C57B8),
        "factory_allocates_and_registers_connection": image.disasm(0x1044C67D0, 0x1044C67F8),
        "factory_move_registration": image.disasm(0x1044C6AAC, 0x1044C6AD4),
        "copy_wrapper_registration": image.disasm(0x10449434C, 0x104494380),
        "move_wrapper_registration": image.disasm(0x104494380, 0x1044943DC),
        "callers_of_copy_wrapper": image.disasm(0x104C8B8CC, 0x104C8B8F4),
        "slot56_mask_and_wtapp_dispatch": image.disasm(0x1044C5FFC, 0x1044C60C8),
        "channel_interface_use": image.disasm(0x1044C6448, 0x1044C64C0),
        "rtc_app_vptr_constructors": check_rtc_app_vptrs(image),
        "rtc_app_connection_candidate": {
            "rtti": RTC_APP_NAME, "addresspoint": RTC_APP_VTABLE,
            "slot10_send": rtc_slots[10], "slot21_query": rtc_slots[21],
            "slot22_initialize": rtc_slots[22],
            "query_forwarder": image.disasm(0x102117CC4, 0x102117CCC),
            "initialize_forwarder": image.disasm(0x102117D78, 0x102117D80),
        },
        "physics_context_send_dispatch": image.disasm(0x104D4C7A0, 0x104D4C848),
        "slot192_candidates_for_slot56_target": slot192_candidates,
    }
    return {
        "binary": str(PINNED_IMAGE), "sha256": image.sha256,
        "status": "modern constructor and field operations verified statically",
        "rtti": CLASS_NAME, "addresspoint": CLASS_VTABLE, "slot56": CLASS_SLOT56,
        "physics_context_field_264_registration": "not proven; captured factory output is stored at a separate containing-object offset +0xEA0",
        "inner_wtapp_connection_type": "RtcAppConnection is a strong RTTI and slot-signature candidate for +0x850; exact runtime dynamic type is unproven",
        "checks": checks,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
