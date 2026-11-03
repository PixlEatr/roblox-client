#!/usr/bin/env python3
"""Bounded synthetic Unicorn check for native matrix->quaternion helper."""
import hashlib
import json
import math
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                 UC_ARM64_REG_X30)
from physics_rotation import native_matrix3x3_to_quaternion

BEGIN = 0x102E865A4
END = 0x102E86744  # thunk after the helper; helper itself RETs before this
BASE = 0x20000000
INPUT = BASE + 0x1000
OUTPUT = BASE + 0x2000
STACK = BASE + 0xE000
RETURN = 0x30000000


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def matrix_from_quaternion(q):
    x, y, z, w = q
    return tuple(f32(v) for v in (
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)))


def main():
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    image = Path(provenance['copy']).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != provenance['sha256']:
        raise RuntimeError('Pinned native binary hash mismatch')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())
    segment = next(s for s in layout['segments'] if s['address'] <= BEGIN and
                   END <= s['address'] + s['file_size'])
    file_off = segment['offset'] + BEGIN - segment['address']
    code = image[file_off:file_off + END - BEGIN]
    if len(code) != END - BEGIN:
        raise RuntimeError('Native helper bytes are not fully file-backed')

    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    code_page = BEGIN & ~0xfff
    uc.mem_map(code_page, 0x1000)
    # Preserve adjacent bytes because execution includes the helper's complete
    # return path and the stop address is the next thunk.
    page_off = segment['offset'] + code_page - segment['address']
    uc.mem_write(code_page, image[page_off:page_off + 0x1000])
    uc.mem_map(BASE, 0x10000)

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
        raw = struct.pack('<9f', *matrix)
        uc.mem_write(INPUT, raw)
        uc.mem_write(OUTPUT, b'\0' * 16)
        for reg, value in ((UC_ARM64_REG_X0, OUTPUT), (UC_ARM64_REG_X1, INPUT),
                           (UC_ARM64_REG_X30, RETURN), (UC_ARM64_REG_SP, STACK)):
            uc.reg_write(reg, value)
        uc.emu_start(BEGIN, RETURN, count=1000)
        if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
            raise RuntimeError(f'{name}: native helper did not return cleanly at {uc.reg_read(UC_ARM64_REG_PC):#x}')
        native = struct.unpack('<4f', uc.mem_read(OUTPUT, 16))
        production = native_matrix3x3_to_quaternion(matrix)
        if any(abs(a-b) > 1e-6 for a, b in zip(native, production)):
            raise AssertionError(f'{name}: native {native!r} != production {production!r}')
        results.append({'name': name, 'matrix_row_major': list(matrix),
                        'native_xyzw': list(native), 'production_xyzw': list(production),
                        'exact_float32_match': native == production})

    report = {
        'status': 'verified', 'evidence_type': 'bounded Unicorn emulation; synthetic matrices only',
        'binary_sha256': digest, 'binary_version': provenance['version'],
        'routine': '0x102E865A4', 'entry_thunk': '0x102E86744',
        'memory_contract': 'input: nine row-major float32 matrix elements; output: x,y,z,w float32',
        'cases': results,
        'unsupported': ['matrix orthogonalization', 'invalid/non-rotation matrix behavior',
                        'mapping from any runtime CFrame memory object to this matrix argument'],
    }
    out = root / 'outputs/physics-final/native-matrix-quaternion.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'validated {len(results)} matrices; report {out}')


if __name__ == '__main__':
    main()
