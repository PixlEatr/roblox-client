#!/usr/bin/env python3
"""Bounded D46074 parser check with synthetic objects and optional captured bytes.

This checks cursor accounting, not live server ownership or state application.
"""
import hashlib
import argparse
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
                                 UC_ARM64_REG_X4, UC_ARM64_REG_X5,
                                 UC_ARM64_REG_X6, UC_ARM64_REG_X7,
                                 UC_ARM64_REG_X30, UC_ARM64_REG_S0,
                                 UC_ARM64_REG_D0)
from physics_history import CurrentRecord
from physics_root_record import encode_client_current_body
from physics_packet import TransformHeader
from physics_rotation import RotationField
from physics_velocity import encode_native_short_velocity
from physics_wire import velocity as encode_compact_velocity

SERVER_BODY_READER = 0x104D46074
CURRENT_DISPATCH = 0x104D4654C
CURRENT_LEAF = 0x104D47B94
MOTOR_LIST_READER = 0x104D47A98
CHILD_READER = 0x104D469F4
SCOPED_REFERENCE_READER = 0x104CD1F58
REFERENCE_HELPER = 0x104F23D98
VARUINT_WRAPPER = 0x1015F95A4
VARUINT_BODY = 0x1015F95E0
U8_READER = 0x1015FA0D8
U8_BODY = 0x1015FA0FC
U16_READER = 0x1015FA26C
U32_READER = 0x1015F9DCC
RAW_U32_READER = 0x1015F8C98
RAW_BYTES_READER = 0x1015F8E60
BUFFER_COPY = 0x105165D60
LDEXP_HELPER = 0x105165BBC
RESOLVE_REFERENCE_BOUNDARY = 0x102B2BCF0
STATISTICS_BOUNDARY = 0x104DBE644
ROOT_FIELD_QUERY = 0x104DBE540
NATIVE_BOUNDS_ERROR = 0x105164FB0
BASE = 0x20000000
STACK = BASE + 0x1E000
READER = BASE + 0x4000
WIRE = BASE + 0x5000
SELF = BASE + 0x7000
ROOT_OBJECT = BASE + 0x9000
VECTOR_STORAGE = BASE + 0xC000
CANARY = BASE + 0x1F000
RETURN = 0x30000000
GUARD_VALUE = 0x7d1b3ac94f5082e6

# Exact native routine ranges used by the bounded parser. The hook rejects any
# direct call that leaves these codecs/dispatchers or the listed boundaries.
ALLOWED_RANGES = (
    (0x104D46074, 0x104D46474),
    (0x104D4654C, 0x104D46768),
    (0x104D469F4, 0x104D46BB8),
    (0x104D47A98, 0x104D47B68),
    (0x104D47B94, 0x104D47C00),
    (0x104D48680, 0x104D4886C),
    (0x104D4886C, 0x104D48978),
    (0x104CD1F58, 0x104CD1FC0),
    (0x104F23D98, 0x104F23DE4),
    (0x104CD2004, 0x104CD2044),
    (0x1015F8E60, 0x1015F8EE8),
    (0x1015F8F40, 0x1015F8F78),
    (0x1015F8C98, 0x1015F8D84),
    (0x1015F95A4, 0x1015F95E0),
    (0x1015F95E0, 0x1015F9778),
    (0x1015FA0D8, 0x1015FA0FC),
    (0x1015FA0FC, 0x1015FA128),
    (0x1015FA26C, 0x1015FA2A0),
    (0x1015F9DCC, 0x1015F9DF0),
    (0x1015F9DF0, 0x1015F9E1C),
    (0x1015FA354, 0x1015FA388),
    (0x104CBEAE0, 0x104CBEC4C),
    (0x104CBEE10, 0x104CBEF64),
    (0x104CBF400, 0x104CBF4AC),
    (0x104CBF634, 0x104CBF6E4),
    (0x101151A14, 0x101151A54),
    (0x104DBE540, 0x104DBE550),
    (0x102E86748, 0x102E867D8),
    (0x102E81DAC, 0x102E81E80),
)


def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


def put_s0(uc, value):
    uc.reg_write(UC_ARM64_REG_S0, struct.unpack('<I', struct.pack('<f', f32(value)))[0])


def get_s0(uc):
    return struct.unpack('<f', struct.pack('<I', uc.reg_read(UC_ARM64_REG_S0)))[0]


def call_bl_target(word, pc):
    imm = word & 0x03ffffff
    if imm & (1 << 25):
        imm -= 1 << 26
    return pc + imm * 4


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--output', type=Path,
                        default=root / 'outputs/physics-final/native-root-body-reader.json')
    parser.add_argument('--captured-bodies', type=Path,
                        help='JSON list of bounded body_hex values, each including one outer null')
    args = parser.parse_args()
    provenance = json.loads((root / 'outputs/provenance.json').read_text())
    image = (args.image or Path(provenance['copy'])).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != provenance['sha256']:
        raise RuntimeError('Pinned native binary hash mismatch')
    layout = json.loads((root / 'outputs/macho-layout.json').read_text())

    def image_read(addr, size):
        seg = next(s for s in layout['segments'] if s['address'] <= addr and
                   addr + size <= s['address'] + s['file_size'])
        off = seg['offset'] + addr - seg['address']
        return image[off:off + size]

    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.mem_map(BASE, 0x20000)
    mapped_pages = set()

    def map_page(address):
        page = address & ~0xfff
        if page in mapped_pages:
            return
        seg = next((s for s in layout['segments'] if s['address'] <= page and
                    page + 0x1000 <= s['address'] + s['file_size']), None)
        if seg is None:
            raise RuntimeError(f'Unmapped native access outside pinned image: {address:#x}')
        uc.mem_map(page, 0x1000)
        uc.mem_write(page, image_read(page, 0x1000))
        mapped_pages.add(page)

    boundary_addresses = (BUFFER_COPY, LDEXP_HELPER, RESOLVE_REFERENCE_BOUNDARY,
                          ROOT_FIELD_QUERY, NATIVE_BOUNDS_ERROR)
    eager_addresses = (SERVER_BODY_READER, CURRENT_DISPATCH, CURRENT_LEAF,
                       MOTOR_LIST_READER, CHILD_READER, SCOPED_REFERENCE_READER,
                       REFERENCE_HELPER, VARUINT_WRAPPER, VARUINT_BODY, U8_READER,
                       U8_BODY, U16_READER, U32_READER, RAW_U32_READER,
                       RAW_BYTES_READER, BUFFER_COPY, LDEXP_HELPER,
                       RESOLVE_REFERENCE_BOUNDARY, ROOT_FIELD_QUERY,
                       NATIVE_BOUNDS_ERROR, 0x104CD2004, 0x104CD2044,
                       0x104CBEAE0, 0x104CBEE10, 0x104CBF400, 0x104CBF634,
                       0x102E86748, 0x102E81DAC, 0x101151A14,
                       0x10647C000, 0x106ACD000, 0x106A48000)
    for address in eager_addresses:
        map_page(address)

    # Synthetic runtime metadata: reader ABI, server receiver role, object
    # context tag, empty motor bucket, and an in-capacity staging container.
    uc.mem_write(READER, b'\0' * 0x40)
    uc.mem_write(READER + 0x10, struct.pack('<Q', WIRE))
    uc.mem_write(SELF, b'\0' * 0x500)
    uc.mem_write(SELF, struct.pack('<Q', ROOT_OBJECT))
    uc.mem_write(SELF + 0x11, b'\x01')
    uc.mem_write(SELF + 0xE0, b'\0')
    uc.mem_write(ROOT_OBJECT, b'\0' * 0x4000)
    uc.mem_write(ROOT_OBJECT + 0xA8, struct.pack('<I', 0x42))
    uc.mem_write(ROOT_OBJECT + 0xFC, struct.pack('<I', 0))
    uc.mem_write(ROOT_OBJECT + 0x3EBD, b'\0')
    uc.mem_write(ROOT_OBJECT + 0x2CA8, struct.pack('<Q', VECTOR_STORAGE + 0x100))
    uc.mem_write(ROOT_OBJECT + 0x2CB0, struct.pack('<Q', VECTOR_STORAGE + 0x400))
    uc.mem_write(VECTOR_STORAGE + 0x108, struct.pack('<QQ', VECTOR_STORAGE + 0x800,
                                                     VECTOR_STORAGE + 0x800))
    uc.mem_write(0x106ACD5F8, b'\0')  # disable timing/telemetry branch only
    uc.mem_write(0x10647C7B0, struct.pack('<Q', CANARY))
    uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))

    calls = []
    hooks = []
    stop_boundary = []
    native_errors = []
    unclassified = []

    def in_allowed_range(addr):
        return any(lo <= addr < hi for lo, hi in ALLOWED_RANGES)

    def set_x0(value):
        uc.reg_write(UC_ARM64_REG_X0, value & 0xffffffffffffffff)

    def code_hook(_uc, address, size, _data):
        word = struct.unpack('<I', uc.mem_read(address, 4))[0]
        if address == RESOLVE_REFERENCE_BOUNDARY:
            cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
            result_ptr = uc.reg_read(UC_ARM64_REG_X1)
            parsed_ref = struct.unpack('<Q', uc.mem_read(result_ptr, 8))[0]
            stop_boundary.append({'address': hex(address), 'pc': hex(address),
                                  'reader_cursor': cursor,
                                  'parsed_reference_u64': f'0x{parsed_ref:016x}',
                                  'description': 'unresolved instance-map lookup after native child-null parse'})
            uc.emu_stop()
            return
        # Stop on unclassified direct BL/BLR calls before executing them.
        if word & 0xfc000000 == 0x94000000:
            target = call_bl_target(word, address)
            calls.append({'pc': hex(address), 'target': hex(target)})
            if target in (BUFFER_COPY, LDEXP_HELPER):
                pass
            elif target == ROOT_FIELD_QUERY:
                # This helper only reads the synthetic object's mode bit.
                pass
            elif target == RESOLVE_REFERENCE_BOUNDARY:
                cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
                stop_boundary.append({'address': hex(target), 'pc': hex(address),
                                      'reader_cursor': cursor,
                                      'description': 'unresolved instance-map lookup after native child-null parse'})
                uc.emu_stop()
                return
            elif target == STATISTICS_BOUNDARY:
                cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
                stop_boundary.append({'address': hex(target), 'pc': hex(address),
                                      'reader_cursor': cursor,
                                      'description': 'root body parsed; stop before native statistics update'})
                uc.emu_stop()
                return
            elif target == NATIVE_BOUNDS_ERROR:
                native_errors.append({'address': hex(target), 'reader_cursor':
                                      struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]})
                uc.emu_stop()
                return
            elif not in_allowed_range(target):
                unclassified.append({'pc': hex(address), 'target': hex(target)})
                uc.emu_stop()
                return
        elif word & 0xfffffc1f == 0xd63f0000:
            unclassified.append({'pc': hex(address), 'target': 'BLR dynamic target'})
            uc.emu_stop()
            return

        if address == BUFFER_COPY:
            dst, src, count = (uc.reg_read(UC_ARM64_REG_X0),
                               uc.reg_read(UC_ARM64_REG_X1),
                               uc.reg_read(UC_ARM64_REG_X2))
            uc.mem_write(dst, bytes(uc.mem_read(src, count)))
            hooks.append({'address': hex(address), 'kind': 'byte-copy', 'count': count})
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))
        elif address == LDEXP_HELPER:
            exponent = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
            if exponent & 0x80000000:
                exponent -= 1 << 32
            put_s0(uc, math.ldexp(get_s0(uc), exponent))
            hooks.append({'address': hex(address), 'kind': 'power-of-two-scale', 'exponent': exponent})
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_X30))

    def unmapped(_uc, access, address, size, value, _data):
        map_page(address)
        return True

    uc.hook_add(UC_HOOK_CODE, code_hook)
    uc.hook_add(UC_HOOK_MEM_FETCH_UNMAPPED, unmapped)
    uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED, unmapped)
    uc.hook_add(UC_HOOK_MEM_WRITE_UNMAPPED, unmapped)

    inputs = [{'flags': flags, 'mode': mode, 'body': None, 'origin': 'synthetic'}
              for mode, flags in [('compact', 0), ('short', 0x40),
                                  ('compact', 0x20), ('short', 0x60)]]
    if args.captured_bodies:
        captured = json.loads(args.captured_bodies.read_text())
        if not isinstance(captured, list) or len(captured) > 128:
            raise ValueError('captured body list must contain at most 128 entries')
        for entry in captured:
            body = bytes.fromhex(entry['body_hex'])
            if not 4 <= len(body) <= 4096 or body[0] not in (0, 0x20, 0x40, 0x60) or body[-1] != 0:
                raise ValueError('unsupported captured body flags, size, or outer terminator')
            inputs.append({'flags': body[0], 'mode': 'short' if body[0] & 0x40 else 'compact',
                           'body': body, 'origin': 'captured', 'capture': entry.get('capture')})
    cases = []
    for case in inputs:
        mode, flags = case['mode'], case['flags']
        velocity_encoder = encode_native_short_velocity if mode == 'short' else encode_compact_velocity
        q = (0.31, -0.42, 0.17, 0.837)
        p = (1.25, -2.5, 3.75)
        lv = (3.0, -1.25, 0.5)
        av = (-0.75, 2.0, -1.5)
        header = TransformHeader(0, False, False, False, 0)
        record = CurrentRecord(header, p, RotationField(0, 'native-quaternion', q),
                               lv, av, mode, reference_bytes=b'\0')
        # Use the production encoder, including bit5's omitted child list.
        # The final outer null is outside this body encoder.
        body = case['body'] or encode_client_current_body(record, d76c_flags=flags) + b'\0'
        uc.mem_write(WIRE, body)
        uc.mem_write(READER + 0x30, struct.pack('<I', len(body)))
        uc.mem_write(READER + 0x38, struct.pack('<I', 0))
        uc.mem_write(SELF, struct.pack('<Q', ROOT_OBJECT))
        uc.mem_write(SELF + 0x11, b'\x01')
        uc.mem_write(SELF + 0xE0, b'\0')
        uc.mem_write(ROOT_OBJECT + 0xA8, struct.pack('<I', 0x42))
        uc.mem_write(ROOT_OBJECT + 0xFC, struct.pack('<I', 0))
        uc.mem_write(ROOT_OBJECT + 0x3EBD, b'\0')
        uc.mem_write(ROOT_OBJECT + 0x2CA8, struct.pack('<Q', VECTOR_STORAGE + 0x100))
        uc.mem_write(ROOT_OBJECT + 0x2CB0, struct.pack('<Q', VECTOR_STORAGE + 0x400))
        uc.mem_write(VECTOR_STORAGE + 0x108, struct.pack('<QQ', VECTOR_STORAGE + 0x800,
                                                         VECTOR_STORAGE + 0x800))
        uc.mem_write(CANARY, struct.pack('<Q', GUARD_VALUE))
        calls.clear(); hooks.clear(); stop_boundary.clear(); native_errors.clear(); unclassified.clear()
        for reg, val in ((UC_ARM64_REG_X0, SELF), (UC_ARM64_REG_X1, READER),
                         (UC_ARM64_REG_X2, 0), (UC_ARM64_REG_X30, RETURN),
                         (UC_ARM64_REG_SP, STACK), (UC_ARM64_REG_X3, 0),
                         (UC_ARM64_REG_X4, 0), (UC_ARM64_REG_X5, 0),
                         (UC_ARM64_REG_X6, 0), (UC_ARM64_REG_X7, 0),
                         (UC_ARM64_REG_D0, 0)):
            uc.reg_write(reg, val)
        uc.emu_start(SERVER_BODY_READER, RETURN, count=50000)
        cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
        if unclassified:
            raise RuntimeError(f'{mode}: parser reached unclassified call {unclassified[0]} at cursor {cursor}/{len(body)}')
        if native_errors:
            raise RuntimeError(f'{mode}: native reader error {native_errors[0]} at cursor {cursor}/{len(body)}')
        if not stop_boundary:
            raise RuntimeError(f'{mode}: expected parser boundary not reached; pc={uc.reg_read(UC_ARM64_REG_PC):#x}, cursor={cursor}/{len(body)}, tail_calls={calls[-12:]}')
        has_children = not bool(flags & 0x20)
        expected_boundary = RESOLVE_REFERENCE_BOUNDARY if has_children else STATISTICS_BOUNDARY
        if stop_boundary[0]['address'] != hex(expected_boundary):
            raise AssertionError(f'{mode}/{flags:#x}: unexpected parser boundary {stop_boundary[0]}')
        expected_before_outer = len(body) - 1
        if cursor != expected_before_outer:
            raise AssertionError(f'{mode}: D46074 path cursor {cursor}, expected {expected_before_outer} before outer terminator; bytes={body.hex()}')
        # D4654C parses the root suffix. With child traversal enabled, D469F4
        # parses child null before the unresolved mapping boundary. Without
        # traversal, D46074 reaches its statistics tail. Outer null belongs to
        # the enclosing parser and is separately run through the native codec.
        root_suffix_cursor = len(body) - 1 - int(has_children)
        uc.mem_write(READER + 0x30, struct.pack('<I', len(body)))
        for reg, val in ((UC_ARM64_REG_X0, ROOT_OBJECT), (UC_ARM64_REG_X1, READER),
                         (UC_ARM64_REG_X30, RETURN), (UC_ARM64_REG_SP, STACK)):
            uc.reg_write(reg, val)
        uc.emu_start(SCOPED_REFERENCE_READER, RETURN, count=6000)
        outer_result = uc.reg_read(UC_ARM64_REG_X0) & 0xffffffff
        outer_cursor = struct.unpack('<I', uc.mem_read(READER + 0x38, 4))[0]
        if outer_cursor != len(body) or outer_result != 0xffffffff:
            raise AssertionError(f'{mode}: outer null reference result={outer_result:#x}, cursor={outer_cursor}/{len(body)}')
        # These cases use one-byte null root references. Child traversal, when
        # selected, consumes an additional distinct one-byte null reference.
        cases.append({
            'wire_origin': case['origin'], 'capture': case.get('capture'),
            'velocity_mode': mode, 'd76c_flags': flags,
            'synthetic_runtime_inputs': {
                'receiver_mode_byte_self_plus_0x11': 1,
                'reader_buffer_size': len(body), 'root_context_tag': 0x42,
                'root_object_mapping_pointer': 0,
                'empty_motor_bucket': True,
                'staging_vector_has_capacity': True,
                'telemetry_fastflag_forced_off': True,
                'receiver_local_flag_self_plus_0xe0': 0,
            },
            'body_hex': body.hex(), 'body_length': len(body),
            'cursor_at_parser_boundary': cursor,
            'cursor_at_unresolved_child_mapping_boundary': cursor if has_children else None,
            'expected_cursor_after_child_null': expected_before_outer if has_children else None,
            'child_traversal_enabled': has_children,
            'expected_cursor_before_outer_null': expected_before_outer,
            'root_suffix_cursor_before': root_suffix_cursor - 1,
            'root_suffix_cursor_after': root_suffix_cursor,
            'child_null_cursor_before': root_suffix_cursor if has_children else None,
            'child_null_cursor_after': expected_before_outer if has_children else None,
            'outer_null_cursor_before': expected_before_outer,
            'outer_null_cursor_after': outer_cursor,
            'outer_null_result_u32': f'0x{outer_result:08x}',
            'body_cursor_accounting_matches': True,
            'native_call_targets': calls,
            'explicit_hooks': hooks,
            'stopped_at': stop_boundary[0],
            'production_velocity_fields_hex': ([velocity_encoder(lv).hex(), velocity_encoder(av).hex()]
                                               if case['body'] is None else None),
        })

    report = {
        'status': 'verified_to_explicit_boundaries',
        'evidence_type': 'bounded Unicorn execution of pinned server-mode root-body parser; synthetic objects, labeled synthetic or captured wire bytes',
        'binary_sha256': digest, 'binary_version': provenance['version'],
        'binary_source_provenance': provenance.get('source_provenance'),
        'entry': hex(SERVER_BODY_READER), 'current_dispatch': hex(CURRENT_DISPATCH),
        'current_leaf': hex(CURRENT_LEAF), 'child_reader': hex(CHILD_READER),
        'scoped_reference_reader': hex(SCOPED_REFERENCE_READER),
        'root_body_layout_tested': 'Production encode_client_current_body with flags 0, 0x40, 0x20, 0x60; enclosing outer null is separate',
        'boundary': 'Child traversal stops at instance-map resolver 0x102B2BCF0; no-child traversal stops before statistics update 0x104DBE644. No resolver or statistics result is fabricated.',
        'unclassified_parser_calls': [],
        'cases': cases,
        'unsupported': ['instance-map resolution and child application', 'world/ownership/anchor effects',
                        'outer packet framing beyond one separately parsed outer null',
                        'server acceptance or live replication'],
    }
    out = args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'validated {len(cases)} server-role root body cases to explicit parser boundaries; report {out}')


if __name__ == '__main__':
    main()
