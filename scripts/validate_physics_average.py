"""Compare real native averaging instructions with local job statistics offline.

Requires optional unicorn==2.1.4. Executes only 1008e94d8 in bounded emulation;
no application startup, imports, credentials, sockets, or server claims.
"""
import bisect
import hashlib
import json
import math
from pathlib import Path
import struct

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_PROT_READ,
                     UC_PROT_WRITE, UC_PROT_EXEC, UC_HOOK_MEM_READ_UNMAPPED)
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X30, UC_ARM64_REG_D0, UC_ARM64_REG_PC

from physics_job_statistics import PhysicsJobStatistics


def main():
    root = Path(__file__).resolve().parents[1]
    binary = (root / 'inputs/RobloxPlayer-0.745-arm64').read_bytes()
    digest = hashlib.sha256(binary).hexdigest()
    if digest != 'b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870':
        raise ValueError('unsupported native image')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())
    start = 0x1008e94d8
    end = layout['functions'][bisect.bisect_right(layout['functions'], start)]
    emulator = Uc(UC_ARCH_ARM64, UC_MODE_ARM)

    def read(address, size):
        segment = next(s for s in layout['segments'] if s['address'] <= address
                       and address + size <= s['address'] + s['file_size'])
        offset = segment['offset'] + address - segment['address']
        return binary[offset:offset + size]

    for page in range(start & ~4095, (end + 4095) & ~4095, 4096):
        emulator.mem_map(page, 4096, UC_PROT_READ | UC_PROT_EXEC)
        emulator.mem_write(page, read(page, 4096))

    def constant_page(uc, access, address, size, value, user):
        # Unmapped instruction fetches and writes are never handled. Only
        # file-backed constant data may be read by this leaf function.
        page = address & ~4095
        if not 0x105000000 <= page < 0x106480000:
            return False
        uc.mem_map(page, 4096, UC_PROT_READ)
        uc.mem_write(page, read(page, 4096))
        return True

    emulator.hook_add(UC_HOOK_MEM_READ_UNMAPPED, constant_page)
    scratch, stop = 0x20000000, 0x30000000
    emulator.mem_map(scratch, 4096, UC_PROT_READ | UC_PROT_WRITE)
    emulator.mem_map(stop, 4096, UC_PROT_READ | UC_PROT_EXEC)
    cases = []
    for coefficient in (0.049, 0.05, 0.051, 0.2):
        times = iter((0.0, 0.1, 0.1, 0.3, 0.6, 1.6))
        model = PhysicsJobStatistics(clock=lambda: next(times))
        model.smoothing = coefficient
        model.completed()  # Native interval timer's first completion seeds time.
        emulator.mem_write(scratch, struct.pack('<ddddB', coefficient, 0.0, 0.0, 0.0, 1))
        for _ in range(5):
            model.completed()
            emulator.reg_write(UC_ARM64_REG_X0, scratch)
            emulator.reg_write(UC_ARM64_REG_X30, stop)
            emulator.reg_write(UC_ARM64_REG_D0, struct.unpack('<Q', struct.pack('<d', model.last_interval))[0])
            emulator.emu_start(start, stop, count=1000)
            if emulator.reg_read(UC_ARM64_REG_PC) != stop:
                raise AssertionError('native average did not return within instruction limit')
            actual = struct.unpack('<ddddB', emulator.mem_read(scratch, 33))
            expected = (coefficient, model.last_interval, model.mean_interval, model.variance)
            if not all(math.isclose(a, b, rel_tol=1e-13, abs_tol=1e-15)
                       for a, b in zip(actual[:4], expected)) or actual[4] != 0:
                raise AssertionError('native and runtime averaging state differ')
        cases.append({'coefficient': coefficient, 'native_samples_matched': 5})
    report = {'binary_sha256': digest, 'offline_only': True,
              'native_function': hex(start), 'cases': cases,
              'native_average_recurrence_verified': True,
              'full_physics_equivalence_verified': False, 'server_acceptance_verified': False}
    (root / 'outputs/ping-analysis/physics-average-native-validation.json').write_text(
        json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
