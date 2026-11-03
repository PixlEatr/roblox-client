import copy
import struct
import unittest

from isr_replication import InstanceStateReceiver, decode_packet, decode_short_quaternion
from owned_part_poses import _velocities
from physics_velocity import decode_native_short_velocity
from wire_reader import WireError
from world_replication import World
from world_snapshot import _node_snapshot


# Isolated PV bodies from the 150-second capture. No account/session credentials.
BODIES = {
    0: bytes.fromhex('00452ed1cd4a5261ac7d4c07d3c030fe5f7bf53806803e81'),
    1: bytes.fromhex('006127c8d8b9573d1d5e8100b2016b419eae89fdae3f'),
    2: bytes.fromhex('0060a700730dab0ade19d34260bd9cfb01903fa5'),
}


def fixture():
    props = [{'id': 0, 'name': 'PhysicsRepRootPart', 'type': 28},
             {'id': 1, 'name': 'PhysicsRepRootRef', 'type': 69},
             {'id': 2, 'name': 'ReplicationPV', 'type': 57}]
    world = World({'classes': [{'name': 'Part', 'properties': [0, 1, 2],
                               'isr_properties': [0, 1, 2]}], 'properties': props})
    world.instances[(1, 42)] = {'class': 0, 'properties': {}, 'parent': None}
    return world


def packet(lod=0, *, timestamp=100, body=None, key=42, mask=4, count=1):
    if body is None:
        body = BODIES[min(lod, 2)]
    header = b'\xa1' + struct.pack('<IIiHBB', timestamp, 1, 0x7fffffff, count, lod, 0)
    record = b'\x01' + struct.pack('<IHHQH', key, 0, 3, mask, len(body)) + body
    return header + record * count


class IsrReplicationTests(unittest.TestCase):
    def test_all_four_lods_consume_captured_bodies_and_export_runtime_values(self):
        for lod in range(4):
            with self.subTest(lod=lod):
                world = fixture()
                receiver = InstanceStateReceiver(world, legacy_pv_enabled=True)
                self.assertTrue(receiver.receive(packet(lod)))
                self.assertEqual(receiver.summary()['records_applied'], 1)
                node = _node_snapshot(world, (1, 42), world.instances[(1, 42)])
                frame = node['properties']['CFrame']['$cframe']
                self.assertEqual(len(frame['rotation']), 9)
                self.assertEqual(frame['lod'], lod)
                self.assertEqual(node['origins']['CFrame'], 'derived_received')
                for name in ('Position', 'AssemblyLinearVelocity', 'AssemblyAngularVelocity'):
                    self.assertEqual(len(node['properties'][name]['$vector3']), 3)
                self.assertEqual(world.actions[-1]['kind'], 'set')

    def test_truncation_and_trailing_data_never_commit_partial_updates(self):
        raw = packet(count=2)
        for malformed in [raw[:i] for i in range(len(raw))] + [raw+b'\0']:
            world = fixture()
            receiver = InstanceStateReceiver(world, legacy_pv_enabled=True)
            self.assertFalse(receiver.receive(malformed))
            self.assertEqual(world.revision, 0)
            self.assertEqual(world.instances[(1, 42)]['properties'], {})

    def test_body_length_is_validated_separately_from_next_record(self):
        with self.assertRaises(WireError):
            decode_packet(packet(body=BODIES[0]+b'\0'), fixture().schema, legacy_pv_enabled=True)

    def test_schema_dirty_bits_and_lod_are_bounded(self):
        for raw in (packet(mask=8), packet(lod=4)):
            with self.assertRaises(WireError):
                decode_packet(raw, fixture().schema, legacy_pv_enabled=True)

    def test_unresolved_instances_are_not_created(self):
        world = fixture()
        receiver = InstanceStateReceiver(world, legacy_pv_enabled=True)
        self.assertTrue(receiver.receive(packet(key=99)))
        self.assertNotIn((1, 99), world.instances)
        self.assertEqual(receiver.summary()['unresolved_records'], 1)

    def test_stale_packets_do_not_overwrite_and_timestamp_rollover_is_supported(self):
        world = fixture()
        receiver = InstanceStateReceiver(world, legacy_pv_enabled=True)
        for stamp in (0xfffffff0, 5):
            self.assertTrue(receiver.receive(packet(timestamp=stamp)))
        revision = world.revision
        self.assertTrue(receiver.receive(packet(timestamp=0xfffffff1)))
        self.assertEqual(world.revision, revision)
        self.assertEqual(receiver.summary()['stale_packets_ignored'], 1)

    def test_feature_gate_does_not_guess_adaptive_format(self):
        world = fixture()
        receiver = InstanceStateReceiver(world, legacy_pv_enabled=False)
        self.assertFalse(receiver.receive(packet()))
        self.assertEqual(world.revision, 0)

    def test_root_reference_fields_use_schema_and_allow_null(self):
        world = fixture()
        receiver = InstanceStateReceiver(world, legacy_pv_enabled=False)
        self.assertTrue(receiver.receive(packet(mask=3, body=b'\1'+struct.pack('<I', 17)+b'\0')))
        self.assertEqual(world.instances[(1, 42)]['properties'],
                         {'PhysicsRepRootPart': (1, 17), 'PhysicsRepRootRef': None})

    def test_short_velocity_keeps_its_codec_when_reused_by_authored_poses(self):
        world = fixture()
        receiver = InstanceStateReceiver(world, legacy_pv_enabled=True)
        receiver.receive(packet(1))
        props = world.instances[(1, 42)]['properties']
        vectors, source = _velocities(props)
        self.assertEqual(source, 'received_replication_pv')
        for vector, packed in zip(vectors, props['ReplicationPV']['packed_velocities']):
            raw = bytes((packed[0],))+struct.pack('<H', packed[1])
            self.assertEqual(vector, decode_native_short_velocity(raw).vector)
        bad = copy.deepcopy(props)
        bad['ReplicationPV']['packed_velocities'][0][1] = 65536
        with self.assertRaises(ValueError):
            _velocities(bad)

    def test_zero_short_quaternion_and_truncation(self):
        for omitted in range(4):
            expected = [0., 0., 0., 0.]
            expected[omitted] = 1.
            self.assertEqual(decode_short_quaternion(struct.pack('<I', omitted << 30)), tuple(expected))
        with self.assertRaises(WireError):
            decode_short_quaternion(b'\0' * 3)


if __name__ == '__main__':
    unittest.main()
