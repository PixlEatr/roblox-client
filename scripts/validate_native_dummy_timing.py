"""Execute the pinned timing reader on synthetic replies; stop before callback.

No account, server connection, authentication state, or native app is used.
"""
import hashlib
import json
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED
from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2
from unicorn.arm64_const import UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP
from dummy_sync import response_timing

ENTRY = 0x104EEF158
CALLBACK = 0x10008A070
EXTENDED_OPTION = 0x106ACFC48
BASE = 0x20000000
SELF, BUFFER, WIRE = BASE+0x1000, BASE+0x2000, BASE+0x3000
MASK = (1 << 64)-1


def signed64(value):
    value &= MASK
    return value-(1 << 64) if value >= 1 << 63 else value


def trunc_div(value, divisor):
    return -(abs(value)//divisor) if value < 0 else value//divisor


def main():
    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root/'outputs/provenance.json').read_text())
    image = Path(provenance['copy']).read_bytes()
    if hashlib.sha256(image).hexdigest() != provenance['sha256']:
        raise RuntimeError('Pinned native image hash mismatch')
    segments = json.loads((root/'outputs/macho-layout.json').read_text())['segments']
    cases = []
    for name, words, received, extended in (
        ('positive_offsets', (1000000, 80, 1700000000000000, 80000000), 1020080, True),
        ('negative_offsets', (1000000, 80, 900000, 800000), 1020080, True),
        ('legacy_length', (1000000, 80, 900000), 1020080, True),
        ('extended_disabled', (1000000, 80, 900000, 800000), 1020080, False),
        ('fractional_integer_rounding', (1000000, 3, 1021001, 1020999), 1020004, True),
        ('fixed_width_subtraction', ((1 << 64)-2000, 10, 5000000, 4000000), 1000, True),
    ):
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(BASE, 0x10000)
        mapped = set()

        def map_image_page(address):
            page = address & ~0xfff
            if page in mapped:
                return
            segment = next((s for s in segments if s['address'] <= page < s['address']+s['size']), None)
            if segment is None:
                raise RuntimeError(f'Unclassified native memory access {address:#x}')
            uc.mem_map(page, 0x1000)
            file_count = min(0x1000, max(0, segment['file_size']-(page-segment['address'])))
            if file_count:
                start = segment['offset']+page-segment['address']
                uc.mem_write(page, image[start:start+file_count])
            mapped.add(page)

        def unmapped(_uc, access, address, size, value, _data):
            map_image_page(address)
            return True

        result = {}

        def on_code(_uc, address, size, _data):
            if address == CALLBACK:
                result.update(callback_pointer=uc.reg_read(UC_ARM64_REG_X0),
                              receive_us=uc.reg_read(UC_ARM64_REG_X1),
                              offset_ms=signed64(uc.reg_read(UC_ARM64_REG_X2)),
                              extended_offset_ms=signed64(uc.reg_read(UC_ARM64_REG_X3)))
                uc.emu_stop()

        for address in (ENTRY, CALLBACK, EXTENDED_OPTION):
            map_image_page(address)
        uc.hook_add(UC_HOOK_CODE, on_code)
        uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, unmapped)
        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped)
        uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, unmapped)
        payload = b'\x03'+struct.pack('<'+'Q'*len(words), *words)
        uc.mem_write(WIRE, payload)
        uc.mem_write(BUFFER+0x20, struct.pack('<QQ', WIRE, len(payload)))
        uc.mem_write(BUFFER+0x40, b'\x01')
        uc.mem_write(EXTENDED_OPTION, bytes((int(extended),)))
        uc.reg_write(UC_ARM64_REG_SP, BASE+0xF000)
        uc.reg_write(UC_ARM64_REG_X0, SELF)
        uc.reg_write(UC_ARM64_REG_X1, BUFFER)
        uc.reg_write(UC_ARM64_REG_X2, received)
        uc.reg_write(UC_ARM64_REG_X30, BASE+0xFF00)
        uc.emu_start(ENTRY, BASE+0xFF00, count=10000)
        half_interval = trunc_div(signed64(received-words[0]-words[1]), 2000)
        expected_offset = signed64(half_interval+trunc_div(signed64(words[2]-received), 1000))
        expected_extended = (signed64(half_interval+trunc_div(signed64(words[3]-received), 1000))
                             if extended and len(words) == 4 else 0)
        if result != dict(callback_pointer=SELF+0x88, receive_us=received,
                          offset_ms=expected_offset, extended_offset_ms=expected_extended):
            raise RuntimeError(f'Native timing mismatch in {name}')
        production = response_timing(payload, received, extended_timing=extended)
        assert production.local_receive_us == result['receive_us']
        assert production.half_interval_ms == half_interval
        assert production.callback_offset_ms == result['offset_ms']
        assert (production.callback_extended_offset_ms or 0) == result['extended_offset_ms']
        assert bytes(uc.mem_read(SELF+0x135, 1)) == b'\x01'
        assert struct.unpack('<Q', uc.mem_read(SELF, 8))[0] == received
        cases.append(dict(name=name, matched=True, production_codec_matched=True, receive_us=received,
                          offset_ms=expected_offset, extended_offset_ms=expected_extended))
    report = dict(binary_sha256=provenance['sha256'], entry=hex(ENTRY),
                  stopped_before_callback=hex(CALLBACK), cases=cases,
                  hooks=['stop before callback; no native functions replaced'],
                  limitations=['Synthetic buffer/object and extended timing option.',
                               'Callback consumer and physics clock use not established.',
                               'No live session or server acceptance evidence.'])
    (root/'outputs/physics-final/native-dummy-timing.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'native_cases_verified':len(cases), 'callback_executed':False}))


if __name__ == '__main__':
    main()
