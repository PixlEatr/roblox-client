#!/usr/bin/env python3
"""Run native matrix->six-byte quaternion serialization on synthetic matrices."""
import hashlib
import json
import math
import struct
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED)
from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X30)
from physics_rotation import native_matrix3x3_to_quaternion, encode_native_quaternion

SERIALIZER = 0x104CBEC4C
MATRIX_CONVERTER_THUNK = 0x102E86744
MATRIX_CONVERTER_BODY = 0x102E865A4
STREAM_WRITE = 0x1015F9230
STACK_CHECK_FAIL = 0x10516510C
BASE = 0x20000000
STREAM = BASE + 0x3000
MATRIX = BASE + 0x1000
STACK = BASE + 0xE000
CANARY = BASE + 0xF000
RETURN = 0x30000000
GUARD_VALUE = 0x71A5C0DE9932B847


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def matrix_from_quaternion(q):
    x, y, z, w = q
    return tuple(f32(v) for v in (
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)))


def native_image_reader(image, layout):
    def read(addr, size):
        seg = next(s for s in layout['segments'] if s['address'] <= addr and
                   addr + size <= s['address'] + s['file_size'])
        off = seg['offset'] + addr - seg['address']
        return image[off:off + size]
    return read


def main():
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    image = Path(provenance['copy']).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != provenance['sha256']:
        raise RuntimeError('Pinned native binary hash mismatch')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())
    image_read = native_image_reader(image, layout)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mapped_pages = set()

    def map_image_page(address):
        page = address & ~0xfff
        if page in mapped_pages:
            return
        seg = next((s for s in layout['segments']
                    if s['address'] <= page and page + 0x1000 <= s['address'] + s['file_size']), None)
        if seg is None:
            raise RuntimeError(f'Unmapped access outside file-backed native segments: {address:#x}')
        uc.mem_map(page, 0x1000)
        uc.mem_write(page, image_read(page, 0x1000))
        mapped_pages.add(page)

    for address in (SERIALIZER, MATRIX_CONVERTER_THUNK, MATRIX_CONVERTER_BODY,
                    STREAM_WRITE, 0x10647C000):
        map_image_page(address)
    uc.mem_map(BASE, 0x10000)
    # This global is initialized at runtime. Seed the stack-protector pointer
    # and value deterministically so the serializer's prologue/epilogue check
    # can execute normally in the bounded harness.
    uc.mem_write(0x10647C7B0, struct.pack('<Q', CANARY))
    uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))

    written = bytearray()
    events = []

    def code_hook(_uc, address, size, _data):
        events.append(address)
        if address == STREAM_WRITE:
            stream = uc.reg_read(UC_ARM64_REG_X0)
            data_ptr = uc.reg_read(UC_ARM64_REG_X1)
            count = uc.reg_read(UC_ARM64_REG_X2)
            if stream != STREAM or count not in (2, 4):
                raise RuntimeError(f'Unexpected native stream write args: {stream:#x}, {data_ptr:#x}, {count}')
            written.extend(uc.mem_read(data_ptr, count))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == STACK_CHECK_FAIL:
            raise RuntimeError('Native serializer stack protector failed')

    def unmapped_hook(_uc, access, address, size, value, _data):
        map_image_page(address)
        return True

    uc.hook_add(UC_HOOK_CODE, code_hook)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped_hook)
    uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, unmapped_hook)

    cases = [
        ('identity', (1,0,0, 0,1,0, 0,0,1)),
        ('yaw_plus_45', matrix_from_quaternion((0, math.sin(math.pi/8), 0, math.cos(math.pi/8)))),
        ('yaw_minus_45', matrix_from_quaternion((0, math.sin(-math.pi/8), 0, math.cos(math.pi/8)))),
        ('yaw_plus_90', matrix_from_quaternion((0, math.sin(math.pi/4), 0, math.cos(math.pi/4)))),
        ('yaw_minus_90', matrix_from_quaternion((0, math.sin(-math.pi/4), 0, math.cos(math.pi/4)))),
        ('yaw_180', matrix_from_quaternion((0, 1, 0, 0))),
        ('pitch_roll_mixed', matrix_from_quaternion((0.31, -0.42, 0.17, 0.837))),
        ('dominant_x', matrix_from_quaternion((0.91, 0.12, -0.20, 0.31))),
        ('dominant_y', matrix_from_quaternion((0.11, 0.93, 0.15, -0.29))),
        ('dominant_z', matrix_from_quaternion((-0.21, 0.16, 0.92, 0.28))),
    ]
    results = []
    for name, matrix in cases:
        uc.mem_write(MATRIX, struct.pack('<9f', *matrix))
        uc.mem_write(STREAM, b'\0' * 16)
        uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))
        written.clear()
        events.clear()
        for reg, value in ((UC_ARM64_REG_X0, STREAM), (UC_ARM64_REG_X1, MATRIX),
                           (UC_ARM64_REG_X30, RETURN), (UC_ARM64_REG_SP, STACK)):
            uc.reg_write(reg, value)
        uc.emu_start(SERIALIZER, RETURN, count=5000)
        if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
            raise RuntimeError(f'{name}: serializer failed to return at {uc.reg_read(UC_ARM64_REG_PC):#x}; events={events[-8:]}')
        native = bytes(written)
        q = native_matrix3x3_to_quaternion(matrix)
        production = encode_native_quaternion(q)
        if MATRIX_CONVERTER_BODY not in events:
            raise AssertionError(f'{name}: native matrix->quaternion body was not executed')
        if events.count(STREAM_WRITE) != 2:
            raise AssertionError(f'{name}: expected native serializer to make two stream writes')
        if len(native) != 6 or native != production:
            raise AssertionError(f'{name}: native={native.hex()} production={production.hex()} q={q!r}')
        results.append({'name': name, 'matrix_row_major': list(matrix),
                        'native_matrix_quaternion_xyzw': list(q),
                        'native_wire_hex': native.hex(),
                        'production_composition_wire_hex': production.hex(),
                        'exact_match': True, 'matrix_converter_body_executed': True,
                        'native_stream_write_calls': events.count(STREAM_WRITE),
                        'native_write_sizes': [2, 4]})

    report = {
        'status': 'verified',
        'evidence_type': 'bounded Unicorn execution; synthetic matrices only',
        'binary_sha256': digest, 'binary_version': provenance['version'],
        'serializer': hex(SERIALIZER), 'matrix_conversion_thunk': hex(MATRIX_CONVERTER_THUNK),
        'matrix_conversion_body': hex(MATRIX_CONVERTER_BODY), 'stream_write_hook': hex(STREAM_WRITE),
        'input_contract': 'X1 points to nine row-major float32 matrix elements; X0 is synthetic stream object',
        'output_contract': 'native serializer emitted two bytes followed by four bytes through native writer calls',
        'runtime_state': 'synthetic stack-protector pointer/value seeded to allow native prologue/epilogue check',
        'cases': results,
        'unsupported': ['runtime CFrame-to-matrix pointer mapping', 'server acceptance or live replication behavior'],
    }
    out = root / 'outputs/physics-final/native-matrix-quaternion-wire.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'validated native matrix-to-wire chain on {len(results)} matrices; report {out}')


if __name__ == '__main__':
    main()
