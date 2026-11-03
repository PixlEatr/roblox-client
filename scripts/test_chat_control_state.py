import collections
import unittest
from types import SimpleNamespace

from chat_control_state import ChatControlState


ALLOCATED = 'ServerToClientUniverseChatChannelAllocatedSignalV1'
PRESET_CONFIG = 'ServerToClientPresetChatConfigChangedSignal'
USER_ACCESS = 'ServerToClientPresetChatUserAccessChangedSignal'


def fixture(*, other_target=False):
    event_specs = [(ALLOCATED, (2,)), (PRESET_CONFIG, (9, 2)),
                   (USER_ACCESS, (2,)), ('ServerToClientMessageReplicateSignal', (2,))]
    classes = [
        {'name': 'TextChatService', 'events': [0, 1, 2, 3]},
        {'name': 'Folder', 'events': []},
    ]
    events = [{'id': i, 'class': 0, 'name': name,
               'arguments': [{'type': kind} for kind in types]}
              for i, (name, types) in enumerate(event_specs)]
    instances = {(9, 1): {'class': 0}}
    if other_target:
        instances[(9, 2)] = {'class': 1}
    return SimpleNamespace(schema={'classes': classes, 'events': events},
                           instances=instances, event_sequence=0,
                           event_journal=collections.deque(maxlen=4096)), (9, 1)


def push(world, service, event_id, args):
    world.event_sequence += 1
    world.event_journal.append((world.event_sequence,
                                {'key': service, 'event': event_id,
                                 'arguments': args}))


class ChatControlStateTests(unittest.TestCase):
    def test_replays_pre_attach_control_history_and_ignores_messages(self):
        world, service = fixture()
        push(world, service, 3, [b'ordinary message'])
        push(world, service, 0, [b'global'])
        push(world, service, 1, [True, b'{"version":1}'])
        push(world, service, 2, [b'Enabled'])
        state = ChatControlState()
        result = state.consume(world)
        self.assertEqual(result, {
            'allocated_global': True, 'config_seen': True,
            'config_enabled': True, 'access_seen': True,
            'access_label': 'Enabled'})
        self.assertEqual(state.consume(world), result)

    def test_only_exact_global_allocation_counts(self):
        world, service = fixture()
        state = ChatControlState()
        push(world, service, 0, [b'custom'])
        result = state.consume(world)
        self.assertFalse(result['allocated_global'])
        push(world, service, 0, [b'global'])
        self.assertTrue(state.consume(world)['allocated_global'])

    def test_access_label_does_not_become_send_permission(self):
        world, service = fixture()
        state = ChatControlState()
        push(world, service, 2, [b'Enabled'])
        self.assertEqual(state.consume(world)['access_label'], 'Enabled')
        push(world, service, 2, [b'CanSend'])
        result = state.consume(world)
        self.assertTrue(result['access_seen'])
        self.assertEqual(result['access_label'], 'unknown')
        self.assertNotIn('can_send', result)

    def test_invalid_signature_target_and_oversized_version_are_ignored(self):
        world, service = fixture(other_target=True)
        state = ChatControlState()
        push(world, service, 1, [True, b'v' * 4097])
        push(world, (9, 2), 0, [b'global'])
        world.schema['events'][2]['arguments'] = [{'type': 9}]
        push(world, service, 2, [b'Enabled'])
        # The descriptor no longer belongs to the service class.
        world.schema['classes'][0]['events'].remove(0)
        push(world, service, 0, [b'global'])
        result = state.consume(world)
        self.assertFalse(result['allocated_global'])
        self.assertFalse(result['config_seen'])
        self.assertIsNone(result['config_enabled'])
        self.assertFalse(result['access_seen'])
        self.assertEqual(result['access_label'], 'unknown')

    def test_journal_gap_invalidates_old_state_but_accepts_later_controls(self):
        world, service = fixture()
        state = ChatControlState()
        push(world, service, 0, [b'global'])
        push(world, service, 1, [True, b'{}'])
        self.assertTrue(state.consume(world)['config_enabled'])
        world.event_journal = collections.deque(maxlen=2)
        for sequence, event_id, args in ((5, 3, [b'x']),
                                         (6, 0, [b'global']),
                                         (7, 2, [b'Enabled'])):
            world.event_sequence = sequence
            world.event_journal.append((sequence, {'key': service,
                                                    'event': event_id,
                                                    'arguments': args}))
        result = state.consume(world)
        self.assertTrue(result['allocated_global'])
        self.assertFalse(result['config_seen'])
        self.assertIsNone(result['config_enabled'])
        self.assertTrue(result['access_seen'])
        self.assertEqual(result['access_label'], 'Enabled')

    def test_cross_world_and_malformed_world_are_rejected_or_cleared(self):
        world, service = fixture()
        state = ChatControlState()
        push(world, service, 0, [b'global'])
        self.assertTrue(state.consume(world)['allocated_global'])
        other, _ = fixture()
        with self.assertRaises(ValueError):
            state.consume(other)
        world.event_sequence = 'bad'
        result = state.consume(world)
        self.assertFalse(result['allocated_global'])
        self.assertFalse(result['config_seen'])


if __name__ == '__main__':
    unittest.main()
