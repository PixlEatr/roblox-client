#!/usr/bin/env python3
"""Bounded native server-role current-leaf reader check; synthetic only."""
import hashlib
import json
import math
import struct
from pathlib import Path

from unicorn import (Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE,
                     UC_HOOK_MEM_FETCH_UNMAPPED, UC_HOOK_MEM_READ_UNMAPPED,
                     UC_HOOK_MEM_WRITE_UNMAPPED)
from unicorn.arm64_const import (UC_ARM64_REG_PC, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_X0, UC_ARM64_REG_X1,
                                 UC_ARM64_REG_X2, UC_ARM64_REG_X3,
                                 UC_ARM64_REG_X4, UC_ARM64_REG_X30,
                                 UC_ARM64_REG_S0)
from physics_history import CurrentRecord
from physics_packet import TransformHeader
from physics_rotation import (RotationField, decode_native_quaternion,
                              encode_native_quaternion,
                              native_matrix3x3_to_quaternion)
from physics_velocity import decode, decode_native_short_velocity, encode_native_short_velocity
from physics_wire import position as encode_position, velocity as encode_velocity
from wire_reader import Reader
from world_replication import World

# 0x104D47B94 is the one-current-record leaf. It calls the actual transform
# reader 0x104D48680 and velocity leaf 0x104D4886C.
CURRENT_LEAF = 0x104D47B94
TRANSFORM_READER = 0x104D48680
VELOCITY_READER = 0x104D4886C
POSITION_READER = 0x104CBEAE0
QUATERNION_READER = 0x104CBEE10
SHORT_VELOCITY_READER = 0x104CBF634
COMPACT_VELOCITY_READER = 0x104CBF400
READ_BYTES = 0x1015F8E60
READ_U8 = 0x1015FA0D8
READ_U16 = 0x1015FA26C
READ_U32 = 0x1015F9DCC
SCOPED_REFERENCE_READER = 0x104CD1F58
REFERENCE_VARUINT_HELPER = 0x104F23D98
VARUINT_READER = 0x1015F95A4
VARUINT_LOOP = 0x1015F95E0
READ_RAW_U32 = 0x1015F8C98
BUFFER_COPY = 0x105165D60
LDEXP_HELPER = 0x105165BBC
NATIVE_BOUNDS_ERROR = 0x105164FB0
BASE = 0x20000000
STACK = BASE + 0xE000
READER = BASE + 0x4000
WIRE = BASE + 0x5000
OUTPUT = BASE + 0x6000
SELF = BASE + 0x7000
CANARY = BASE + 0xF000
RETURN = 0x30000000
GUARD_VALUE = 0x91e1cda5c0deface


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def set_s0(uc, value):
    uc.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', f32(value)))[0])


def get_s0(uc):
    return struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0]


def close3(a, b, tol=4e-4):
    return len(a) == len(b) and all(abs(x-y) <= tol for x, y in zip(a, b))


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

    def image_read(address, size):
        seg = next(s for s in layout['segments'] if s['address'] <= address and
                   address + size <= s['address'] + s['file_size'])
        offset = seg['offset'] + address - seg['address']
        return image[offset:offset + size]

    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, 0x10000)
    mapped = set()

    def map_image_page(address):
        page = address & ~0xfff
        if page in mapped:
            return
        seg = next((s for s in layout['segments'] if s['address'] <= page and
                    page + 0x1000 <= s['address'] + s['file_size']), None)
        if seg is None:
            raise RuntimeError(f'Unmapped native access outside file-backed image: {address:#x}')
        uc.mem_map(page, 0x1000)
        uc.mem_write(page, image_read(page, 0x1000))
        mapped.add(page)

    hooks = {BUFFER_COPY, LDEXP_HELPER, NATIVE_BOUNDS_ERROR}
    addresses = (CURRENT_LEAF, TRANSFORM_READER, VELOCITY_READER,
                 POSITION_READER, QUATERNION_READER, SHORT_VELOCITY_READER,
                 COMPACT_VELOCITY_READER, READ_BYTES, READ_U8, READ_U16, READ_U32,
                 SCOPED_REFERENCE_READER, REFERENCE_VARUINT_HELPER,
                 VARUINT_READER, VARUINT_LOOP, READ_RAW_U32,
                 BUFFER_COPY, LDEXP_HELPER, NATIVE_BOUNDS_ERROR,
                 0x102E86748, 0x10647C000,
                 0x106A48000)
    for address in addresses:
        map_image_page(address)
    # Runtime-initialized stack protector state, seeded consistently for the
    # native quaternion decoder's real prologue/epilogue check.
    uc.mem_write(0x10647C7B0, struct.pack('<Q', CANARY))
    uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))

    events = []
    copy_calls = []
    native_error = []

    def code_hook(_uc, address, size, _data):
        events.append(address)
        if address == BUFFER_COPY:
            dst, src, count = (uc.reg_read(UC_ARM64_REG_X0),
                               uc.reg_read(UC_ARM64_REG_X1),
                               uc.reg_read(UC_ARM64_REG_X2))
            uc.mem_write(dst, bytes(uc.mem_read(src, count)))
            copy_calls.append(count)
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == LDEXP_HELPER:
            exponent = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            if exponent & 0x80000000:
                exponent -= 1 << 32
            set_s0(uc, math.ldexp(get_s0(uc), exponent))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == NATIVE_BOUNDS_ERROR:
            native_error.append({'pc': hex(address), 'cursor': struct.unpack(
                '<I', uc.mem_read(READER + 0x38, 4))[0]})
            uc.emu_stop()

    def unmapped_hook(_uc, access, address, size, value, _data):
        map_image_page(address)
        return True

    uc.hook_add(UC_HOOK_CODE, code_hook)
    uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, unmapped_hook)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped_hook)
    uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, unmapped_hook)

    matrix_samples = (
        ('yaw_plus_90', (0.0, 0.7071067690849304, 0.0, 0.7071067690849304)),
        ('mixed_rotation', (0.31, -0.42, 0.17, 0.837)),
    )
    cases = []
    for mode_name, mode_flag, vel_encoder, vel_decoder in (
            ('compact5', 0, encode_velocity, decode),
            ('short3', 1, encode_native_short_velocity, decode_native_short_velocity)):
        for rot_name, quaternion in matrix_samples:
            pos = (1.25, -2.5, 3.75)
            linear = (3.0, -1.25, 0.5)
            angular = (-0.75, 2.0, -1.5)
            rotation = RotationField(0, 'native-quaternion', quaternion)
            header = TransformHeader(0, False, False, False, 0)
            rec = CurrentRecord(header, pos, rotation, linear, angular,
                                'short' if mode_flag else 'compact', state=255)
            wire = rec.encode()
            # This is the actual Reader layout used by 0x1015F8E60: buffer
            # base at +0x10, size at +0x30, cursor at +0x38.
            uc.mem_write(READER, b'\0' * 0x40)
            uc.mem_write(READER + 0x10, struct.pack('<Q', WIRE))
            uc.mem_write(READER + 0x30, struct.pack('<I', len(wire)))
            uc.mem_write(READER + 0x38, struct.pack('<I', 0))
            uc.mem_write(WIRE, wire)
            uc.mem_write(OUTPUT, b'\0' * 0x100)
            uc.mem_write(SELF, b'\0' * 0x200)
            uc.mem_write(SELF + 0xE0, b'\0')  # disable world/statistics side path
            uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))
            events.clear()
            copy_calls.clear()
            for reg, value in ((UC_ARM64_REG_X0, SELF), (UC_ARM64_REG_X1, READER),
                               (UC_ARM64_REG_X2, OUTPUT), (UC_ARM64_REG_X3, mode_flag),
                               (UC_ARM64_REG_X4, 0), (UC_ARM64_REG_X30, RETURN),
                               (UC_ARM64_REG_SP, STACK)):
                uc.reg_write(reg, value)
            uc.emu_start(CURRENT_LEAF, RETURN, count=12000)
            if uc.reg_read(UC_ARM64_REG_PC) != RETURN:
                raise RuntimeError(f'{mode_name}/{rot_name}: native reader did not return at {uc.reg_read(UC_ARM64_REG_PC):#x}, tail={events[-12:]}')
            cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
            consumed = wire[:cursor]
            remaining = wire[cursor:]
            expected_position = tuple(World.compact_position(Reader(encode_position(pos))))
            expected_rotation, qused = decode_native_quaternion(encode_native_quaternion(quaternion))
            expected_linear = vel_decoder(vel_encoder(linear)).vector
            expected_angular = vel_decoder(vel_encoder(angular)).vector
            actual_rotation_matrix = struct.unpack('<9f', uc.mem_read(OUTPUT, 36))
            actual_quaternion = native_matrix3x3_to_quaternion(actual_rotation_matrix)
            actual_position = struct.unpack('<3f', uc.mem_read(OUTPUT + 0x24, 12))
            actual_velocities = struct.unpack('<6f', uc.mem_read(OUTPUT + 0x30, 24))
            if cursor != len(wire) - 1 or remaining != b'\xff':
                raise AssertionError(f'{mode_name}/{rot_name}: expected transform-only consumption leaving state suffix; cursor={cursor}/{len(wire)} remainder={remaining.hex()}')
            if not close3(actual_position, expected_position):
                raise AssertionError(f'{mode_name}/{rot_name}: position native={actual_position} production={expected_position}')
            expected_rotation_matrix = matrix_from_quaternion(expected_rotation)
            if not close3(actual_rotation_matrix, expected_rotation_matrix, tol=7e-4):
                raise AssertionError(f'{mode_name}/{rot_name}: native matrix={actual_rotation_matrix} production={expected_rotation_matrix}; q={actual_quaternion} expected={expected_rotation}')
            # Matrix->quaternion conversion is sign-insensitive by construction.
            qdot = sum(a*b for a, b in zip(actual_quaternion, expected_rotation))
            if abs(abs(qdot) - 1.0) > 7e-4:
                raise AssertionError(f'{mode_name}/{rot_name}: matrix pose q={actual_quaternion} production={expected_rotation}')
            if not close3(actual_velocities[:3], expected_linear) or not close3(actual_velocities[3:], expected_angular):
                raise AssertionError(f'{mode_name}/{rot_name}: velocity native={actual_velocities} production={expected_linear + expected_angular}')
            required = (TRANSFORM_READER, VELOCITY_READER, POSITION_READER,
                        QUATERNION_READER, SHORT_VELOCITY_READER if mode_flag else COMPACT_VELOCITY_READER)
            if any(address not in events for address in required):
                raise AssertionError(f'{mode_name}/{rot_name}: not all native decode helpers executed: {[hex(a) for a in required if a not in events]}')
            cases.append({
                'mode': mode_name, 'rotation_case': rot_name,
                'input_record_hex': wire.hex(), 'record_bytes': len(wire),
                'native_leaf_consumed_bytes': cursor, 'native_leaf_consumed_hex': consumed.hex(),
                'native_leaf_remaining_hex': remaining.hex(),
                'expected_suffix_hex': 'ff',
                'position_native': list(actual_position), 'position_production': list(expected_position),
                'quaternion_native_xyzw_from_output_matrix': list(actual_quaternion),
                'quaternion_production_xyzw': list(expected_rotation),
                'rotation_matrix_native_row_major': list(actual_rotation_matrix),
                'rotation_matrix_production_row_major': list(expected_rotation_matrix),
                'linear_velocity_native': list(actual_velocities[:3]),
                'linear_velocity_production': list(expected_linear),
                'angular_velocity_native': list(actual_velocities[3:]),
                'angular_velocity_production': list(expected_angular),
                'decode_helpers_executed': [hex(a) for a in required],
                'native_reader_copy_sizes': list(copy_calls),
                'pose_match': True,
            })

            # Server mode 1's caller treats this post-leaf field as a scoped
            # reference. Its null encoding is one zero byte; following child
            # and outer null terminators are distinct references.
            root_record = CurrentRecord(header, pos, rotation, linear, angular,
                                        'short' if mode_flag else 'compact',
                                        reference_bytes=b'\0')
            transform_wire = root_record.encode()[:-1]
            root_wire = transform_wire + b'\0\0\0'
            uc.mem_write(READER, b'\0' * 0x40)
            uc.mem_write(READER + 0x10, struct.pack('<Q', WIRE))
            uc.mem_write(READER + 0x30, struct.pack('<I', len(root_wire)))
            uc.mem_write(READER + 0x38, struct.pack('<I', 0))
            uc.mem_write(WIRE, root_wire)
            uc.mem_write(SELF, b'\0' * 0x200)
            uc.mem_write(SELF + 0xA8, struct.pack('<I', 0x42))
            uc.mem_write(SELF + 0xE0, b'\0')
            uc.mem_write(OUTPUT, b'\0' * 0x100)
            uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))
            for reg, value in ((UC_ARM64_REG_X0, SELF), (UC_ARM64_REG_X1, READER),
                               (UC_ARM64_REG_X2, OUTPUT), (UC_ARM64_REG_X3, mode_flag),
                               (UC_ARM64_REG_X4, 0), (UC_ARM64_REG_X30, RETURN),
                               (UC_ARM64_REG_SP, STACK)):
                uc.reg_write(reg, value)
            uc.emu_start(CURRENT_LEAF, RETURN, count=12000)
            transform_end = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
            if transform_end != len(transform_wire):
                raise AssertionError(f'{mode_name}/{rot_name}: reference fixture leaf consumed {transform_end}, expected {len(transform_wire)}')
            null_references = []
            for label in ('root_suffix', 'child_terminator', 'outer_terminator'):
                before = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
                for reg, value in ((UC_ARM64_REG_X0, SELF), (UC_ARM64_REG_X1, READER),
                                   (UC_ARM64_REG_X30, RETURN), (UC_ARM64_REG_SP, STACK)):
                    uc.reg_write(reg, value)
                events.clear()
                uc.emu_start(SCOPED_REFERENCE_READER, RETURN, count=6000)
                result = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
                after = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
                if result != 0xffffffff or after != before + 1:
                    raise AssertionError(f'{mode_name}/{rot_name}/{label}: native null reference result={result:#x}, cursor {before}->{after}')
                null_references.append({'field': label, 'wire_hex': '00',
                                        'native_result_u32': '0xffffffff',
                                        'cursor_before': before, 'cursor_after': after})
            if struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0] != len(root_wire):
                raise AssertionError('the three null references did not consume exactly their three separate bytes')
            cases[-1]['root_reference_fixture'] = {
                'full_wire_hex': root_wire.hex(),
                'transform_bytes_before_reference_parser': len(transform_wire),
                'null_references': null_references,
                'total_cursor_after_three_references': len(root_wire),
                'all_three_are_distinct_one_byte_nulls': True,
            }

    # The old one-byte FF suffix plus child and outer null terminators totals
    # three bytes. The scoped parser consumes FF and the child 00 as ULEB tag
    # 127, leaving only the outer 00 where a four-byte raw ID is required.
    old_tail = bytes.fromhex('ff 00 00')
    uc.mem_write(READER, b'\0' * 0x40)
    uc.mem_write(READER + 0x10, struct.pack('<Q', WIRE))
    uc.mem_write(READER + 0x30, struct.pack('<I', len(old_tail)))
    uc.mem_write(READER + 0x38, struct.pack('<I', 0))
    uc.mem_write(WIRE, old_tail)
    uc.mem_write(SELF, b'\0' * 0x200)
    uc.mem_write(SELF + 0xA8, struct.pack('<I', 0x42))
    native_error.clear()
    events.clear()
    for reg, value in ((UC_ARM64_REG_X0, SELF), (UC_ARM64_REG_X1, READER),
                       (UC_ARM64_REG_X30, RETURN), (UC_ARM64_REG_SP, STACK)):
        uc.reg_write(reg, value)
    uc.emu_start(SCOPED_REFERENCE_READER, RETURN, count=6000)
    old_tail_probe = {
        'wire_hex': old_tail.hex(),
        'native_bounds_error_hook_reached': bool(native_error),
        'native_error': native_error[0] if native_error else None,
        'explanation': 'The old suffix ff followed by child/outer nulls is ff 00 00. ULEB128 consumes ff 00 as non-null tag 127, leaving one byte; the scoped reference then requires four raw ID bytes and reaches the native bounds-error path.',
    }
    if not native_error:
        raise AssertionError('legacy ff0000 + child/outer nulls did not reach native truncation path')

    report = {
        'status': 'verified',
        'evidence_type': 'bounded Unicorn execution of pinned native server-mode current leaf; synthetic records only',
        'binary_sha256': digest, 'binary_version': provenance['version'],
        'server_role_evidence': 'server receiver mode 1 reaches 0x104D47B94; caller sets its velocity codec argument from outer flags bit 6',
        'current_leaf': hex(CURRENT_LEAF), 'transform_reader': hex(TRANSFORM_READER),
        'position_reader': hex(POSITION_READER), 'quaternion_reader': hex(QUATERNION_READER),
        'velocity_reader': hex(VELOCITY_READER),
        'reader_contract': 'native stream reader object: buffer pointer +0x10, size +0x30, cursor +0x38',
        'output_contract': 'row-major rotation matrix at +0, decoded position at +0x24, linear/angular vectors at +0x30/+0x3c',
        'current_vs_history_static_trace': {
            'dispatch': '0x104D4654C selects 0x104D47B94 for receiver mode byte +0x11 == 1; otherwise it calls 0x104D47C00',
            'single_current_leaf': '0x104D47B94 reads one transform flags byte, calls 0x104D48680 then 0x104D4886C, and returns before the caller parses the state/reference suffix',
            'counted_history_wrapper': '0x104D47C00 reads one u8 sample count; zero returns immediately; each sample reads one float32 time delta, calls 0x104D47B94, then the wrapper/caller handles its suffix before decrementing the count',
            'history_wrapper_emulated': False,
            'basis': 'direct raw ARM64 disassembly from the pinned image; positive-count loop not emulated in this report',
        },
        'hooks': ['0x105165D60 byte-copy primitive', '0x105165BBC power-of-two scaling helper'],
        'cases': cases,
        'legacy_ff_suffix_probe': old_tail_probe,
        'unsupported': ['surrounding root/motor/reference parsing', 'world/application queue and ownership effects',
                        'live server acceptance', 'full current suffix dispatch after leaf return'],
    }
    out = root / 'outputs/physics-final/native-current-leaf-reader.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'validated {len(cases)} server-role current leaf cases; report {out}')


if __name__ == '__main__':
    main()
