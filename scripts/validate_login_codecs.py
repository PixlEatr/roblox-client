#!/usr/bin/env python3
"""Compare pure Python codecs against bounded ARM64 emulation, offline only.

Maps only the checksum functions, their constant seed, and synthetic scratch
memory. No client startup, imports, system calls, network, or credentials.
Requires the optional analysis dependency unicorn==2.1.4.
"""
import hashlib
import json
from pathlib import Path
import random

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X30, UC_ARM64_REG_SP

from login_packets import ticket_hash, ticket_checksum


def main():
    root = Path(__file__).resolve().parent.parent
    meta = json.loads((root / "outputs/provenance.json").read_text())
    binary = Path(meta["copy"]).read_bytes()
    if hashlib.sha256(binary).hexdigest() != meta["sha256"]:
        raise ValueError("Binary hash does not match the selected build")
    layout = json.loads((root / "outputs/macho-layout.json").read_text())

    def read(address, size):
        segment = next(s for s in layout["segments"] if s["address"] <= address
                       and address + size <= s["address"] + s["file_size"])
        offset = segment["offset"] + address - segment["address"]
        return binary[offset:offset + size]

    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in (0x104C90000, 0x104F0B000, 0x102ED4000, 0x102ED5000):
        emulator.mem_map(page, 4096, UC_PROT_READ | UC_PROT_EXEC)
        emulator.mem_write(page, read(page, 4096))
    emulator.mem_map(0x106AD3000, 4096, UC_PROT_READ)
    emulator.mem_write(0x106AD34E0, read(0x106AD34E0, 4))
    emulator.mem_map(0x20000000, 65536, UC_PROT_READ | UC_PROT_WRITE)

    def call(address, *args):
        for register, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2), args):
            emulator.reg_write(register, value)
        emulator.reg_write(UC_ARM64_REG_X30, 0x30000000)
        emulator.reg_write(UC_ARM64_REG_SP, 0x20008000)
        emulator.emu_start(address, 0x30000000, timeout=100000, count=20000)
        return emulator.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF

    rng = random.Random(745)
    count_hash = count_permutation = 0
    for size in list(range(34)) + [63, 64, 65, 255, 510, 1024]:
        data = bytes(rng.randrange(256) for _ in range(size))
        emulator.mem_write(0x20001000, data)
        for seed in (0, 1, 0xFFFFFFFF):
            expected = call(0x102ED4F00, 0x20001000, len(data), seed)
            if ticket_hash(data, seed) != expected:
                raise AssertionError("Ticket hash differs from the ARM64 function")
            count_hash += 1
    for value in [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF] + [rng.randrange(1 << 32) for _ in range(100)]:
        if ticket_checksum(value) != call(0x104C901B8, value):
            raise AssertionError("Ticket checksum differs from the ARM64 function")
        count_permutation += 1
    report = {"sha256": meta["sha256"], "offline_only": True, "synthetic_inputs_only": True,
              "emulator": "unicorn 2.1.4", "native_hash_cases_passed": count_hash,
              "native_checksum_cases_passed": count_permutation}
    (root / "outputs/gamejoin/login-codec-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
