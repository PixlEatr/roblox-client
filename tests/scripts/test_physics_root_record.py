import unittest

from physics_root_record import (
    PhysicsRootRecordError,
    RootRecordEnvelope,
    encode_instance_reference,
    encode_zero_child_no_history_body,
    encode_zero_child_no_history_root_record,
    ZeroChildNoHistoryConditions,
    encode_client_current_body,
    ClientChildBody,
)
class EncodedCurrent:
    """Test double for the separately tested E704 transform+suffix encoder."""

    def __init__(self, payload, velocity_mode='compact'):
        self.payload = payload
        self.state = None
        self.reference_bytes = b'\0'
        self.velocity_mode = velocity_mode

    def encode(self):
        return self.payload


def current():
    return EncodedCurrent(b'\xe1\0')


def conditions():
    return ZeroChildNoHistoryConditions(
        sender_mode=1, history_value=0, motor_records=(), children=(),
        cached_block=None, stream_available=True, record_state=1,
        body_selected=True)


class RootRecordEnvelopeTests(unittest.TestCase):
    def test_dependent_child_bodies_follow_native_preorder_without_root_envelopes(self):
        from physics_history import CurrentRecord
        from physics_packet import TransformHeader
        from physics_rotation import RotationField
        header=TransformHeader(1,True,True,True,0xe1)
        root=CurrentRecord(header,(0.,0.,0.),RotationField(1,'selector',1),None,None,
                           'compact',reference_bytes=b'\0')
        child=CurrentRecord(header,(0.,0.,0.),RotationField(1,'selector',1),None,None,
                            'compact',state=255)
        a,b=encode_instance_reference(1,(1,22)),encode_instance_reference(1,(1,23))
        descendants=(ClientChildBody(a,child,children=(ClientChildBody(b,child),)),)
        encoded=encode_client_current_body(root,d76c_flags=8,child_records=descendants)
        self.assertEqual(encoded,b'\x08\x00\xe1\x00'+a+b'\x00\xe1\xff'+b+b'\x00\xe1\xff\x00')
        for flags,items in ((0x28,descendants),(8,(ClientChildBody(a,root),)),
                            (8,(ClientChildBody(a,child),ClientChildBody(a,child)))):
            with self.subTest(flags=flags),self.assertRaises(PhysicsRootRecordError):
                encode_client_current_body(root,d76c_flags=flags,child_records=items)

    def test_client_role_zero_has_no_history_count_or_time_bytes(self):
        from physics_history import CurrentRecord
        from physics_packet import TransformHeader
        from physics_rotation import RotationField
        record = CurrentRecord(TransformHeader(1,True,True,True,0xe1),
                               (0.,0.,0.), RotationField(1,'selector',1),
                               None,None,'compact',reference_bytes=b'\0')
        self.assertEqual(encode_client_current_body(record,d76c_flags=0),
                         b'\x00\x00\xe1\x00\x00')
        self.assertEqual(encode_client_current_body(record,d76c_flags=0x20),
                         b'\x20\x00\xe1\x00')
        for flags, motors, children in ((0x40,(),()),(0x80,(),()),
                                       (0,(b'motor',),()),(0,(),(b'child',))):
            with self.subTest(flags=flags,motors=motors,children=children):
                with self.assertRaises(PhysicsRootRecordError):
                    encode_client_current_body(record,d76c_flags=flags,
                                               motor_records=motors,child_records=children)

    def test_root_helper_rejects_child_index_suffix(self):
        from physics_history import CurrentRecord
        from physics_packet import TransformHeader
        from physics_rotation import RotationField
        record = CurrentRecord(TransformHeader(1,True,True,True,0xe1),
                               (0.,0.,0.), RotationField(1,'selector',1),
                               None,None,'compact',state=255)
        with self.assertRaises(PhysicsRootRecordError):
            encode_client_current_body(record, d76c_flags=0)
        with self.assertRaises(PhysicsRootRecordError):
            encode_zero_child_no_history_body(record, d76c_flags=0, conditions=conditions())

    def test_proven_write_order_without_optional_blocks(self):
        record = RootRecordEnvelope(b"REF", b"BODY")
        self.assertEqual(record.encode(stream_available=True, record_enabled=True),
                         b"REF\x00\x01BODY")

    def test_cached_and_body_blocks_follow_presence_bytes(self):
        record = RootRecordEnvelope(b"REF", b"BODY", b"CACHE")
        self.assertEqual(record.encode(stream_available=True, record_enabled=True),
                         b"REF\x01CACHE\x01BODY")

    def test_absent_body_emits_false_presence_byte(self):
        record = RootRecordEnvelope(b"REF", None)
        self.assertEqual(record.encode(stream_available=True, record_enabled=True),
                         b"REF\x00\x00")

    def test_native_early_return_gates_fail_closed(self):
        record = RootRecordEnvelope(b"REF", b"BODY")
        for stream, enabled in ((False, True), (True, False)):
            with self.assertRaises(PhysicsRootRecordError):
                record.encode(stream_available=stream, record_enabled=enabled)

    def test_opaque_fields_must_be_bytes(self):
        with self.assertRaises(PhysicsRootRecordError):
            RootRecordEnvelope("REF", b"BODY").encode(
                stream_available=True, record_enabled=True)
        with self.assertRaises(PhysicsRootRecordError):
            RootRecordEnvelope(b"REF", bytearray(b"BODY")).encode(
                stream_available=True, record_enabled=True)

    def test_gate_inputs_must_be_explicit_booleans(self):
        with self.assertRaises(PhysicsRootRecordError):
            RootRecordEnvelope(b"REF", b"BODY").encode(
                stream_available=1, record_enabled=True)

    def test_reference_null_and_scoped_raw_id(self):
        self.assertEqual(encode_instance_reference(17, None), b"\x00")
        self.assertEqual(encode_instance_reference(17, (0, 0x12345678)),
                         b"\x11\x78\x56\x34\x12")
        self.assertEqual(encode_instance_reference(17, (42, 0x12345678)),
                         b"\x2a\x78\x56\x34\x12")
        self.assertEqual(encode_instance_reference(17, (300, 1)),
                         b"\xac\x02\x01\x00\x00\x00")
        self.assertEqual(encode_instance_reference(17, (0xFFFFFFFF, 9)), b"\x00")
        with self.assertRaises(PhysicsRootRecordError):
            encode_instance_reference(-1, (0, 1))
        with self.assertRaises(PhysicsRootRecordError):
            encode_instance_reference(0x1_0000_0000, (0, 1))

    def test_zero_child_nohistory_body_prefix_and_terminator(self):
        self.assertEqual(encode_zero_child_no_history_body(
            current(), d76c_flags=1, conditions=conditions()),
            b"\x01\x00\x01\x00\x00\x00\x00\xe1\x00\x00")

    def test_zero_child_nohistory_velocity_mode_must_match_flag_bit6(self):
        for record_mode, flags in (('compact', 0x41), ('short', 0x01)):
            with self.subTest(record_mode=record_mode, flags=flags):
                with self.assertRaisesRegex(PhysicsRootRecordError, 'velocity codec'):
                    encode_zero_child_no_history_body(
                        EncodedCurrent(b'\xe1\0', velocity_mode=record_mode),
                        d76c_flags=flags, conditions=conditions())

    def test_complete_supported_root_record_envelope(self):
        self.assertEqual(
            encode_zero_child_no_history_root_record(
                17, (0, 0x12345678), current(), d76c_flags=1, conditions=conditions()),
            b"\x11\x78\x56\x34\x12\x00\x01"
            b"\x01\x00\x01\x00\x00\x00\x00\xe1\x00\x00")

    def test_requires_all_supported_branch_facts(self):
        with self.assertRaises(PhysicsRootRecordError):
            encode_zero_child_no_history_body(
                current(), d76c_flags=1,
                conditions=ZeroChildNoHistoryConditions(
                    sender_mode=1, history_value=0, motor_records=(),
                    children=("child",), cached_block=None,
                    stream_available=True, record_state=1, body_selected=True))
        with self.assertRaises(PhysicsRootRecordError):
            encode_zero_child_no_history_body(
                current(), d76c_flags=0x21, conditions=conditions())
        invalid_gate = ZeroChildNoHistoryConditions(
            sender_mode=1, history_value=0, motor_records=(), children=(),
            cached_block=None, stream_available=True, record_state=0,
            body_selected=True)
        with self.assertRaises(PhysicsRootRecordError):
            encode_zero_child_no_history_root_record(
                17, (0, 0x12345678), current(), d76c_flags=1,
                conditions=invalid_gate)


if __name__ == "__main__":
    unittest.main()
