import unittest

from physics_packet import (
    PhysicsPacketError,
    TransformHeader,
    decode_transform_component,
    decode_transform_header,
    encode_transform_component,
    encode_transform_header,
    encode_physics_message,
)
from physics_header import LAYOUT_SEQUENCE_THEN_TAG
from physics_rotation import RotationField
from physics_wire import velocity as encode_velocity
from physics_velocity import encode_native_short_velocity


class TransformHeaderTests(unittest.TestCase):
    def test_all_header_bytes_round_trip(self):
        for raw in range(256):
            with self.subTest(raw=raw):
                fields, used = decode_transform_header(bytes((raw,)))
                self.assertEqual(used, 1)
                self.assertEqual(fields.rotation_selector, raw & 0x1F)
                self.assertEqual(fields.position_is_zero, bool(raw & 0x80))
                self.assertEqual(fields.linear_velocity_omitted, bool(raw & 0x40))
                self.assertEqual(fields.angular_velocity_omitted, bool(raw & 0x20))
                self.assertEqual(fields.raw, raw)
                self.assertEqual(
                    encode_transform_header(
                        rotation_selector=fields.rotation_selector,
                        position_is_zero=fields.position_is_zero,
                        linear_velocity_omitted=fields.linear_velocity_omitted,
                        angular_velocity_omitted=fields.angular_velocity_omitted,
                    ),
                    bytes((raw,)),
                )

    def test_decode_at_offset_does_not_consume_payload(self):
        fields, used = decode_transform_header(b"\xaa\xd5payload", 1)
        self.assertEqual(used, 1)
        self.assertEqual(fields.raw, 0xD5)
        self.assertEqual(fields.rotation_selector, 0x15)
        self.assertTrue(fields.position_is_zero)
        self.assertTrue(fields.linear_velocity_omitted)
        self.assertFalse(fields.angular_velocity_omitted)

    def test_rejects_truncated_or_invalid_input(self):
        for data, offset in ((b"", 0), (b"\0", -1), (b"\0", 1), (b"\0", 0.5), (bytearray(b"\0"), 0)):
            with self.subTest(data=data, offset=offset), self.assertRaises(PhysicsPacketError):
                decode_transform_header(data, offset)
        for selector in (-1, 32, True, 1.0):
            with self.subTest(selector=selector), self.assertRaises(PhysicsPacketError):
                encode_transform_header(
                    rotation_selector=selector,
                    position_is_zero=False,
                    linear_velocity_omitted=False,
                    angular_velocity_omitted=False,
                )
        with self.assertRaises(PhysicsPacketError):
            encode_transform_header(
                rotation_selector=0,
                position_is_zero=1,
                linear_velocity_omitted=False,
                angular_velocity_omitted=False,
            )

    def test_compact_component_round_trip(self):
        cases = (
            (
                TransformHeader(1, False, False, False, 0x01),
                (1.25, -2.5, 3.75),
                RotationField(1, "selector", 1),
                (0.25, -0.5, 1.0),
                (0.0, 0.0, 0.0),
            ),
            (
                TransformHeader(1, True, True, True, 0xE1),
                None,
                RotationField(1, "selector", 1),
                None,
                None,
            ),
        )
        for header, pos, rot, linear, angular in cases:
            with self.subTest(selector=header.rotation_selector):
                encoded = encode_transform_component(
                    header=header,
                    position_value=pos,
                    rotation=rot,
                    linear_velocity=linear,
                    angular_velocity=angular,
                    velocity_mode="compact",
                )
                decoded = decode_transform_component(encoded, velocity_mode="compact")
                self.assertEqual(decoded.header, header)
                self.assertEqual(decoded.rotation.selector, rot.selector)
                self.assertEqual(decoded.rotation.kind, rot.kind)
                if rot.kind == "native-quaternion":
                    self.assertEqual(decoded.rotation.kind, "native-quaternion")
                    self.assertLess(max(abs(a-b) for a, b in zip(decoded.rotation.value, rot.value)), 0.0002)
                else:
                    self.assertEqual(decoded.rotation.value, rot.value)
                self.assertEqual(decoded.linear_velocity is None, linear is None)
                self.assertEqual(decoded.angular_velocity is None, angular is None)
                if linear is not None:
                    self.assertEqual(decoded.linear_velocity.packed, encode_velocity(linear))
                if angular is not None:
                    self.assertEqual(decoded.angular_velocity.packed, encode_velocity(angular))
                if pos is None:
                    self.assertEqual(decoded.position, (0.0, 0.0, 0.0))
                else:
                    self.assertLess(max(abs(a-b) for a, b in zip(decoded.position, pos)), 0.1)
                self.assertEqual(decoded.consumed, len(encoded))

    def test_short_velocity_component_round_trip_and_boundaries(self):
        header = TransformHeader(1, True, False, False, 0x81)
        rotation = RotationField(1, "selector", 1)
        vectors = ((0.0, 0.0, 0.0), (1.0, -2.0, 3.0))
        angular = (-100.0, 25.5, 0.2)
        encoded = encode_transform_component(
            header=header,
            position_value=None,
            rotation=rotation,
            linear_velocity=vectors[0],
            angular_velocity=angular,
            velocity_mode="short",
        )
        expected = (bytes((header.raw,))
                    + encode_native_short_velocity(vectors[0])
                    + encode_native_short_velocity(angular))
        self.assertEqual(encoded, expected)
        decoded = decode_transform_component(encoded, velocity_mode="short")
        self.assertEqual(decoded.consumed, len(encoded))
        self.assertEqual(decoded.linear_velocity.packed,
                         encode_native_short_velocity(vectors[0]))
        self.assertTrue(decoded.linear_velocity.zero_sentinel)
        self.assertEqual(decoded.angular_velocity.packed,
                         encode_native_short_velocity(angular))
        self.assertFalse(decoded.angular_velocity.zero_sentinel)
        with self.assertRaises(PhysicsPacketError):
            decode_transform_component(encoded[:-1], velocity_mode="short")

    def test_short_velocity_mode_respects_omission_bits(self):
        header = TransformHeader(1, True, True, True, 0xE1)
        encoded = encode_transform_component(
            header=header,
            position_value=None,
            rotation=RotationField(1, "selector", 1),
            linear_velocity=None,
            angular_velocity=None,
            velocity_mode="short",
        )
        decoded = decode_transform_component(encoded, velocity_mode="short")
        self.assertEqual(encoded, bytes((header.raw,)))
        self.assertIsNone(decoded.linear_velocity)
        self.assertIsNone(decoded.angular_velocity)
        self.assertEqual(decoded.consumed, 1)

    def test_message_framing_appends_records_then_null_reference(self):
        encoded = encode_physics_message(0x0102030405060708, 9, (b"ROOT",),
                                         layout=LAYOUT_SEQUENCE_THEN_TAG)
        self.assertEqual(encoded[:18],
                         b"\x1b\x08\x07\x06\x05\x04\x03\x02\x01"
                         b"\x09\x00\x00\x00\x00\x00\x00\x00\x85")
        self.assertEqual(encoded[18:], b"ROOT\x00")

    def test_message_composes_supported_zero_child_root_record(self):
        from physics_history import CurrentRecord
        from physics_root_record import (
            ZeroChildNoHistoryConditions,
            encode_zero_child_no_history_root_record,
        )

        current = CurrentRecord(
            TransformHeader(1, True, True, True, 0xE1), (0, 0, 0),
            RotationField(1, "selector", 1), None, None, "compact",
            reference_bytes=b"\x00")
        conditions = ZeroChildNoHistoryConditions(
            sender_mode=1, history_value=0, motor_records=(), children=(),
            cached_block=None, stream_available=True, record_state=1,
            body_selected=True)
        # A nonzero first reference word selects its own ULEB scope/tag; the
        # context fallback is used only for a zero first word.
        root = encode_zero_child_no_history_root_record(
            17, (23, 0x12345678), current, d76c_flags=1, conditions=conditions)
        message = encode_physics_message(10, 2, (root,),
                                         layout=LAYOUT_SEQUENCE_THEN_TAG)
        self.assertTrue(message.endswith(
            b"\x17\x78\x56\x34\x12\x00\x01\x01\x00"
            b"\x01\x00\x00\x00\x00\xe1\x00\x00\x00"))

    def test_message_framing_requires_explicit_root_record_bytes_and_layout(self):
        with self.assertRaises(PhysicsPacketError):
            encode_physics_message(1, 2, (), layout="")
        with self.assertRaises(PhysicsPacketError):
            encode_physics_message(1, 2, (bytearray(b"R"),),
                                   layout=LAYOUT_SEQUENCE_THEN_TAG)

    def test_outbound_selector31_uses_six_byte_native_quaternion(self):
        header = TransformHeader(31, True, True, True, 0xFF)
        encoded = encode_transform_component(
            header=header,
            position_value=None,
            rotation=RotationField(31, "native-quaternion", (1.0, 0.0, 0.0, 0.0)),
            linear_velocity=None,
            angular_velocity=None,
            velocity_mode="compact",
        )
        self.assertEqual(len(encoded), 7)  # flags byte + six-byte quaternion
        decoded = decode_transform_component(encoded, velocity_mode="compact")
        self.assertEqual(decoded.rotation.kind, "native-quaternion")
        self.assertLess(max(abs(a-b) for a, b in zip(decoded.rotation.value, (1.0, 0.0, 0.0, 0.0))), 0.0002)
        with self.assertRaises(PhysicsPacketError):
            encode_transform_component(
                header=header,
                position_value=None,
                rotation=RotationField(31, "float32x7", (1.0,) * 7),
                linear_velocity=None,
                angular_velocity=None,
                velocity_mode="compact",
            )

    def test_outbound_selector0_uses_native_quaternion_codec(self):
        header = TransformHeader(0, True, True, True, 0xE0)
        encoded = encode_transform_component(
            header=header,
            position_value=None,
            rotation=RotationField(0, "native-quaternion", (0.0, 0.0, 0.0, 1.0)),
            linear_velocity=None,
            angular_velocity=None,
            velocity_mode="compact",
        )
        self.assertEqual(len(encoded), 7)
        with self.assertRaises(PhysicsPacketError):
            encode_transform_component(
                header=header,
                position_value=None,
                rotation=RotationField(0, "packed-opaque", b"abcdef"),
                linear_velocity=None,
                angular_velocity=None,
                velocity_mode="compact",
            )

    def test_component_rejects_unmapped_rotation_and_alternate_velocity_mode(self):
        with self.assertRaises(PhysicsPacketError):
            decode_transform_component(b"\x99", velocity_mode="compact")
        with self.assertRaises(PhysicsPacketError):
            decode_transform_component(b"\x01", velocity_mode="high")
        with self.assertRaises(PhysicsPacketError):
            encode_transform_component(
                header=TransformHeader(25, True, True, True, 0xF9),
                position_value=None,
                rotation=RotationField(25, "selector", 25),
                linear_velocity=None,
                angular_velocity=None,
                velocity_mode="compact",
            )


if __name__ == "__main__":
    unittest.main()
