import collections
import unittest
from unittest.mock import patch

from chat_protocol import ChatProtocol
from test_chat_protocol import fixture


class ChatAttachCacheTests(unittest.TestCase):
    def setUp(self):
        self.world, self.keys = fixture()
        self.chat = ChatProtocol(42)
        self.assertTrue(self.chat.attach(self.world))
        self.player_key = self.keys[2]

    def scan_counter(self):
        scans = []
        original = self.chat._scan_players

        def counted(world):
            scans.append(getattr(world, 'revision', None))
            return original(world)

        self.chat._scan_players = counted
        return scans

    def test_non_player_receipts_advance_cursor_without_rescanning_players(self):
        scans = self.scan_counter()
        self.world.instances[self.keys[4]]['properties']['Name'] = b'Updated'
        self.world.touch(self.keys[4], ['Name'])
        self.world.shared[b'fresh-shared-entry'] = b'fresh-shared-value'
        self.assertTrue(self.chat.attach(self.world))
        self.assertTrue(self.chat.attach(self.world))
        self.assertEqual(scans, [])
        self.assertEqual(self.chat._attach_revision, self.world.revision)

    def test_new_top_container_rechecks_service_ambiguity(self):
        duplicate = (123, 100)
        self.world.instances[duplicate] = {'class': 0, 'properties': {}, 'parent': None}
        self.world.top_containers[duplicate] = {}
        with self.assertRaisesRegex(ValueError, 'Ambiguous TextChatService'):
            self.chat.attach(self.world)

    def test_existing_top_container_reclassified_as_second_chat_service(self):
        # The top-container key set stays identical while its class changes.
        # Rechecking the small root list must still find the new ambiguity.
        self.world.instances[self.keys[1]]['class'] = 0
        with self.assertRaisesRegex(ValueError, 'Ambiguous TextChatService'):
            self.chat.attach(self.world)

    def test_duplicate_matching_player_creation_rescans_and_rejects_ambiguity(self):
        duplicate = (123, 99)
        self.world.instances[duplicate] = {
            'class': 2, 'properties': {'UserId': 42}, 'parent': self.keys[1]}
        self.world.touch(duplicate, created=True)
        with self.assertRaisesRegex(ValueError, 'Ambiguous local Player'):
            self.chat.attach(self.world)

    def test_internal_journal_gap_falls_back_and_catches_missing_player_action(self):
        cursor = self.world.revision
        duplicate = (123, 98)
        self.world.instances[duplicate] = {
            'class': 2, 'properties': {'UserId': 42}, 'parent': self.keys[1]}
        self.world.touch(duplicate, created=True)
        player_create = self.world.actions[-1]
        self.world.instances[self.keys[4]]['properties']['Name'] = b'Intermediate'
        self.world.touch(self.keys[4], ['Name'])
        self.world.instances[self.keys[4]]['properties']['Name'] = b'Latest'
        self.world.touch(self.keys[4], ['Name'])
        last_action = self.world.actions[-1]
        self.assertEqual(player_create['sequence'], cursor + 1)
        self.assertEqual(last_action['sequence'], cursor + 3)
        self.world.actions = collections.deque([player_create, last_action])
        with self.assertRaisesRegex(ValueError, 'Ambiguous local Player'):
            self.chat.attach(self.world)

    def test_player_reparent_and_restore_rescan_and_update_cache(self):
        self.world.instances[self.player_key]['parent'] = self.keys[0]
        self.world.touch(self.player_key, ['Parent'])
        self.assertFalse(self.chat.attach(self.world))
        self.world.instances[self.player_key]['parent'] = self.keys[1]
        self.world.touch(self.player_key, ['Parent'])
        self.assertTrue(self.chat.attach(self.world))
        self.assertEqual(self.chat._own_player_key, self.player_key)

    def test_player_removal_clears_cached_identity(self):
        self.world.remove_tree(self.player_key)
        self.assertFalse(self.chat.attach(self.world))
        self.assertIsNone(self.chat._own_player_key)

    def test_player_userid_change_triggers_full_scan(self):
        self.world.instances[self.player_key]['properties']['UserId'] = 43
        self.world.touch(self.player_key, ['UserId'])
        self.assertFalse(self.chat.attach(self.world))
        self.world.instances[self.player_key]['properties']['UserId'] = 42
        self.world.touch(self.player_key, ['UserId'])
        self.assertTrue(self.chat.attach(self.world))

    def test_journal_overrun_or_missing_journal_falls_back_to_full_scan(self):
        scans = self.scan_counter()
        self.world.revision += 10
        self.world.actions = collections.deque([
            {'sequence': self.world.revision, 'kind': 'event', 'key': None}])
        self.assertTrue(self.chat.attach(self.world))
        self.assertEqual(len(scans), 1)

        scans.clear()
        del self.world.actions
        self.world.revision += 1
        self.assertTrue(self.chat.attach(self.world))
        self.assertEqual(len(scans), 1)

    def test_fake_world_without_revision_journal_keeps_full_scan_behavior(self):
        del self.world.actions
        del self.world.revision
        scans = self.scan_counter()
        self.assertTrue(self.chat.attach(self.world))
        self.assertTrue(self.chat.attach(self.world))
        self.assertEqual(len(scans), 2)

    def test_fast_path_still_validates_cached_player_and_permissions(self):
        self.world.instances[self.player_key]['properties']['UserId'] = 43
        self.assertFalse(self.chat.attach(self.world))

        # Restore and reattach, then make the received TextSource permission
        # deny sending. The cache must not weaken send/_source checks.
        self.world.instances[self.player_key]['properties']['UserId'] = 42
        self.assertTrue(self.chat.attach(self.world))
        sent = []
        self.assertTrue(self.chat.ready(self.world, lambda *args: sent.append(args)))
        self.world.instances[self.keys[5]]['properties']['CanSend'] = False
        with self.assertRaisesRegex(ValueError, 'disabled sending'):
            self.chat.send('test', lambda *args: sent.append(args))

    def test_non_player_cache_fast_path_uses_constant_class_checks(self):
        original = ChatProtocol._class
        calls = []

        def counted(world, key):
            calls.append(key)
            return original(world, key)

        with patch.object(ChatProtocol, '_class', staticmethod(counted)):
            self.assertTrue(self.chat.attach(self.world))
        self.assertLessEqual(len(calls), 4)


if __name__ == '__main__':
    unittest.main()
