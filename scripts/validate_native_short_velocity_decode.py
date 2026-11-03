#!/usr/bin/env python3
"""Compare production short-velocity decode with bounded native ARM64 emulation."""
import hashlib
import json
import math
import random
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ_UNMAPPED
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                UC_ARM64_REG_X30, UC_ARM64_REG_SP,
                                UC_ARM64_REG_PC, UC_ARM64_REG_S0)
from physics_velocity import decode_native_short_velocity, encode_native_short_velocity


BASE = 0x20000000
STACK = BASE + 0xE000
RETURN = 0x30000000
DECODE = 0x104CBF634
DECODE_END = 0x104CBF6E4
READER = 0x1015FA0D8


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def main():
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    image = Path(provenance['copy']).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != provenance['sha256']:
        raise RuntimeError('Pinned native binary hash mismatch')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())

    def image_read(address, size):
        seg = next(s for s in layout['segments'] if s['address'] <= address
                   and address + size <= s['address'] + s['file_size'])
        off = seg['offset'] + address - seg['address']
        return image[off:off + size]

    def branch_target(address):
        word = struct.unpack('<I', image_read(address, 4))[0]
        if word >> 26 != 0b100101:
            raise RuntimeError('Expected native BL at ' + hex(address))
        displacement = word & 0x3ffffff
        if displacement & (1 << 25):
            displacement -= 1 << 26
        return address + displacement * 4

    ldexp = branch_target(0x104CBF690)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mapped = set()

    def map_page(address):
        page = address & ~4095
        if page in mapped:
            return
        uc.mem_map(page, 4096)
        try:
            uc.mem_write(page, image_read(page, 4096))
        except StopIteration:
            pass
        mapped.add(page)

    for address in (DECODE, READER, ldexp):
        map_page(address)
    uc.mem_map(BASE, 65536)
    input_bytes = b''
    cursor = 0
    outputs = []
    events = []

    def set_s0(value):
        uc.reg_write(UC_ARM64_REG_S0,
                     struct.unpack('<I', struct.pack('<f', f32(value)))[0])

    def code(_uc, address, size, _data):
        nonlocal cursor
        events.append(address)
        if address == READER:
            if cursor >= len(input_bytes):
                raise RuntimeError('Native reader requested beyond supplied short vector')
            uc.reg_write(UC_ARM64_REG_X0, input_bytes[cursor])
            cursor += 1
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == ldexp:
            exponent = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            if exponent & 0x80000000:
                exponent -= 1 << 32
            set_s0(math.ldexp(1.0, exponent))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif DECODE <= address < DECODE_END:
            return
        else:
            raise RuntimeError('Emulation left selected native decoder at ' + hex(address))

    def unmapped_read(_uc, access, address, size, value, _data):
        map_page(address)
        return True

    uc.hook_add(UC_HOOK_CODE, code)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped_read)

    def decode_native(raw):
        nonlocal cursor, input_bytes
        input_bytes = raw
        cursor = 0
        uc.mem_write(0x20002000, bytes(12))
        for reg, value in ((UC_ARM64_REG_X0, 0x20001000),
                           (UC_ARM64_REG_X1, 0x20002000),
                           (UC_ARM64_REG_X30, RETURN),
                           (UC_ARM64_REG_SP, STACK)):
            uc.reg_write(reg, value)
        uc.emu_start(DECODE, RETURN, count=4000)
        if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
            raise RuntimeError('Native decoder did not return; final PCs: ' +
                               ','.join(hex(x) for x in events[-8:]))
        events.clear()
        if cursor != len(raw):
            raise AssertionError('Native decoder consumed %d of %d input bytes' % (cursor, len(raw)))
        return struct.unpack('<3f', uc.mem_read(0x20002000, 12))

    rng = random.Random(634)
    vectors = [
        (0.0, 0.0, 0.0),
        (0.0009, -0.0007, 0.0008),
        (0.001, 0.0, 0.0),
        (-0.001, 0.0, 0.0),
        (1.0, -2.0, 3.0),
        (15.99, -15.99, 7.0),
        (16.0, -16.0, 8.0),
        (32767.0, -128.0, 0.5),
        (1e8, -1e8, 5e7),
    ]
    vectors += [tuple(rng.uniform(-1.0, 1.0) * 2**e for _ in range(3))
                for e in range(-16, 15) for _ in range(8)]
    for index, source in enumerate(vectors):
        packed = encode_native_short_velocity(source)
        native = decode_native(packed)
        production = decode_native_short_velocity(packed)
        if production.packed != packed or production.zero_sentinel != (packed == b'\0'):
            raise AssertionError('Production decoder metadata mismatch at case %d' % index)
        if production.vector != native:
            raise AssertionError('Production/native decoded floats differ at case %d: %r vs %r'
                                 % (index, production.vector, native))

    report = {
        'synthetic_only': True,
        'binary_sha256': digest,
        'native_decoder': hex(DECODE),
        'native_reader_stub': hex(READER),
        'native_ldexpf_stub': hex(ldexp),
        'production_decoder': 'physics_velocity.decode_native_short_velocity',
        'native_decoder_cases_passed': len(vectors),
        'production_decoder_exact_float_cases_passed': len(vectors),
        'all_input_bytes_consumed': True,
    }
    out = root / 'outputs/physics-final/native-short-velocity-decode.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
