import math
import struct
import unittest

from physics_rotation import (
    RotationCodecError, RotationField, decode_type27, decode_type51,
    decode_type57, encode_type27, encode_type51, encode_type57,
    decode_native_quaternion, encode_native_quaternion,
    native_matrix3x3_to_quaternion,
    native_compact_selector_to_matrix,
    native_compact_selector_to_quaternion,
    rotation_is_identity,
    native_yaw_rotation,
)


class PhysicsRotationTests(unittest.TestCase):
    def test_modeled_yaw_faces_motion_after_native_wire_roundtrip(self):
        samples = (
            (0.0, (0., 0., -1.)),
            (-math.pi/2, (1., 0., 0.)),
            (math.pi/2, (-1., 0., 0.)),
            (math.pi, (0., 0., 1.)),
            (-math.pi/4, (math.sqrt(.5), 0., -math.sqrt(.5))),
        )
        for yaw, expected in samples:
            with self.subTest(yaw=yaw):
                rotation = native_yaw_rotation(yaw)
                self.assertEqual((rotation.selector, rotation.kind), (0, 'native-quaternion'))
                quaternion, used = decode_native_quaternion(encode_native_quaternion(rotation.value))
                self.assertEqual(used, 6)
                x, y, z, w = quaternion
                look = (-2*(x*z+y*w), -2*(y*z-x*w), -(1-2*(x*x+y*y)))
                for actual, wanted in zip(look, expected):
                    self.assertAlmostEqual(actual, wanted, delta=.0003)

    def test_modeled_yaw_refuses_unknown_or_nonfinite_orientation(self):
        for value in (None, True, '0', float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaises(RotationCodecError):
                native_yaw_rotation(value)

    def test_type27_golden_selector_and_packed(self):
        # Literal observed framing: selector 0 consumes six bytes; selector 7
        # consumes only itself. Payload remains opaque.
        raw = bytes.fromhex("00 01 23 45 67 89 ab 07")
        packed, used = decode_type27(raw)
        self.assertEqual((packed.selector, packed.kind, packed.value, used),
                         (0, "packed-opaque", bytes.fromhex("01 23 45 67 89 ab"), 7))
        marker, used = decode_type27(raw, used)
        self.assertEqual((marker.selector, marker.kind, marker.value, used), (7, "selector", None, 1))
        self.assertEqual(encode_type27(packed) + encode_type27(marker), raw)

    def test_type27_layout_matched_native_identity_selector(self):
        # The pinned native candidate reads three f32 coordinates, then one
        # selector, consuming six more bytes only when selector is zero.
        prefix = struct.pack('<3f', 12.0, 4.0, -7.0)
        zero_wire = prefix + b'\0' + bytes.fromhex('00 00 00 00 00 c0')
        packed, used = decode_type27(zero_wire, 12)
        self.assertEqual(used, 7)
        self.assertIsNone(rotation_is_identity(packed, 'type27'))

        for selector, expected in ((1, False), (2, True), (3, False), (25, False)):
            with self.subTest(selector=selector):
                field, used = decode_type27(prefix + bytes((selector,)), 12)
                self.assertEqual(used, 1)
                self.assertIs(rotation_is_identity(field, 'type27'), expected)

    def test_type51_golden_forms(self):
        raw = bytes.fromhex("00 81 80 10 20 30 40 50 60")
        absent, n0 = decode_type51(raw)
        marker, n1 = decode_type51(raw, n0)
        packed, n2 = decode_type51(raw, n0 + n1)
        self.assertIsNone(absent)
        self.assertEqual((marker.selector, marker.value, n1), (0x81, 1, 1))
        self.assertEqual((packed.selector, packed.value, n2),
                         (0x80, bytes.fromhex("10 20 30 40 50 60"), 7))
        self.assertEqual(encode_type51(absent) + encode_type51(marker) + encode_type51(packed), raw)

    def test_type57_golden_opaque_and_marker(self):
        raw = bytes.fromhex("60 aa bb cc dd ee ff 03")
        packed, used = decode_type57(raw)
        marker, n = decode_type57(raw, used)
        self.assertEqual((packed.selector, packed.kind, packed.value, used),
                         (0, "packed-opaque", bytes.fromhex("aa bb cc dd ee ff"), 7))
        self.assertEqual((marker.selector, marker.value, n), (3, 3, 1))
        self.assertEqual(encode_type57(packed, flags=0x60) + encode_type57(marker), raw)

    def test_type57_golden_seven_float_form(self):
        # Independent fixed little-endian fixture: 1.0, -2.0, then 0..4.
        raw = bytes.fromhex("1f 00 00 80 3f 00 00 00 c0 00 00 00 00 00 00 80 3f "
                            "00 00 00 40 00 00 40 40 00 00 80 40")
        field, used = decode_type57(raw)
        self.assertEqual(field.selector, 31)
        self.assertEqual(field.kind, "float32x7")
        self.assertEqual(field.value, (1.0, -2.0, 0.0, 1.0, 2.0, 3.0, 4.0))
        self.assertEqual(used, len(raw))
        self.assertEqual(encode_type57(field), raw)

    def test_rejects_truncation_and_inconsistent_encodes(self):
        for decode, raw in ((decode_type27, b"\0"), (decode_type51, b"\x80"),
                            (decode_type57, b"\0"), (decode_type57, b"\x1f")):
            with self.subTest(decode=decode.__name__, raw=raw), self.assertRaises(RotationCodecError):
                decode(raw)
        with self.assertRaises(RotationCodecError):
            encode_type27(RotationField(0, "packed-opaque", b"short"))
        with self.assertRaises(RotationCodecError):
            encode_type51(RotationField(0x80, "selector", 0))
        with self.assertRaises(RotationCodecError):
            encode_type57(RotationField(31, "float32x7", (0.0,) * 6))

    def test_native_compact_selectors_match_verified_matrix_quaternion_outputs(self):
        cases = (
            (1, 'identity',
             (1., 0., 0., 0., 1., 0., 0., 0., 1.),
             (0., 0., 0., 1.), '0000000000c0'),
            (10, 'yaw_minus_90',
             (0., 0., -1., 0., 1., 0., 1., 0., 0.),
             (0., -0.7071067690849304, 0., 0.7071067690849304), '000001400040'),
            (22, 'yaw_plus_90',
             (0., 0., 1., 0., 1., 0., -1., 0., 0.),
             (0., 0.7071067690849304, 0., 0.7071067690849304), '0000ff3f0040'),
            (13, 'yaw_180',
             (-1., 0., 0., 0., 1., 0., 0., 0., -1.),
             (-0., -1., -0., -0.), '000000000040'),
        )
        for selector, name, matrix, quaternion, wire in cases:
            with self.subTest(selector=selector, native_case=name):
                self.assertEqual(native_compact_selector_to_matrix(selector), matrix)
                actual = native_compact_selector_to_quaternion(selector)
                self.assertEqual(actual, quaternion)
                self.assertEqual(encode_native_quaternion(actual).hex(), wire)

    def test_native_compact_selector_rejects_quaternion_fallback_and_invalid_values(self):
        for selector in (0, 25, 31, -1, True, 1.0, None):
            with self.subTest(selector=selector), self.assertRaises(RotationCodecError):
                native_compact_selector_to_matrix(selector)

    def test_native_compact_selector_identity_mapping(self):
        # Literal selector map from 0x105EAA3E3 in the pinned copied Mach-O.
        mapping = (0, 1, 2, 4, 5, 6, 8, 9, 11, 12, 13, 15, 16,
                   19, 20, 22, 23, 24, 26, 27, 29, 30, 31, 33, 34)
        self.assertEqual(tuple(mapping[i] for i in range(25)),
                         (0, 1, 2, 4, 5, 6, 8, 9, 11, 12, 13, 15, 16,
                          19, 20, 22, 23, 24, 26, 27, 29, 30, 31, 33, 34))

        # Native helper 0x102E8F40C expands packed code 1 as axis indices
        # code//6=0, code%6=1. The initialized native axis table starts
        # (+X,+Y,+Z,-X,-Y,-Z), yielding the exact identity basis.
        axes = ((1, 0, 0), (0, 1, 0), (0, 0, 1),
                (-1, 0, 0), (0, -1, 0), (0, 0, -1))
        code = mapping[1]
        first, second = axes[code // 6], axes[code % 6]
        cross = (first[1]*second[2] - first[2]*second[1],
                 first[2]*second[0] - first[0]*second[2],
                 first[0]*second[1] - first[1]*second[0])
        matrix_columns = (first, second, cross)
        matrix = tuple(matrix_columns[c][r] for r in range(3) for c in range(3))
        self.assertEqual(matrix, (1, 0, 0, 0, 1, 0, 0, 0, 1))

        self.assertIs(rotation_is_identity(RotationField(1, "selector"), "native_physics"), True)
        self.assertIs(rotation_is_identity(RotationField(2, "selector"), "type27"), True)
        self.assertIs(rotation_is_identity(RotationField(1, "selector"), "type27"), False)
        self.assertIsNone(rotation_is_identity(RotationField(0, "packed-opaque", b"123456"), "type27"))
        self.assertIs(rotation_is_identity(RotationField(0x82, "selector", 2), "type51"), True)
        self.assertIs(rotation_is_identity(RotationField(0x81, "selector", 1), "type51"), False)
        self.assertIsNone(rotation_is_identity(RotationField(0x80, "packed-opaque", b"123456"), "type51"))
        self.assertIs(rotation_is_identity(RotationField(1, "selector", 1), "type57"), True)
        self.assertIs(rotation_is_identity(RotationField(2, "selector", 2), "type57"), False)
        self.assertIsNone(rotation_is_identity(RotationField(0, "packed-opaque", b"123456"), "type57"))
        self.assertIsNone(rotation_is_identity(RotationField(31, "float32x7", (0.0,) * 7), "type57"))
        self.assertTrue(all(rotation_is_identity(RotationField(i, "selector"), "native_physics") is False
                            for i in range(2, 25)))
        self.assertIsNone(rotation_is_identity(RotationField(0, "packed-opaque", b"123456"), "native_physics"))
        self.assertIsNone(rotation_is_identity(RotationField(25, "selector", 25), "native_physics"))

    def test_native_quaternion_six_byte_layout(self):
        # Native omitted-component index occupies the high two bits of the
        # second little-endian word. Identity in native four-vector slot 3
        # therefore serializes as 00 00 00 00 00 c0.
        raw = encode_native_quaternion((0.0, 0.0, 0.0, 1.0))
        self.assertEqual(raw, bytes.fromhex("00 00 00 00 00 c0"))
        decoded, used = decode_native_quaternion(raw)
        self.assertEqual(used, 6)
        self.assertEqual(decoded, (0.0, 0.0, 0.0, 1.0))

    def test_native_quaternion_round_trip_all_omitted_slots(self):
        samples = (
            (0.2, -0.3, 0.4, 0.842614977),
            (-0.9, 0.1, 0.2, 0.3),
            (0.1, -0.95, 0.1, 0.2),
            (0.1, 0.2, -0.95, 0.2),
        )
        for sample in samples:
            with self.subTest(sample=sample):
                packed = encode_native_quaternion(sample)
                decoded, used = decode_native_quaternion(packed)
                self.assertEqual(used, 6)
                length = sum(x*x for x in sample) ** 0.5
                magnitude = sum((a/length)*b for a, b in zip(sample, decoded))
                # Encoder flips the quaternion sign to keep the omitted
                # component positive; compare rotations up to quaternion sign.
                self.assertGreater(abs(magnitude), 0.9999)

    def test_native_quaternion_rejects_bad_inputs(self):
        for value in ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0),
                      (0.0, 0.0, 0.0, float("nan"))):
            with self.subTest(value=value), self.assertRaises(RotationCodecError):
                encode_native_quaternion(value)
        with self.assertRaises(RotationCodecError):
            decode_native_quaternion(b"short")

    def test_native_matrix_to_quaternion_row_major_yaw(self):
        # Standard row-major Y rotation used by the native helper's matrix
        # convention: [cos,0,sin; 0,1,0; -sin,0,cos].
        q = native_matrix3x3_to_quaternion((0.0, 0.0, 1.0,
                                            0.0, 1.0, 0.0,
                                            -1.0, 0.0, 0.0))
        self.assertEqual(q, (0.0, 0.7071067690849304, 0.0, 0.7071067690849304))
        identity = native_matrix3x3_to_quaternion((1,0,0, 0,1,0, 0,0,1))
        self.assertEqual(identity, (0.0, 0.0, 0.0, 1.0))

    def test_native_matrix_to_quaternion_rejects_bad_shape_and_nonfinite(self):
        for matrix in ((1, 0, 0), (1, 0, 0, 0, 1, 0, 0, 0, float('nan'))):
            with self.subTest(matrix=matrix), self.assertRaises(RotationCodecError):
                native_matrix3x3_to_quaternion(matrix)


if __name__ == "__main__":
    unittest.main()
