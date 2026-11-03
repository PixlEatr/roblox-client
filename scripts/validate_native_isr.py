#!/usr/bin/env python3
"""Offline native ISR PV-reader check against isolated captured body fields."""
import argparse
import hashlib
import json
import math
import random
import struct
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED,
                     UC_HOOK_MEM_WRITE_UNMAPPED)
from unicorn.arm64_const import (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC,
                                 UC_ARM64_REG_S0)
from physics_macho_pointers import Image
from physics_rotation_callscan import EXPECTED_SHA256
from isr_replication import decode_short_quaternion, read_legacy_pv, _matrix
from isr_sender import encode_pv
from wire_reader import Reader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = args.image.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED_SHA256:
        raise ValueError('Pinned image mismatch')
    image = Image.parse(data)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    base, reader, wire, out, stack, canary, end = (
        0x20000000, 0x20004000, 0x20005000, 0x20006000, 0x2000e000, 0x2000f000, 0x30000000)
    uc.mem_map(base, 0x10000)
    mapped = set()
    def map_page(address):
        page = address & ~4095
        if page in mapped:
            return
        segment = next(s for s in image.segments
                       if s.vmaddr <= page and page+4096 <= s.vmaddr+s.filesize)
        uc.mem_map(page, 4096)
        offset = segment.fileoff+page-segment.vmaddr
        uc.mem_write(page, data[offset:offset+4096])
        mapped.add(page)
    def unmapped(_uc, access, address, size, value, userdata):
        map_page(address)
        return True
    native_quaternions = []
    short_only = False
    def hook(_uc, address, size, userdata):
        if address == 0x105165D60:  # memcpy host boundary
            dst, src, count = (uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
            if count > 4096:
                raise ValueError('Native copy exceeds field bound')
            uc.mem_write(dst, bytes(uc.mem_read(src, count)))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == 0x105165BBC:  # ldexpf host boundary
            exponent = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            if exponent & 0x80000000:
                exponent -= 1 << 32
            value = struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0]
            bits = struct.unpack('<I', struct.pack('<f', math.ldexp(value, exponent)))[0]
            uc.reg_write(UC_ARM64_REG_S0, bits)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == 0x102E86748:
            native_quaternions.append(struct.unpack('<4f', uc.mem_read(uc.reg_read(UC_ARM64_REG_X0), 16)))
            if short_only:
                uc.emu_stop()  # Compare the native decoded quaternion before matrix conversion.
        elif address in (0x105164FB0, 0x10516510C):
            raise ValueError('Native bounds/stack guard failed')
    for kind in (UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED):
        uc.hook_add(kind, unmapped)
    uc.hook_add(UC_HOOK_CODE, hook)
    map_page(0x10647C7B0)
    uc.mem_write(0x10647C7B0, struct.pack('<Q', canary))
    uc.mem_write(canary, struct.pack('<Q', 0x91e1cda5c0deface))
    def run(address, raw):
        native_quaternions.clear()
        uc.mem_write(reader, bytes(64))
        uc.mem_write(reader+16, struct.pack('<Q', wire))
        uc.mem_write(reader+48, struct.pack('<I', len(raw)))
        uc.mem_write(wire, raw)
        uc.mem_write(out, bytes(256))
        for reg, value in ((UC_ARM64_REG_X0, reader), (UC_ARM64_REG_X1, out),
                           (UC_ARM64_REG_X2, 0), (UC_ARM64_REG_X30, end), (UC_ARM64_REG_SP, stack)):
            uc.reg_write(reg, value)
        uc.emu_start(address, end, count=12000)
        if not short_only and uc.reg_read(UC_ARM64_REG_PC) != end:
            raise ValueError('Native PV reader did not return')
        cursor = struct.unpack('<I', uc.mem_read(reader+56, 4))[0]
        if cursor != len(raw):
            raise ValueError('Native field did not consume full body')
    rng = random.Random(0x13c)
    short_only = True
    quaternion_cases = [i << 30 for i in range(4)] + [rng.getrandbits(32) for _ in range(512)]
    for word in quaternion_cases:
        raw = struct.pack('<I', word)
        run(0x104CBF13C, raw)
        if native_quaternions != [decode_short_quaternion(raw)]:
            raise AssertionError('Native short quaternion mismatch: '+hex(word))
    short_only = False
    bodies = [
        '00452ed1cd4a5261ac7d4c07d3c030fe5f7bf53806803e81',
        '006127c8d8b9573d1d5e8100b2016b419eae89fdae3f',
        '0060a700730dab0ade19d34260bd9cfb01903fa5',
        '0060a700730dab0ade19d34260bd9cfb01903fa5',
    ]
    cases = []
    for lod, (address, hex_body) in enumerate(zip((0x104F213A4, 0x104F2189C, 0x104F219F8, 0x104F21B54), bodies)):
        raw = bytes.fromhex(hex_body)
        run(address, raw)
        r = Reader(raw)
        production = read_legacy_pv(r, lod)
        r.finish()
        native_position = struct.unpack('<3f', uc.mem_read(out, 12))
        native_velocities = struct.unpack('<6f', uc.mem_read(out+40, 24))
        expected = production['position'] + sum(production['decoded_velocities'], [])
        if not all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-4)
                   for a, b in zip(native_position+native_velocities, expected)):
            raise AssertionError('Native PV position/velocity mismatch for LOD '+str(lod))
        if not native_quaternions or not all(abs(a-b) < 1e-5
                for a,b in zip(_matrix(native_quaternions[-1]), production['rotation'])):
            raise AssertionError('Native PV quaternion mismatch for LOD '+str(lod))
        cases.append({'lod': lod, 'native_reader': hex(address), 'bytes_consumed': len(raw),
                      'position_velocity_rotation_match': True})
    authored_cases = []
    for index in range(64):
        yaw = rng.uniform(-math.pi, math.pi)
        c, s = math.cos(yaw), math.sin(yaw)
        position = tuple(rng.uniform(-500, 500) for _ in range(3))
        linear = tuple(rng.uniform(-30, 30) for _ in range(3))
        angular = (0., 0., 0.) if index % 2 else tuple(rng.uniform(-3, 3) for _ in range(3))
        raw = encode_pv(position, (c,0.,s,0.,1.,0.,-s,0.,c), linear, angular)
        run(0x104F213A4, raw)
        r = Reader(raw)
        decoded = read_legacy_pv(r, 0)
        r.finish()
        native_values = struct.unpack('<3f', uc.mem_read(out, 12)) + struct.unpack('<6f', uc.mem_read(out+40, 24))
        expected = decoded['position'] + sum(decoded['decoded_velocities'], [])
        if not all(math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-4)
                   for a,b in zip(native_values, expected)):
            raise AssertionError('Authored ISR PV differs from native decode')
        if not native_quaternions or not all(abs(a-b) < 1e-5
                for a,b in zip(_matrix(native_quaternions[-1]), decoded['rotation'])):
            raise AssertionError('Authored ISR rotation differs from native decode')
        if not all(abs(a-b) < .02 for a,b in zip(decoded['position'],position)):
            raise AssertionError('Authored ISR position round trip exceeded quantization bound')
        if not all(abs(a-b) < .02 for a,b in zip(decoded['decoded_velocities'][0],linear)):
            raise AssertionError('Authored ISR velocity round trip exceeded quantization bound')
        if not all(abs(a-b) < .001 for a,b in zip(decoded['rotation'],(c,0.,s,0.,1.,0.,-s,0.,c))):
            raise AssertionError('Authored ISR rotation round trip exceeded quantization bound')
        authored_cases.append({'case':index,'bytes_consumed':len(raw),'native_decode_matches':True})
    report = {'image_sha256': EXPECTED_SHA256,
              'image_provenance': 'Installed ad-hoc-signed image containing macsploit dylib; not pristine.',
              'offline_only': True, 'short_quaternion_exact_float_cases': len(quaternion_cases),
              'pv_cases': cases, 'authored_pv_cases':authored_cases,
              'host_boundaries': ['memcpy', 'ldexpf'],
              'server_acceptance_verified': False}
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
