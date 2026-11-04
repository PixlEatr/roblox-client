import collections
import copy
import json
import unittest

from replication_rpc import FrameworkInitRpc
from world_replication import World


def fixture():
    classes = ['ReplicatedStorage', 'Folder', 'Folder', 'RemoteFunction', 'Players', 'Player']
    schema = {'classes': [{'id': i, 'name': n, 'events': []} for i, n in enumerate(classes)],
              'events': []}
    for name, types in [('RemoteOnInvokeClient', [10, 29]),
                        ('RemoteOnInvokeError', [10, 2]),
                        ('RemoteOnInvokeServer', [10, 28, 29]),
                        ('RemoteOnInvokeSuccess', [10, 29])]:
        i = len(schema['events'])
        schema['events'].append({'id': i, 'class': 3, 'name': name,
                                 'arguments': [{'type': t} for t in types]})
        schema['classes'][3]['events'].append(i)
    world = World(schema)
    keys = [(0x123, i) for i in (1, 2, 3, 0x456, 4, 0x789)]
    world.instances = {
        keys[0]: {'class': 0, 'properties': {}, 'parent': None},
        keys[1]: {'class': 1, 'properties': {'Name': b'Fixture'}, 'parent': keys[0]},
        keys[2]: {'class': 2, 'properties': {'Name': b'Network'}, 'parent': keys[1]},
        keys[3]: {'class': 3, 'properties': {'Name': b'Initialize'}, 'parent': keys[2]},
        keys[4]: {'class': 4, 'properties': {}, 'parent': None},
        keys[5]: {'class': 5, 'properties': {'UserId': 123}, 'parent': keys[4]},
    }
    world.top_containers = {keys[0]: {}, keys[4]: {}}
    world.client_scope = 999
    world.tags = [12, 13]
    spec = {'method': 'InvokeServer', 'arguments': [], 'receiver': [999, 999],
            'receiver_path_classes': classes[:4],
            'receiver_path_names': ['Fixture', 'Network', 'Initialize']}
    return world, spec, keys


class RpcTests(unittest.TestCase):
    def test_native_layout_and_fresh_resolution(self):
        world, spec, keys = fixture()
        rpc = FrameworkInitRpc(spec, 123)
        payload = rpc.prepare(world)
        # Native item7/GUID/LE16 event/int32 zigzag1/GUID/emptyTuple/end.
        self.assertEqual(payload, bytes.fromhex('8307a30256040000020002a302890700000000'))
        received = World(copy.deepcopy(world.schema))
        received.packet(payload)
        self.assertEqual(list(received.events), [{'key': keys[3], 'event': 2,
                                                 'arguments': [1, keys[5], []]}])
        self.assertIsNone(rpc.prepare(world))
        self.assertEqual(rpc.summary()['requests_prepared'], 1)

    def test_waits_for_world_target_and_issued_player(self):
        world, spec, keys = fixture()
        rpc = FrameworkInitRpc(spec, 123)
        world.tags = [12]
        self.assertIsNone(rpc.prepare(world))
        world.tags.append(13)
        target = world.instances.pop(keys[3])
        self.assertIsNone(rpc.prepare(world))
        world.instances[keys[3]] = target
        world.instances[keys[5]]['properties']['UserId'] = 456
        self.assertIsNone(rpc.prepare(world))
        world.instances[keys[5]]['properties']['UserId'] = 123
        self.assertIsNotNone(rpc.prepare(world))

    def test_rejects_ambiguous_or_wrong_class_path(self):
        for mutation in ('duplicate', 'wrong_class'):
            world, spec, keys = fixture()
            if mutation == 'duplicate':
                world.instances[(1, 999)] = copy.deepcopy(world.instances[keys[3]])
            else:
                world.instances[keys[3]]['class'] = 2
            with self.assertRaises(ValueError): FrameworkInitRpc(spec, 123).prepare(world)

    def test_rejects_ambiguous_player_and_wrong_schema(self):
        world, spec, keys = fixture()
        world.instances[(1, 999)] = copy.deepcopy(world.instances[keys[5]])
        with self.assertRaises(ValueError): FrameworkInitRpc(spec, 123).prepare(world)
        del world.instances[(1, 999)]
        world.schema['events'][2]['arguments'][1]['type'] = 2
        with self.assertRaises(ValueError): FrameworkInitRpc(spec, 123).prepare(world)

    def test_success_correlates_new_target_and_id_only(self):
        world, spec, keys = fixture()
        world.events.append({'key': keys[3], 'event': 3, 'arguments': [1, [b'stale']]})
        rpc = FrameworkInitRpc(spec, 123)
        rpc.prepare(world)
        self.assertFalse(rpc.poll(world))
        world.events.extend([
            {'key': (1, 123), 'event': 3, 'arguments': [1, [b'other target']]},
            {'key': keys[3], 'event': 3, 'arguments': [2, [b'other request']]},
        ])
        self.assertFalse(rpc.poll(world))
        # Also exercise the independent inbound event parser for a real reply.
        world.packet(bytes.fromhex('8307a302560400000300020102')
                     + b'\x07private' + b'\0')
        self.assertTrue(rpc.poll(world))
        self.assertEqual(rpc.result, [b'private'])
        self.assertNotIn('private', json.dumps(rpc.summary()))
        self.assertFalse(rpc.poll(world))
        self.assertIsNone(rpc.prepare(world))

    def test_error_contents_remain_private(self):
        world, spec, keys = fixture()
        rpc = FrameworkInitRpc(spec, 123)
        rpc.prepare(world)
        world.events.append({'key': keys[3], 'event': 1, 'arguments': [1, b'private error']})
        self.assertTrue(rpc.poll(world))
        self.assertEqual(rpc.error, b'private error')
        self.assertEqual(rpc.summary()['state'], 'server_error')
        self.assertNotIn('private', json.dumps(rpc.summary()))

    def test_malformed_matched_reply_and_journal_overflow(self):
        world, spec, keys = fixture()
        rpc = FrameworkInitRpc(spec, 123)
        rpc.prepare(world)
        world.events.append({'key': keys[3], 'event': 3, 'arguments': [1, b'not tuple']})
        with self.assertRaises(ValueError): rpc.poll(world)
        world, spec, keys = fixture()
        world.events = collections.deque([{'key': None, 'event': 0, 'arguments': []}], maxlen=1)
        rpc = FrameworkInitRpc(spec, 123)
        rpc.prepare(world)
        world.events.append({'key': None, 'event': 0, 'arguments': []})
        with self.assertRaises(ValueError): rpc.poll(world)

    def test_unknown_capabilities_arguments_and_world_rejected(self):
        world, spec, _ = fixture()
        for caps in (0, 0x100001, True):
            with self.assertRaises(ValueError): FrameworkInitRpc(spec, 123, caps)
        invalid = dict(spec, arguments=[1])
        with self.assertRaises(ValueError): FrameworkInitRpc(invalid, 123)
        rpc = FrameworkInitRpc(spec, 123)
        rpc.prepare(world)
        with self.assertRaises(ValueError): rpc.poll(fixture()[0])


if __name__ == '__main__':
    unittest.main()
