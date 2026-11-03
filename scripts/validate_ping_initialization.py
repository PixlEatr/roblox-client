"""Check ping-field constructor stores in bounded ARM64 emulation, offline.

Only two initialization blocks and their read-only constants are mapped.
This checks initial values, not the transport's subsequent writes or server
acceptance. No app startup, authentication data, host imports or sockets.
"""
import bisect
import hashlib
import json
from pathlib import Path
import struct

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X19, UC_ARM64_REG_SP, UC_ARM64_REG_PC


def main():
    root = Path(__file__).resolve().parents[1]
    binary = (root / 'inputs/RobloxPlayer-0.745-arm64').read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    if digest != 'b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870':
        raise ValueError('unsupported native image')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())

    def read(address, size):
        segment = next(s for s in layout['segments'] if s['address'] <= address
                       and address + size <= s['address'] + s['file_size'])
        offset = segment['offset'] + address - segment['address']
        return binary[offset:offset + size]

    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    for page in (0x104cc0000, 0x104e06000):
        emulator.mem_map(page, 4096, UC_PROT_READ | UC_PROT_EXEC)
        emulator.mem_write(page, read(page, 4096))
    for page in (0x105c71000, 0x105925000, 0x105c2e000):
        emulator.mem_map(page, 4096, UC_PROT_READ)
        emulator.mem_write(page, read(page, 4096))
    scratch = 0x20000000
    emulator.mem_map(scratch, 65536, UC_PROT_READ | UC_PROT_WRITE)

    start = 0x104cc09a4
    end = layout['functions'][bisect.bisect_right(layout['functions'], start)]
    # Stop before the first external call (the unrelated string member setup).
    calls = [start + index * 4 for index, (word,) in enumerate(struct.iter_unpack('<I', read(start, end - start)))
             if word & 0x7c000000 == 0x14000000]
    stop = min(calls)
    results = []
    for fill in (0x00, 0x55, 0xa5, 0xff):
        emulator.mem_write(scratch, bytes([fill]) * 0x4000)
        emulator.reg_write(UC_ARM64_REG_SP, scratch + 0xf000)
        # Replicator +1448 -> stats member +4456 -> snapshot +60.
        emulator.reg_write(UC_ARM64_REG_X0, scratch + 1448 + 4456)
        emulator.emu_start(start, stop, count=1000)
        if emulator.reg_read(UC_ARM64_REG_PC) != stop:
            raise AssertionError('native initializer did not reach its bounded stop')
        # Native block clears the 16 bytes beginning at stats member +8288.
        emulator.reg_write(UC_ARM64_REG_X19, scratch + 1448)
        emulator.emu_start(0x104e061fc, 0x104e06208, count=8)
        if emulator.reg_read(UC_ARM64_REG_PC) != 0x104e06208:
            raise AssertionError('native timing initializer did not reach its bounded stop')
        words = [struct.unpack('<I', emulator.mem_read(scratch + offset, 4))[0]
                 for offset in (5964, 9736, 9740)]
        if words != [0, 0, 0]:
            raise AssertionError(f'native ping initialization differs for scratch {fill:#x}: {words}; stop={stop:#x}')
        results.append({'scratch_fill': fill, 'words': words})
    report = {'sha256': digest, 'offline_only': True, 'native_initialization_cases': results,
              'native_initialization_verified': True, 'transport_update_paths_verified': False,
              'server_acceptance_verified': False}
    (root / 'outputs/ping-analysis/native-initialization-validation.json').write_text(
        json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
