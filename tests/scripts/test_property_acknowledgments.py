import struct
import unittest

from property_acknowledgments import PropertyAcknowledgments, encode_property_ack
from replication_out import guid
from wire_reader import Reader, WireError
from world_replication import World


KEY = (123, 456)
SCHEMA = {'classes': [{'name': 'Part', 'unknown_u16': 0}],
          'properties': [{'name': 'CFrame', 'type': 27}, {'name': 'Anchored', 'type': 9}],
          'parent_property_id': 2}


def change(reset=0, *, pi=0, key=KEY):
    body = struct.pack('<fffB', 1, 2, 3, 1) if pi == 0 else b'\x01'
    return b'\x03'+guid(key)+struct.pack('<HB', pi, reset)+body


def decode(payload):
    r = Reader(payload)
    assert r.take(2) == b'\x83\x0a'
    key, pi, present = (r.varuint(), r.u32()), r.u16(), r.u8()
    version = r.varuint() if present else None
    step = r.u32()
    assert r.u8() == 0
    r.finish()
    return key, pi, version, step


class PropertyAcknowledgmentTests(unittest.TestCase):
    def setUp(self):
        self.world = World(SCHEMA)
        self.world.instances[KEY] = {'class': 0, 'properties': {}}
        self.queue = PropertyAcknowledgments(self.world)
        self.sent = []

    def receive(self, *items, step=17):
        self.world.packet(b'\x83'+b''.join(items)+b'\0')
        self.queue.observe(local_step=step)

    def flush(self):
        self.queue.flush(lambda channel, payload: self.sent.append((channel, decode(payload))))

    def test_native_item_layout_and_varuint_boundaries(self):
        for version in (0, 1, 127, 128, 65535, 0xffffffff):
            self.assertEqual(decode(encode_property_ack(KEY, 23, version, 240)),
                             (KEY, 23, version, 240))
        self.assertEqual(encode_property_ack((1, 2), 3, 128, 240).hex(),
                         '830a01020000000300018001f000000000')

    def test_reset_then_increment_and_no_repeat_without_receive(self):
        self.receive(change(1)); self.flush()
        self.receive(change()); self.flush(); self.flush()
        self.queue.observe(local_step=18); self.flush()
        self.assertEqual([v[1][2] for v in self.sent], [0, 1])
        self.assertEqual(self.queue.summary()['received_resets'], 1)

    def test_coalesce_current_receipts_and_preserve_real_step(self):
        self.receive(change(), change(), step=500)
        self.flush()
        self.assertEqual(self.sent, [(1, (KEY, 0, 2, 500))])
        self.receive(change(1), change(), step=501); self.flush()
        self.assertEqual(self.sent[-1], (1, (KEY, 0, 1, 501)))

    def test_invalid_packet_never_publishes_receipt(self):
        for payload in (b'\x83'+change(), b'\x83'+change()+b'\xff',
                        b'\x83'+change()+b'\0extra', b'\x83'+change(2)+b'\0'):
            with self.assertRaises(WireError): self.world.packet(payload)
            self.queue.observe(local_step=0); self.flush()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.queue.versions, {})

    def test_no_ack_for_unrelated_missing_or_local_changes(self):
        self.receive(change(pi=1), change(key=(999, 123)))
        self.world.instances[KEY]['properties']['CFrame'] = {'position': [8, 9, 10]}
        self.world.touch(KEY, ['CFrame']); self.flush()
        self.assertEqual(self.sent, [])

    def test_removal_cancels_ack_and_recreation_resets_identity(self):
        self.receive(change())
        self.world.remove_tree(KEY); self.flush()
        self.assertEqual(self.sent, [])
        self.world.instances[KEY] = {'class': 0, 'properties': {}}
        self.receive(change()); self.flush()
        self.assertEqual(self.sent[-1][1][2], 1)

    def test_new_session_does_not_reuse_prior_versions(self):
        self.receive(change(), change()); self.flush()
        other = World(SCHEMA)
        other.instances[KEY] = {'class': 0, 'properties': {}}
        queue = PropertyAcknowledgments(other)
        other.packet(b'\x83'+change()+b'\0'); queue.observe(local_step=1)
        sent = []; queue.flush(lambda ch, p: sent.append(decode(p)))
        self.assertEqual(sent, [(KEY, 0, 1, 1)])

    def test_failed_send_is_not_retried(self):
        self.receive(change())
        def fail(*args): raise OSError('test')
        with self.assertRaises(OSError): self.queue.flush(fail)
        self.flush()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.queue.error, 'transport_submission_ambiguous_no_retry')

    def test_bound_and_counter_overflow(self):
        self.queue.max_entries = 1
        self.receive(change())
        self.queue.versions[(KEY, 0)] = (self.world.instances[KEY], 0xffffffff)
        self.receive(change()); self.flush()
        self.assertEqual(self.sent[-1][1][2], 0)
        second = (123, 457)
        self.world.instances[second] = {'class': 0, 'properties': {}}
        with self.assertRaisesRegex(ValueError, 'Too many'):
            self.receive(change(key=second))

    def test_encoder_rejects_nonlocal_or_invalid_clock_values(self):
        for step in (None, -1, 2**32, 1.5, True):
            with self.assertRaises(ValueError): encode_property_ack(KEY, 0, 1, step)

    def test_batches_distinct_properties_and_coalesces_between_ticks(self):
        second = (123, 457)
        self.world.instances[second] = {'class': 0, 'properties': {}}
        self.receive(change(), change(key=second), step=5)
        self.receive(change(), step=6)
        packets = []
        self.queue.flush(lambda ch, p: packets.append(p))
        self.assertEqual(packets, [b'\x83'+encode_property_ack(KEY, 0, 2, 6)[1:-1]
                                 +encode_property_ack(second, 0, 1, 5)[1:-1]+b'\0'])
        self.assertEqual(self.queue.sent, 2)
        self.assertEqual(self.queue.packets_sent, 1)


if __name__ == '__main__': unittest.main()
