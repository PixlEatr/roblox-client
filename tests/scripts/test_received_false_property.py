"""Regression for false boolean values across decoded replication and Luau reads."""
import struct
import unittest

import persistent_runtime
from game_wire import encode_unsigned
from test_world_snapshot import fixture
from world_snapshot import make_world_delta, make_world_snapshot


class ReceivedFalsePropertyTests(unittest.TestCase):
    def test_type9_false_survives_replication_delta_snapshot_and_luau(self):
        world, key = fixture()

        # Add Archivable as a real boolean property in this fixture schema.
        property_index = len(world.schema['properties'])
        world.schema['properties'].append({'name': 'Archivable', 'type': 9})
        world.schema['classes'][3]['properties'].append(property_index)
        world.schema['parent_property_id'] = property_index + 1
        world.instances[key]['properties']['Archivable'] = True
        baseline = make_world_snapshot(world, 123)
        root_id = f'{key[0]}:{key[1]}'
        self.assertIs(next(n for n in baseline['nodes'] if n['id'] == root_id)
                      ['properties']['Archivable'], True)

        # Ordinary replication property record: packet 131, change record 3,
        # instance GUID, property index, reset version 0, type-9 boolean 0,
        # then packet terminator 0.
        cursor = world.revision
        wire = (b'\x83\x03' + encode_unsigned(key[0]) + struct.pack('<IHB', key[1], property_index, 0)
                + b'\x00\x00')
        world.packet(wire)
        self.assertIs(world.instances[key]['properties']['Archivable'], False)
        self.assertEqual(world.received_property_changes[0][0], key)

        delta = make_world_delta(world, cursor)
        self.assertIs(delta['actions'][0]['fields']['Archivable'], False)
        snapshot = make_world_snapshot(world, 123)
        snap_node = next(n for n in snapshot['nodes'] if n['id'] == root_id)
        self.assertIs(snap_node['properties']['Archivable'], False)
        self.assertEqual(snap_node['origins']['Archivable'], 'received')

        host = persistent_runtime.PersistentLuauHost(baseline)
        self.addCleanup(host.close)
        result = host.call({'op': 'update', 'timeout_ms': 100,
                            'delta': delta})
        self.assertTrue(result['ok'], result)
        result = host.call({'op': 'execute', 'timeout_ms': 100, 'source': '''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(p.Archivable==false)
assert(client.propertyOrigin(p,"Archivable")=="received")
return true
'''})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True], result)


if __name__ == '__main__':
    unittest.main()
