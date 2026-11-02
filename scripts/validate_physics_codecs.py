#!/usr/bin/env python3
"""Validate codecs by bounded native ARM64 emulation on synthetic vectors only."""
import hashlib
import json
import math
from pathlib import Path
import random
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ_UNMAPPED
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC, UC_ARM64_REG_S0)

from physics_wire import position, velocity, f32
from wire_reader import Reader
from world_replication import World


def main():
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    binary = Path(provenance['copy']).read_bytes()
    if hashlib.sha256(binary).hexdigest() != provenance['sha256']: raise ValueError('Binary changed')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())

    def read(address, size):
        seg = next(s for s in layout['segments'] if s['address'] <= address
                   and address + size <= s['address'] + s['file_size'])
        off = seg['offset'] + address - seg['address']
        return binary[off:off + size]

    def branch_target(address):
        word = struct.unpack('<I', read(address, 4))[0]
        imm = word & 0x3ffffff
        if imm & 0x2000000: imm -= 0x4000000
        return address + imm * 4

    hooks = {branch_target(0x104cbe804): 'frexp', branch_target(0x104cbe828): 'ldexp', 0x1015f9230: 'write'}
    ranges = [(0x104cbe7b4, 0x104cbeae0), (0x104cbf25c, 0x104cbf400)]
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mapped = set()

    def map_page(address):
        page = address & ~4095
        if page not in mapped:
            uc.mem_map(page, 4096)
            uc.mem_write(page, read(page, 4096))
            mapped.add(page)

    for lo, hi in ranges:
        for page in range(lo & ~4095, (hi + 4095) & ~4095, 4096): map_page(page)
    for address in hooks: map_page(address)
    uc.mem_map(0x20000000, 65536)
    output = bytearray()

    def bits(v): return struct.unpack('<I', struct.pack('<f', v))[0]
    def float_register(): return struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0]

    def code(_uc, address, size, data):
        name = hooks.get(address)
        if name:
            if name == 'write':
                output.extend(uc.mem_read(uc.reg_read(UC_ARM64_REG_X1), uc.reg_read(UC_ARM64_REG_X2)))
            elif name == 'frexp':
                mantissa, exponent = math.frexp(float_register())
                uc.mem_write(uc.reg_read(UC_ARM64_REG_X0), struct.pack('<i', exponent))
                uc.reg_write(UC_ARM64_REG_S0, bits(mantissa))
            else:
                exponent = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
                if exponent & 0x80000000: exponent -= 1 << 32
                uc.reg_write(UC_ARM64_REG_S0, bits(math.ldexp(float_register(), exponent)))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif not any(lo <= address < hi for lo, hi in ranges):
            raise ValueError('Emulator left the selected pure codec')

    def unmapped(_uc, access, address, size, value, data):
        map_page(address)
        return True

    uc.hook_add(UC_HOOK_CODE, code)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped)

    def call(address, v):
        output.clear()
        uc.mem_write(0x20001000, struct.pack('<fff', *v))
        for reg, value in [(UC_ARM64_REG_X0, 0x20002000), (UC_ARM64_REG_X1, 0x20001000),
                           (UC_ARM64_REG_X30, 0x30000000), (UC_ARM64_REG_SP, 0x2000e000)]:
            uc.reg_write(reg, value)
        uc.emu_start(address, 0x30000000, timeout=100000, count=5000)
        if uc.reg_read(UC_ARM64_REG_PC) != 0x30000000: raise ValueError('Emulation did not return')
        return bytes(output)

    rng = random.Random(745)
    cases = [[0., 0., 0.]] + [[s*v, -s*v, s*v/3] for s in (1,-1)
                            for v in (.0009,.001,1,15.99,16,511.99,512,1023.99,1024,16384,1e6)]
    cases += [[rng.uniform(-1,1) * 2**e for _ in range(3)] for e in range(25) for _ in range(12)]
    count = 0
    for v in cases:
        for address, encoder in [(ranges[0][0], position), (ranges[1][0], velocity)]:
            if call(address, v) != encoder(v):
                raise AssertionError('Native/Python mismatch: %s synthetic case %d' % (encoder.__name__, count))
        decoded = World.compact_position(Reader(position(v)))
        exponent = max(0, math.frexp(max(abs(f32(x)) for x in v))[1])
        scale = 1023 if exponent <= 4 else 65535 if exponent <= 10 else 2097151
        if any(abs(x-f32(y)) > 2**exponent/scale for x,y in zip(decoded,v)):
            raise AssertionError('Position decode error exceeds one quantization step')
        count += 1
    report = {'offline_only': True, 'synthetic_inputs_only': True, 'binary_sha256': provenance['sha256'],
              'position_native_cases_passed': count, 'velocity_native_cases_passed': count,
              'position_roundtrip_cases_passed': count}
    (root/'outputs/gamejoin/physics-codec-validation.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__': main()
