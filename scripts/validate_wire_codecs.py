#!/usr/bin/env python3
"""Compare wire primitives with copied ARM64 code using synthetic inputs only.

Only pure varint/string codecs and a bounded byte-write sink are executed.
No native app, authentication material, networking, or platform imports run.
"""
import hashlib
import json
from pathlib import Path
import random
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                UC_ARM64_REG_X2, UC_ARM64_REG_X30,
                                UC_ARM64_REG_PC, UC_ARM64_REG_SP)

from game_wire import encode_string, encode_unsigned, encode_varint
from login_packets import encode_signed


def main():
    root = Path(__file__).resolve().parent.parent
    meta = json.loads((root / 'outputs/provenance.json').read_text())
    binary = Path(meta['copy']).read_bytes()
    if hashlib.sha256(binary).hexdigest() != meta['sha256']:
        raise ValueError('Unexpected copied binary')
    segments = json.loads((root / 'outputs/macho-layout.json').read_text())['segments']

    def native(address, size):
        segment = next(s for s in segments if s['address'] <= address
                       and address + size <= s['address'] + s['file_size'])
        offset = segment['offset'] + address - segment['address']
        return binary[offset:offset + size]

    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in (0x1015F9000, 0x102236000):
        emulator.mem_map(page, 4096)
        emulator.mem_write(page, native(page, 4096))
    emulator.mem_map(0x106A63000, 4096)
    emulator.mem_write(0x106A63B78, b'\1')
    emulator.mem_map(0x20000000, 65536)
    output = bytearray()

    def sink(uc, address, size, data):
        if address == 0x102236904:
            destination = uc.reg_read(UC_ARM64_REG_X0)
            source = uc.reg_read(UC_ARM64_REG_X1)
            count = uc.reg_read(UC_ARM64_REG_X2)
            if count > 8:
                raise ValueError('Unexpected QUIC decoder copy size')
            uc.mem_write(destination, bytes(uc.mem_read(source, count)))
            uc.reg_write(UC_ARM64_REG_PC, address + 4)
        elif address == 0x1015F9230:
            source = uc.reg_read(UC_ARM64_REG_X1)
            count = uc.reg_read(UC_ARM64_REG_X2)
            if count > 8192:
                raise ValueError('Unexpected native byte-write size')
            output.extend(uc.mem_read(source, count))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == 0x102C7C8C8:
            raise AssertionError('Unexpected allocator path')

    emulator.hook_add(UC_HOOK_CODE, sink)

    def call(address, *args):
        output.clear()
        for register, value in zip((UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2), args):
            emulator.reg_write(register, value & ((1 << 64) - 1))
        emulator.reg_write(UC_ARM64_REG_X30, 0x30000000)
        emulator.reg_write(UC_ARM64_REG_SP, 0x20008000)
        emulator.emu_start(address, 0x30000000, timeout=100000, count=20000)
        return bytes(output)

    rng = random.Random(7451137)
    unsigned = [0, 1, 126, 127, 128, 129, (1 << 64) - 1]
    unsigned += [(1 << n) + delta for n in range(7, 63, 7) for delta in (-1, 0, 1)]
    unsigned += [rng.getrandbits(64) for _ in range(256)]
    for value in unsigned:
        assert call(0x1015F9530, value, 0x20002000) == encode_unsigned(value)
    signed = [-(1 << 63), -1, 0, 1, (1 << 63) - 1]
    signed += [rng.randrange(-(1 << 63), 1 << 63) for _ in range(256)]
    for value in signed:
        assert call(0x1015F9524, value, 0x20002000) == encode_signed(value)

    strings = ['', 'synthetic', 'é', '非秘密']
    strings += ['x' * size for size in (22, 23, 24, 126, 127, 128, 129, 255, 1024)]
    for value in strings:
        raw = value.encode('utf-8')
        if len(raw) < 23:
            obj = raw.ljust(23, b'\0') + bytes((len(raw),))
        else:
            obj = struct.pack('<QQQ', 0x20004000, len(raw), (1 << 63) | len(raw))
            emulator.mem_write(0x20004000, raw)
        emulator.mem_write(0x20001000, obj)
        assert call(0x1015F9804, 0x20001000, 0x20002000, 8192) == encode_string(value)

    quic_cases = 0
    for value in [0, 1, 63, 64, 16383, 16384, (1 << 30) - 1, 1 << 30, (1 << 62) - 1]:
        for width in (1, 2, 4, 8):
            if value >= 1 << (8 * width - 2):
                continue
            raw = encode_varint(value, width)
            emulator.mem_write(0x20001000, raw)
            emulator.mem_write(0x20002000, b'\0' * 8)
            call(0x102236878, 0x20001000, len(raw), 0x20002000)
            assert emulator.reg_read(UC_ARM64_REG_X0) == width
            assert struct.unpack('<Q', emulator.mem_read(0x20002000, 8))[0] == value
            quic_cases += 1
    report = {'offline_only': True, 'synthetic_inputs_only': True, 'sha256': meta['sha256'],
              'native_unsigned_cases_passed': len(unsigned), 'native_signed_cases_passed': len(signed),
              'native_string_cases_passed': len(strings), 'native_quic_cases_passed': quic_cases}
    (root / 'outputs/audit').mkdir(exist_ok=True)
    (root / 'outputs/audit/wire-codec-validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
