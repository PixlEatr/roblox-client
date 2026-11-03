#!/usr/bin/env python3
"""Bounded ARM64 emulation of selected native physics codecs; synthetic data only."""
import hashlib
import json
import math
import random
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ_UNMAPPED
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC,
                                UC_ARM64_REG_S0)
from physics_rotation import encode_native_quaternion as production_encode_quaternion
from physics_rotation import decode_native_quaternion as production_decode_quaternion
from physics_velocity import encode_native_short_velocity as production_encode_short_velocity


BASE = 0x20000000
STACK = BASE + 0xE000
RETURN = 0x30000000


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def round_away(x):
    return math.trunc(x + (0.5 if x >= 0 else -0.5))


def quaternion_encode(q):
    q = [f32(x) for x in q]
    left = f32(f32(q[0]*q[0]) + f32(q[1]*q[1]))
    right = f32(f32(q[2]*q[2]) + f32(q[3]*q[3]))
    total = f32(left + right)
    inv = f32(1.0 / f32(math.sqrt(total)))
    q = [f32(x * inv) for x in q]
    largest = max(range(4), key=lambda i: abs(q[i]))
    if q[largest] < 0:
        q = [-x for x in q]
    other = [i for i in range(4) if i != largest]
    vals = []
    for i in other:
        scaled = f32(f32(q[i] / f32(0.7071067690849304)) * f32(16383.0))
        adjusted = f32(scaled + f32(0.5 if q[i] >= 0 else -0.5))
        vals.append(math.trunc(adjusted) & 0x7fff)
    first = (largest << 30) | (vals[1] << 15) | vals[2]
    # Native writes the first selected component as a 16-bit word, then
    # packs index and the remaining two components in the following word.
    return struct.pack('<HI', vals[0], first)


def quaternion_decode(data):
    a, b = struct.unpack('<HI', data)
    largest = b >> 30
    codes = [a & 0x7fff, (b >> 15) & 0x7fff, b & 0x7fff]
    # 15-bit two's-complement values are the native signed mantissas.
    vals = [f32(f32(((x ^ 0x4000) - 0x4000) / 16383.0) * f32(0.7071067690849304)) for x in codes]
    q = [0.0] * 4
    it = iter(vals)
    for i in range(4):
        if i != largest:
            q[i] = next(it)
    q[largest] = math.sqrt(max(1.0 - sum(x*x for x in q), 0.0))
    n = math.sqrt(sum(x*x for x in q))
    return [x/n for x in q]


def short_velocity(v):
    v = [f32(x) for x in v]
    m = max(abs(x) for x in v)
    if m < f32(0.001):
        return b'\0'
    e = max(-16, min(15, math.frexp(m)[1]))
    scale = math.ldexp(1.0, e)
    q = [max(-1.0, min(1.0, x / scale)) for x in v]
    x, y, z = [math.trunc(f32(f32(a * 31.0) + (0.5 if a >= 0 else -0.5))) & 0x3f for a in q]
    return bytes([((x >> 4) | (e << 2) | 0x80) & 0xff,
                  ((x << 4) | (y >> 2)) & 0xff,
                  ((z & 0x3f) | (y << 6)) & 0xff])


def normalized_close(a, b, tol):
    # q and -q represent the same orientation.
    return min(max(abs(x-y) for x, y in zip(a, b)),
               max(abs(x+y) for x, y in zip(a, b))) <= tol


def main():
    root = Path(__file__).resolve().parent.parent
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    image = Path(provenance['copy']).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != provenance['sha256']:
        raise RuntimeError('Pinned native binary hash mismatch')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())

    def image_read(addr, size):
        seg = next(s for s in layout['segments'] if s['address'] <= addr
                   and addr + size <= s['address'] + s['file_size'])
        off = seg['offset'] + addr - seg['address']
        return image[off:off + size]

    def bl_target(addr):
        word = struct.unpack('<I', image_read(addr, 4))[0]
        if word >> 26 != 0b100101:
            raise RuntimeError('Expected BL at ' + hex(addr))
        imm = word & 0x3ffffff
        if imm & (1 << 25):
            imm -= 1 << 26
        return addr + imm * 4

    funcs = {
        'orientation_encode': (0x104cbec4c, 0x104cbee10),
        'orientation_decode': (0x104cbee10, 0x104cbef64),
        'short_velocity': (0x104cbf4ac, 0x104cbf634),
    }
    float_helper = {bl_target(0x104cbf514): 'frexp', bl_target(0x104cbf540): 'ldexp'}
    rotate_helpers = {
        0x102e86744: 'quat_in', 0x102e86748: 'quat_out',
        0x1015f9230: 'write', 0x1015fa26c: 'read_u16',
        0x1015f9dcc: 'read_u32',
    }
    all_hooks = {**float_helper, **rotate_helpers}
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mapped = set()

    def map_page(addr):
        page = addr & ~4095
        if page in mapped:
            return
        uc.mem_map(page, 4096)
        try:
            uc.mem_write(page, image_read(page, 4096))
        except StopIteration:
            pass
        mapped.add(page)

    for lo, hi in funcs.values():
        for page in range(lo & ~4095, (hi + 4095) & ~4095, 4096):
            map_page(page)
    for addr in all_hooks:
        map_page(addr)
    uc.mem_map(BASE, 65536)
    write_buf = bytearray()
    input_quat = [0., 0., 0., 1.]
    seen_input = None
    read_words = (0, 0)
    decoded_quat = None
    events = []

    def set_s0(value):
        uc.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', f32(value)))[0])

    def code(_uc, addr, size, _data):
        nonlocal decoded_quat, seen_input
        events.append(addr)
        if addr not in all_hooks:
            if any(lo <= addr < hi for lo, hi in funcs.values()):
                return
            raise RuntimeError('Emulation left selected native codec at ' + hex(addr))
        if addr in float_helper:
            helper = float_helper[addr]
            if helper == 'frexp':
                p = uc.reg_read(UC_ARM64_REG_X0)
                mantissa, exponent = math.frexp(struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0])
                uc.mem_write(p, struct.pack('<i', exponent))
                set_s0(mantissa)
            else:
                e = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
                if e & 0x80000000:
                    e -= 1 << 32
                set_s0(math.ldexp(struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0], e))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
            return
        helper = rotate_helpers.get(addr)
        if helper == 'write':
            ptr, count = uc.reg_read(UC_ARM64_REG_X1), uc.reg_read(UC_ARM64_REG_X2)
            write_buf.extend(uc.mem_read(ptr, count))
        elif helper == 'quat_in':
            uc.mem_write(uc.reg_read(UC_ARM64_REG_X0), struct.pack('<4f', *input_quat))
            seen_input = list(struct.unpack('<4f', uc.mem_read(uc.reg_read(UC_ARM64_REG_X0), 16)))
        elif helper == 'read_u16':
            uc.reg_write(UC_ARM64_REG_X0, read_words[0])
        elif helper == 'read_u32':
            uc.reg_write(UC_ARM64_REG_X0, read_words[1])
        elif helper == 'quat_out':
            ptr = uc.reg_read(UC_ARM64_REG_X0)
            decoded_quat = list(struct.unpack('<4f', uc.mem_read(ptr, 16)))
        else:
            raise RuntimeError('Unexpected helper call at ' + hex(addr))
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def on_unmapped(_uc, access, addr, size, value, _data):
        map_page(addr)
        return True

    uc.hook_add(UC_HOOK_CODE, code)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, on_unmapped)

    def call(addr, x0=0x20001000, x1=0x20002000):
        for reg, val in ((UC_ARM64_REG_X0, x0), (UC_ARM64_REG_X1, x1),
                         (UC_ARM64_REG_X2, 0), (UC_ARM64_REG_X30, RETURN),
                         (UC_ARM64_REG_SP, STACK)):
            uc.reg_write(reg, val)
        uc.emu_start(addr, RETURN, count=12000)
        if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
            raise RuntimeError('Native function did not return: %s at pc=%s events=%s' %
                               (hex(addr), hex(uc.reg_read(UC_ARM64_REG_PC)), [hex(x) for x in events[-8:]]))
        events.clear()

    rng = random.Random(745)
    qcases = [[0., 0., 0., 1.], [1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.]]
    qcases += [[rng.uniform(-1, 1) for _ in range(4)] for _ in range(96)]
    qn = 0
    for q in qcases:
        input_quat = [f32(x) for x in q]
        if max(abs(x) for x in input_quat) < 1e-8:
            continue
        write_buf.clear()
        call(funcs['orientation_encode'][0])
        packed = bytes(write_buf)
        if len(packed) != 6 or packed != quaternion_encode(input_quat):
            raise AssertionError('Native/reference orientation case %d differs: native=%s reference=%s input=%s'
                                 % (qn, packed.hex(), quaternion_encode(input_quat).hex(), input_quat))
        production_packed = production_encode_quaternion(input_quat)
        if packed != production_packed:
            raise AssertionError('Production orientation encoder case %d differs: native=%s production=%s input=%s'
                                 % (qn, packed.hex(), production_packed.hex(), input_quat))
        read_words = (struct.unpack('<H', packed[:2])[0], struct.unpack('<I', packed[2:])[0])
        decoded_quat = None
        call(funcs['orientation_decode'][0])
        expected = quaternion_decode(packed)
        if decoded_quat is None or not normalized_close(decoded_quat, expected, 2e-5):
            raise AssertionError('Native compact orientation decoder disagrees with independent model')
        production_decoded, production_used = production_decode_quaternion(packed)
        if production_used != 6 or not normalized_close(decoded_quat, production_decoded, 2e-5):
            raise AssertionError('Production orientation decoder case %d differs from native output' % qn)
        qnorm = math.sqrt(sum(x*x for x in input_quat))
        normalized = [x/qnorm for x in input_quat]
        if not normalized_close(decoded_quat, normalized, 0.00015):
            raise AssertionError('Compact orientation roundtrip exceeds quantization tolerance')
        qn += 1

    vv = [[0., 0., 0.], [0.0009, -0.0007, 0.0008], [0.001, 0., 0.],
          [1., -2., 3.], [15.99, -15.99, 7.], [16., -16., 8.],
          [32767., -128., 0.5], [1e8, -1e8, 5e7]]
    vv += [[rng.uniform(-1, 1) * 2**e for _ in range(3)] for e in range(-16, 15) for _ in range(8)]
    vn = 0
    for v in vv:
        uc.mem_write(0x20002000, struct.pack('<3f', *v))
        write_buf.clear()
        call(funcs['short_velocity'][0])
        packed = bytes(write_buf)
        if packed != short_velocity(v):
            raise AssertionError('Native short-velocity case %d differs: native=%s model=%s input=%s'
                                 % (vn, packed.hex(), short_velocity(v).hex(), v))
        production_packed = production_encode_short_velocity(v)
        if packed != production_packed:
            raise AssertionError('Production short-velocity case %d differs: native=%s production=%s input=%s'
                                 % (vn, packed.hex(), production_packed.hex(), v))
        if len(packed) not in (1, 3):
            raise AssertionError('Short velocity must encode as one or three bytes')
        vn += 1

    report = {
        'offline_synthetic_only': True,
        'binary_sha256': digest,
        'emulated_ranges': {k: [hex(v[0]), hex(v[1])] for k, v in funcs.items()},
        'orientation_input_helper': hex(0x102e86744),
        'orientation_output_helper': hex(0x102e86748),
        'captured_writer': hex(0x1015f9230),
        'stubbed_dependencies': ['quaternion source/sink', 'stream writer/reader', 'frexpf', 'ldexpf'],
        'compact_orientation_encode_decode_cases_passed': qn,
        'production_compact_orientation_encode_decode_cases_passed': qn,
        'production_orientation_functions': [
            'physics_rotation.encode_native_quaternion',
            'physics_rotation.decode_native_quaternion',
        ],
        'production_orientation_encode_comparison': 'exact six-byte output',
        'production_orientation_decode_comparison': 'component tolerance 2e-5, quaternion sign invariant',
        'short_velocity_encode_cases_passed': vn,
        'production_short_velocity_encode_cases_passed': vn,
        'production_short_velocity_function': 'physics_velocity.encode_native_short_velocity',
        'production_short_velocity_comparison': 'exact one-byte or three-byte output',
        'live_transport_or_server_authority_verified': False,
    }
    out = root / 'outputs/physics-final/native-codecs.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
