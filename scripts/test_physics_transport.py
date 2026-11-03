import unittest

from physics_transport import PhysicsTransportAdapter
from physics_transport_mapping import candidate_mapping, map_packet_family
from unreliable import parse_datagram


class PhysicsTransportAdapterTests(unittest.TestCase):
    def make_adapter(self, **kwargs):
        return PhysicsTransportAdapter(
            4, 7, 101, evidence="synthetic fixture channel binding", **kwargs)

    def test_requires_explicit_binding_and_evidence(self):
        with self.assertRaises(TypeError):
            PhysicsTransportAdapter()
        with self.assertRaises(ValueError):
            PhysicsTransportAdapter(4, 7, 101, evidence=" ")
        with self.assertRaises(ValueError):
            PhysicsTransportAdapter(4, 32, 101, evidence="fixture")

    def test_stages_fifo_monotonic_frames_and_decodes_them(self):
        adapter = self.make_adapter()
        self.assertEqual(adapter.stage(b"first"), 1)
        self.assertEqual(adapter.stage(b"second"), 2)
        self.assertEqual(len(adapter), 2)
        first = adapter.drain(1)
        second = adapter.drain()
        self.assertEqual([item.sequence for item in first + second], [1, 2])
        for item, expected in zip(first + second, (b"first", b"second")):
            self.assertEqual((item.application, item.channel, item.wire_id), (4, 7, 101))
            parsed = parse_datagram(item.encoded)
            self.assertEqual(parsed["wire_id"], item.wire_id)
            self.assertEqual(parsed["sequence"], item.sequence)
            self.assertEqual(parsed["payload"], expected)
            self.assertTrue(parsed["complete"])
        self.assertEqual(len(adapter), 0)

    def test_rejected_payloads_do_not_consume_sequence_or_capacity(self):
        adapter = self.make_adapter(max_payload=3, max_queue=1)
        with self.assertRaises(TypeError):
            adapter.stage("not bytes")
        with self.assertRaises(ValueError):
            adapter.stage(b"four")
        self.assertEqual(len(adapter), 0)
        self.assertEqual(adapter.stage(b"ok"), 1)
        with self.assertRaises(BufferError):
            adapter.stage(b"x")
        self.assertEqual(adapter.drain()[0].sequence, 1)
        self.assertEqual(adapter.stage(b"x"), 2)

    def test_limits_are_bounded_and_drain_validates_limit(self):
        with self.assertRaises(ValueError):
            self.make_adapter(max_payload=1101)
        with self.assertRaises(ValueError):
            self.make_adapter(max_queue=4097)
        adapter = self.make_adapter()
        for limit in (True, -1, 1.5):
            with self.assertRaises(ValueError):
                adapter.drain(limit)


class PhysicsTransportMappingTests(unittest.TestCase):
    def test_physics_packet_family_returns_unverified_candidate(self):
        mapped = map_packet_family(0x85)
        self.assertEqual(mapped, candidate_mapping())
        self.assertEqual(mapped["channel_index"], 6)
        self.assertEqual(mapped["status"], "unverified_candidate")
        self.assertIsNone(mapped["effective_mode"])
        self.assertIsNone(mapped["unreliable_open_control_expected"])

    def test_unknown_and_malformed_packet_families_fail_closed(self):
        for packet_family in (0x84, 0x86, -1, 256, True, 133.0, "133", None):
            with self.subTest(packet_family=packet_family):
                self.assertIsNone(map_packet_family(packet_family))


if __name__ == "__main__":
    unittest.main()
