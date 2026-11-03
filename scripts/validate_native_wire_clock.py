"""Replay pinned native arithmetic with synthetic inputs; no live connection.

Runs the capability-intersection block and the fallback clock's subtraction,
integer conversion and multiplication blocks without replacing instructions.
The clock scale helper is outside the arithmetic blocks: its result is an
explicit fixture input, not an executed OS query or a runtime clock sample.
"""
import argparse
import json
import random
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
from unicorn.arm64_const import (UC_CPU_ARM64_MAX, UC_ARM64_REG_X0,
    UC_ARM64_REG_X19, UC_ARM64_REG_X20, UC_ARM64_REG_X21, UC_ARM64_REG_D0)
from physics_rotation_callscan import parse_macho, EXPECTED_SHA256


def validate(image_path):
    image = parse_macho(image_path.read_bytes(), expected_sha256=EXPECTED_SHA256)
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.ctl_set_cpu_model(UC_CPU_ARM64_MAX)
    for page in (0x10212a000, 0x102c92000):
        off = image.text_offset + page - image.text_address
        uc.mem_map(page, 0x1000)
        uc.mem_write(page, image.data[off:off+0x1000])
    uc.mem_map(0x20000000, 0x2000)
    rng = random.Random(312)
    masks = [(0, 0), (2**64-1, 0), (0, 2**64-1), (2**64-1, 2**64-1),
             (0x100000, 0x022dd3e750bb1892), (1<<25, 1<<25), (1<<25, 0)]
    masks += [(rng.getrandbits(64), rng.getrandbits(64)) for _ in range(100)]
    for local, remote in masks:
        uc.mem_write(0x200000c0, struct.pack('<QQ', local, 0xdeadbeef))
        uc.mem_write(0x20001000, struct.pack('<Q', remote))
        uc.reg_write(UC_ARM64_REG_X20, 0x20000000)
        uc.reg_write(UC_ARM64_REG_X21, 0x20001000)
        uc.emu_start(0x10212aab0, 0x10212aacc, count=20)
        actual = struct.unpack('<Q', uc.mem_read(0x200000c8, 8))[0]
        assert actual == local & remote, (local, remote, actual)

    ticks = [(900_000_000_000, 900_030_000_000, 1e-9),
             (900_000_000_000, 900_030_000_000, 1/24_000_000),
             (12345, 12345, 1/24_000_000)]
    ticks += [(rng.randrange(10**10, 10**12), rng.randrange(10**12, 10**13),
               1/24_000_000) for _ in range(100)]
    for origin, sample, scale in ticks:
        uc.reg_write(UC_ARM64_REG_X19, sample)
        uc.reg_write(UC_ARM64_REG_X0, origin)
        uc.emu_start(0x102c92480, 0x102c92488, count=4)
        uc.reg_write(UC_ARM64_REG_D0, struct.unpack('<Q', struct.pack('<d', scale))[0])
        uc.emu_start(0x102c9248c, 0x102c92490, count=2)
        actual = struct.unpack('<d', struct.pack('<Q', uc.reg_read(UC_ARM64_REG_D0)))[0]
        assert actual == float(sample-origin)*scale, (origin, sample, scale, actual)
    return {
        'image_sha256': image.sha256,
        'native_capability_intersection_cases': len(masks),
        'native_relative_clock_arithmetic_cases': len(ticks),
        'native_instruction_blocks': [['0x10212aab0', '0x10212aacc'],
            ['0x102c92480', '0x102c92488'], ['0x102c9248c', '0x102c92490']],
        'native_instructions_modified': False,
        'helper_hooks': [],
        'limitations': ['Synthetic register and memory inputs.',
            'OS clock and scale helper calls are outside the executed blocks.',
            'No live physics or server acceptance proof.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
