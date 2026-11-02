import hashlib
import struct
import unittest

import zstandard

from game_wire import encode_unsigned
from wire_reader import Reader, WireError, decompress_stream
from world_replication import World
from login_packets import encode_signed
from replication_out import marker_reply, MarkerQueue


class ReaderTests(unittest.TestCase):
    def test_marker_queue_waits_for_world_then_preserves_order(self):
        queue = MarkerQueue()
        world = World({'classes': []})
        queue.add(b'\x84\x01\x00\x00\x00')
        queue.add(b'\x84\x02\x00\x00\x00')
        self.assertEqual(queue.drain(world), [])
        world.client_scope = 1
        self.assertEqual(queue.drain(world), [])
        world.tags.extend([12, 13])
        self.assertEqual(queue.drain(world), [b'\x83\x04\x01\0\0\0\0', b'\x83\x04\x02\0\0\0\0'])
        self.assertEqual(queue.drain(world), [])

    def test_marker_queue_bounds_and_invalid_payload_preserve_pending(self):
        queue = MarkerQueue()
        with self.assertRaises(ValueError): queue.add(b'\x84')
        self.assertEqual(len(queue.pending), 0)
        for _ in range(64): queue.add(b'\x84\0\0\0\0')
        with self.assertRaises(ValueError): queue.add(b'\x84\0\0\0\0')
        self.assertEqual(len(queue.pending), 64)

    def test_replication_marker_echo_and_bounds(self):
        self.assertEqual(marker_reply(b'\x84\x78\x56\x34\x12'), b'\x83\x04\x78\x56\x34\x12\x00')
        for payload in (b'', b'\x84\x00', b'\x84'+bytes(5), b'\x83'+bytes(4)):
            with self.assertRaises(ValueError): marker_reply(payload)

    def test_truncated_and_overflow_fields(self):
        for value in (0, 127, 128, 65535, 2**32 - 1):
            encoded = encode_unsigned(value)
            self.assertEqual(Reader(encoded).varuint(), value)
            for size in range(len(encoded)):
                with self.assertRaises(WireError): Reader(encoded[:size]).varuint()
        for data in (b'\x80'*5, b'\xff'*4+b'\x10'):
            with self.assertRaises(WireError): Reader(data).varuint()
        with self.assertRaises(WireError): Reader(b'abc').take(4)

    def test_zstd_size_bounds_and_extra_frame(self):
        raw = b'world fixture' * 100
        compressed = zstandard.ZstdCompressor().compress(raw)
        frame = struct.pack('<II', len(compressed), len(raw)) + compressed
        self.assertEqual(decompress_stream(Reader(frame)).take(len(raw)), raw)
        with self.assertRaises(WireError):
            decompress_stream(Reader(struct.pack('<II', len(compressed), len(raw)-1) + compressed))
        with self.assertRaises(WireError):
            decompress_stream(Reader(struct.pack('<II', 1, 2**31) + b'x'))
        doubled = compressed + compressed
        with self.assertRaises((WireError, zstandard.ZstdError)):
            decompress_stream(Reader(struct.pack('<II', len(doubled), len(raw)) + doubled))


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.w = World({'classes': [{'name': n} for n in ('Player', 'Model', 'Part', 'Humanoid', 'Workspace')]})

    def test_shared_content_integrity(self):
        raw = b'public synthetic fixture'
        key = hashlib.blake2b(raw, digest_size=16).digest()
        packet = key + b'\0' + encode_unsigned(len(raw)) + raw
        self.assertEqual(self.w.shared_entry(Reader(packet)), key)
        self.assertEqual(self.w.shared_entry(Reader(key+b'\1')), key)
        with self.assertRaises(WireError): self.w.shared_entry(Reader(packet[:-1]+b'!'))
        with self.assertRaises(WireError): self.w.shared_entry(Reader(bytes(16)+b'\1'))

    def test_globals_validate_all_containers_before_committing(self):
        data = b'\x81'+bytes(6)+b'\x01\x7b\x01\x01'+struct.pack('<IH', 5, 4)+b'\x01\x00'
        for size in range(len(data)):
            with self.assertRaises(WireError): self.w.globals(data[:size])
            self.assertIsNone(self.w.client_scope)
            self.assertFalse(self.w.instances)
        self.w.globals(data)
        self.assertEqual(self.w.client_scope, 123)
        self.assertEqual(self.w.global_flags[6], 1)
        self.assertEqual(self.w.instances[(1, 5)]['class'], 4)

    def avatar_fixture(self):
        keys = [(1, i) for i in range(5)]
        player, model, root, humanoid, workspace = keys
        self.w.instances = {
            player: {'class': 0, 'properties': {'UserId': 123, 'Character': model}},
            model: {'class': 1, 'properties': {'PrimaryPart': root}, 'parent': workspace},
            root: {'class': 2, 'properties': {'Name': b'HumanoidRootPart', 'CFrame': {'position': [1., 2., 3.]}}, 'parent': model},
            humanoid: {'class': 3, 'properties': {}, 'parent': model},
            workspace: {'class': 4, 'properties': {}},
        }
        return keys

    def test_spawn_requires_identity_references_and_world_root(self):
        keys = self.avatar_fixture()
        self.assertTrue(self.w.avatar(123)['evidence']['player_spawned'])
        self.assertFalse(self.w.avatar(456)['evidence']['player_spawned'])
        for key in keys[1:]:
            saved = self.w.instances.pop(key)
            self.assertFalse(self.w.avatar(123)['evidence']['player_spawned'])
            self.w.instances[key] = saved
        self.w.instances[keys[2]]['properties']['CFrame']['position'][0] = float('nan')
        self.assertFalse(self.w.avatar(123)['evidence']['player_spawned'])

    def test_removed_character_invalidates_spawn(self):
        keys = self.avatar_fixture()
        self.w.remove_tree(keys[1])
        self.assertFalse(self.w.avatar(123)['evidence']['player_spawned'])
        self.assertEqual(set(self.w.instances), {keys[0], keys[4]})

    def test_nonstreaming_world_requires_complete_load_and_unanchored_root(self):
        keys = self.avatar_fixture()
        self.w.instances[keys[4]]['properties']['StreamingEnabled'] = False
        self.w.instances[keys[2]]['properties']['Anchored'] = False
        self.assertFalse(self.w.avatar(123)['evidence']['movement_ready'])
        self.w.tags.append(13)
        evidence = self.w.avatar(123)['evidence']
        self.assertTrue(evidence['movement_ready'])
        self.assertFalse(evidence['spawn_region_received'])
        self.w.instances[keys[2]]['properties']['Anchored'] = True
        self.assertFalse(self.w.avatar(123)['evidence']['movement_ready'])
        self.w.instances[keys[3]]['properties']['Health'] = float('nan')
        self.assertFalse(self.w.avatar(123)['evidence']['player_spawned'])

    def test_streaming_world_requires_received_spawn_region_inside_radius(self):
        keys = self.avatar_fixture()
        self.w.instances[keys[4]]['properties']['StreamingEnabled'] = True
        self.w.instances[keys[2]]['properties']['Anchored'] = False
        self.w.tags.append(13)
        self.assertFalse(self.w.avatar(123)['evidence']['movement_ready'])
        self.w.regions.append({'coordinate': [0, 0, 0], 'radius': 1})
        self.w.received_regions.add((0, 0, 0))
        self.assertTrue(self.w.avatar(123)['evidence']['movement_ready'])
        self.w.instances[keys[2]]['properties']['CFrame']['position'] = [64., 2., 3.]
        self.assertFalse(self.w.avatar(123)['evidence']['movement_ready'])

    def test_content_id_uses_compression_only_in_selected_contexts(self):
        w = World({'strings': ['', 'rbxassetid://'], 'classes': [], 'properties': []})
        prop = {'type': 33, 'name': 'Asset'}
        self.assertEqual(w.value(Reader(b'\x03' + encode_signed(123)), prop, set(), compressed=True),
                         b'rbxassetid://123')
        self.assertEqual(w.value(Reader(b'\x02\x03abc'), prop, set(), compressed=True),
                         b'rbxassetid://abc')
        self.assertEqual(w.value(Reader(b'\x03abc'), prop, set(), compressed=False), b'abc')
        with self.assertRaises(WireError):
            w.value(Reader(b'\x04\x00'), prop, set(), compressed=True)
        with self.assertRaises(WireError):
            w.value(Reader(b'\x02\x04abc'), prop, set(), compressed=True)

    def test_new_instance_two_lists_have_different_content_encodings(self):
        w = World({'strings': ['', 'rbxassetid://'],
                   'classes': [{'name': 'Synthetic', 'properties': [0, 1]}],
                   'properties': [{'name': 'First', 'type': 33}, {'name': 'Second', 'type': 33}]})
        header = b'\x01' + struct.pack('<IH', 2, 0)
        data = header + b'\x00\x03' + encode_signed(7) + b'\xff\x01\x03abc\xff\x00'
        r = Reader(data)
        instance = w.new_instance(r)
        r.finish()
        self.assertEqual(instance['properties'], {'First': b'rbxassetid://7', 'Second': b'abc'})


if __name__ == '__main__': unittest.main()
