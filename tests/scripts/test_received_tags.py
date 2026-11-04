import copy
import hashlib
import unittest

from persistent_runtime import PersistentLuauHost
from received_tags import decode_tags, tag_snapshot
from test_world_snapshot import fixture
from world_snapshot import make_world_snapshot, make_world_delta


class ReceivedTagsTests(unittest.TestCase):
    def world(self, value=None):
        world, key = fixture()
        pi = len(world.schema['properties'])
        world.schema['properties'].append({'name': 'Tags', 'type': 46})
        world.schema['classes'][3]['properties'].append(pi)
        if value is not None:
            self.assign(world, key, value)
        return world, key

    def assign(self, world, key, value):
        digest = hashlib.blake2b(value, digest_size=16).digest()
        world.shared[digest] = value
        world.instances[key]['properties']['Tags'] = {'shared': digest.hex()}

    def host(self, world):
        host = PersistentLuauHost(make_world_snapshot(world, 123))
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 100})
        self.assertTrue(result['ok'], result)
        return result

    def test_nul_delimited_utf8_and_set_membership(self):
        self.assertEqual(decode_tags(b''), [])
        self.assertEqual(decode_tags('one\0\0café\0one\0'.encode()), ['café', 'one'])
        self.assertEqual(decode_tags(b'x' * 65534 + b'\0a\0b'), ['a', 'b', 'x' * 65534])
        for value in (b'\xff', b'x' * 65537, b'x' * (1024 * 1024 + 1),
                      b'\0'.join(str(i).encode() for i in range(257))):
            with self.assertRaises(ValueError): decode_tags(value)

    def test_absent_unresolved_and_invalid_are_not_known_empty(self):
        self.assertEqual(tag_snapshot({}), {})
        fresh = {'fresh': True, 'complete': True}
        for bad in (b'\xff', {'shared': '00' * 16}, {'shared': 'bad'}, None):
            result = tag_snapshot({'Tags': bad}, creation=fresh)
            self.assertFalse(result['collection_tags_known'])
        for flag in ('fresh', 'complete'):
            creation = dict(fresh); creation[flag] = False
            self.assertEqual(tag_snapshot({}, creation=creation), {})

    def test_shared_payload_reaches_luau_without_changing_received_state(self):
        world, key = self.world(b'original\0second')
        before = copy.deepcopy(world.instances)
        host = self.host(world)
        result = self.execute(host, '''
p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local tags=p:GetTags()
p:AddTag("local")
return #tags,p:HasTag("original"),p:HasTag("second"),p:HasTag("local")
''')
        self.assertEqual(result['values'], [2, True, True, True])
        self.assertEqual(world.instances, before)

    def test_fresh_cached_creation_has_empty_constructor_tags(self):
        world, key = self.world()
        world.creation[key].update(ordinary=False, property_lists=1)
        result = self.execute(self.host(world), '''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(#p:GetTags()==0)
p:AddTag("assigned")
return p:HasTag("assigned")
''')
        self.assertEqual(result['values'], [True])

    def test_captured_large_tag_size_does_not_make_received_state_unknown(self):
        world, key = self.world(b'x' * 65534 + b'\0a\0b')
        result = self.execute(self.host(world), '''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(#p:GetTags()==3 and p:HasTag(string.rep("x",65534)))
p:AddTag("local")
return #p:GetTags(),p:HasTag("local")
''')
        self.assertEqual(result['values'], [4, True])

    def test_received_changes_replace_local_tags_and_emit_membership_events(self):
        world, key = self.world(b'old')
        host = self.host(world)
        self.execute(host, '''
p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
p:AddTag("local")
local c=game:GetService("CollectionService")
observed={}
c:GetInstanceRemovedSignal("old"):Connect(function(i)assert(i==p);table.insert(observed,"old")end)
c:GetInstanceRemovedSignal("local"):Connect(function(i)assert(i==p);table.insert(observed,"local")end)
c:GetInstanceAddedSignal("new"):Connect(function(i)assert(i==p);table.insert(observed,"new")end)
''')
        cursor = world.revision
        for value in (b'new', b'new'):
            self.assign(world, key, value); world.touch(key, ['Tags'])
        delta = make_world_delta(world, cursor)
        self.assertEqual([a['tag_snapshot']['collection_tags'] for a in delta['actions']], [['new'], ['new']])
        response = host.call({'op': 'update', 'delta': delta, 'timeout_ms': 100})
        self.assertTrue(response['ok'], response)
        result = self.execute(host, 'return #observed,p:HasTag("new"),p:HasTag("old"),p:HasTag("local")')
        self.assertEqual(result['values'], [3, True, False, False])
        cursor = world.revision; self.assign(world, key, b'\xff'); world.touch(key, ['Tags'])
        response = host.call({'op': 'update', 'delta': make_world_delta(world, cursor), 'timeout_ms': 100})
        self.assertTrue(response['ok'], response)
        result = self.execute(host, 'return pcall(function()return p:GetTags()end)')
        self.assertFalse(result['values'][0])

    def test_recreation_preserves_omitted_tags_and_invalidates_explicit_unknown(self):
        world, key = self.world(b'known')
        snapshot = make_world_snapshot(world, 123)
        record = next(copy.deepcopy(n) for n in snapshot['nodes'] if n['id'] == '1:4')
        host = self.host(world)
        record.pop('collection_tags'); record.pop('collection_tags_known')
        for index, state in enumerate(({}, {'collection_tags': [], 'collection_tags_known': False}), 1):
            record.update(state)
            response = host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
                'version': 1, 'delta': True, 'sequence': world.revision + index,
                'actions': [{'kind': 'create', 'id': '1:4', 'node': record, 'sequence': world.revision + index}]}})
            self.assertTrue(response['ok'], response)
            result = self.execute(host, 'return pcall(function()return game:GetService("Players").LocalPlayer.Character.HumanoidRootPart:HasTag("known")end)')
            self.assertEqual(result['values'][0], not bool(state))


if __name__ == '__main__': unittest.main()
