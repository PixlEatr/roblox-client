import unittest

from animation_replication import (
    SUPPORTED_CAPABILITIES, encode_on_combined_update,
)
from world_replication import World


def animation_world():
    classes = [
        {'name': 'Players', 'events': [], 'properties': []},
        {'name': 'Player', 'events': [], 'properties': []},
        {'name': 'Model', 'events': [], 'properties': []},
        {'name': 'Humanoid', 'events': [], 'properties': []},
        {'name': 'Animator', 'events': [0], 'properties': []},
        {'name': 'Workspace', 'events': [], 'properties': []},
    ]
    enums = [{'id': i, 'name': f'Enum{i}', 'bits': 8} for i in range(38)]
    enums[37] = {'id': 37, 'name': 'AnimationPriority', 'bits': 7}
    event = {
        'id': 0, 'class': 4, 'name': 'OnCombinedUpdate',
        'arguments': [
            {'type': 33, 'enum': 65535}, {'type': 9, 'enum': 65535},
            {'type': 11, 'enum': 65535}, {'type': 11, 'enum': 65535},
            {'type': 11, 'enum': 65535}, {'type': 11, 'enum': 65535},
            {'type': 7, 'enum': 37}, {'type': 9, 'enum': 65535},
            {'type': 10, 'enum': 65535},
        ],
    }
    world = World({'classes': classes, 'enums': enums, 'events': [event], 'strings': ['']})
    keys = {
        'players': (9, 1), 'player': (9, 2), 'character': (9, 3),
        'humanoid': (9, 4), 'workspace': (9, 5), 'animator': (9, 6),
    }
    world.instances.update({
        keys['players']: {'class': 0, 'properties': {}, 'parent': None},
        keys['player']: {'class': 1, 'properties': {'UserId': 4242,
                         'Character': keys['character']}, 'parent': keys['players']},
        keys['character']: {'class': 2, 'properties': {}, 'parent': keys['workspace']},
        keys['humanoid']: {'class': 3, 'properties': {}, 'parent': keys['character']},
        keys['workspace']: {'class': 5, 'properties': {}, 'parent': None},
        keys['animator']: {'class': 4, 'properties': {}, 'parent': keys['humanoid']},
    })
    world.creation[keys['animator']] = {'fresh': True, 'complete': True}
    return world, keys


def encode(world, animator, **overrides):
    fields = dict(
        capability_bits=SUPPORTED_CAPABILITIES,
        animation='rbxassetid://123456', play_state=True,
        fade_time=0.125, weight=0.75, speed=1.0,
        time_position=2.5, priority=2, looping=True,
        values_updated=0x1234,
    )
    fields.update(overrides)
    return encode_on_combined_update(world, 4242, animator, **fields)


class AnimationReplicationCodecTests(unittest.TestCase):
    def test_packet_roundtrips_through_world_packet_with_native_schema(self):
        world, keys = animation_world()
        packet = encode(world, keys['animator'])
        self.assertEqual(packet[:2], b'\x83\x07')
        self.assertEqual(packet[-1:], b'\0')

        decoded = World(world.schema)
        decoded.packet(packet)
        event = decoded.events[-1]
        self.assertEqual(event['key'], keys['animator'])
        self.assertEqual(event['event'], 0)
        self.assertEqual(event['arguments'], [
            b'rbxassetid://123456', True, 0.125, 0.75, 1.0, 2.5, 2, True, 0x1234,
        ])

    def test_signed_values_updated_is_explicit_and_preserved_without_interpretation(self):
        world, keys = animation_world()
        packet = encode(world, keys['animator'], values_updated=-7)
        decoded = World(world.schema)
        decoded.packet(packet)
        self.assertEqual(decoded.events[-1]['arguments'][-1], -7)
        with self.assertRaises(TypeError):
            encode_on_combined_update(
                world, 4242, keys['animator'], capability_bits=SUPPORTED_CAPABILITIES,
                animation='rbxassetid://1', play_state=True, fade_time=0.,
                weight=1., speed=1., time_position=0., priority=2, looping=True)

    def test_priority_core_1000_roundtrips_as_native_unsigned_enum_integer(self):
        world, keys = animation_world()
        packet = encode(world, keys['animator'], priority=1000)
        decoded = World(world.schema)
        decoded.packet(packet)
        self.assertEqual(decoded.events[-1]['arguments'][6], 1000)

    def test_only_complete_own_humanoid_animator_and_exact_schema_are_accepted(self):
        world, keys = animation_world()
        other = (9, 90)
        world.instances[other] = {'class': 4, 'properties': {}, 'parent': keys['character']}
        world.creation[other] = {'fresh': True, 'complete': True}
        with self.assertRaisesRegex(ValueError, 'under the issued player Humanoid'):
            encode(world, other)
        world.creation[keys['animator']]['complete'] = False
        with self.assertRaisesRegex(ValueError, 'complete server-received'):
            encode(world, keys['animator'])
        world.creation[keys['animator']]['complete'] = True
        world.schema['events'][0]['arguments'][6]['enum'] = 36
        with self.assertRaisesRegex(ValueError, 'Unsupported Animator'):
            encode(world, keys['animator'])

    def test_invalid_parent_and_nonempty_prefix_zero_are_rejected(self):
        world, keys = animation_world()
        world.instances[keys['player']]['parent'] = None
        with self.assertRaisesRegex(ValueError, 'not parented to Players'):
            encode(world, keys['animator'])

        world, keys = animation_world()
        world.schema['strings'][0] = 'rbxassetid://'
        with self.assertRaisesRegex(ValueError, 'ContentId prefix table'):
            encode(world, keys['animator'])

    def test_wire_ranges_types_and_capability_are_checked(self):
        world, keys = animation_world()
        for updates in (-(1 << 31) - 1, 1 << 31, True):
            with self.subTest(values_updated=updates), self.assertRaises(ValueError):
                encode(world, keys['animator'], values_updated=updates)
        for priority in (-1, 6, 128, True):
            with self.subTest(priority=priority), self.assertRaises(ValueError):
                encode(world, keys['animator'], priority=priority)
        with self.assertRaises(ValueError):
            encode(world, keys['animator'], capability_bits=SUPPORTED_CAPABILITIES | 1)
        with self.assertRaises(ValueError):
            encode(world, keys['animator'], play_state=1)
        with self.assertRaises(ValueError):
            encode(world, keys['animator'], speed=float('nan'))


if __name__ == '__main__':
    unittest.main()
