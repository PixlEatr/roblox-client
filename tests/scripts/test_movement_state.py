import unittest

from test_world_snapshot import fixture
from movement_state import inspect_movement_state


class MovementStateTests(unittest.TestCase):
    def setUp(self):
        self.world, self.root_key = fixture()

    def test_explicit_unanchored_and_received_pose_enable_local_controller(self):
        self.world.instances[self.root_key]['properties']['Anchored'] = False
        state = inspect_movement_state(self.world, 123)
        public, private = state['public'], state['private']
        self.assertTrue(public['local_controller_ready'], public)
        self.assertTrue(public['explicitly_unanchored'])
        self.assertEqual(public['anchored_origin'], 'received')
        self.assertTrue(public['received_pose_available'])
        self.assertEqual(public['received_pose_field'], 'CFrame')
        self.assertEqual(private['received_pose'], (1.0, 2.0, 3.0))
        self.assertIs(private['root'], self.world.instances[self.root_key])

    def test_missing_anchor_is_not_inferred_from_local_class_default(self):
        state = inspect_movement_state(self.world, 123)
        self.assertFalse(state['public']['local_controller_ready'])
        self.assertFalse(state['public']['explicitly_unanchored'])
        self.assertEqual(state['public']['anchored_origin'], 'unavailable')
        self.assertIn('anchored_not_explicitly_received', state['public']['reasons'])
        self.assertFalse(state['public']['server_walk_confirmed'])

    def test_anchored_root_blocks_local_controller(self):
        self.world.instances[self.root_key]['properties']['Anchored'] = True
        state = inspect_movement_state(self.world, 123)
        self.assertFalse(state['public']['local_controller_ready'])
        self.assertIn('root_is_anchored', state['public']['reasons'])

    def test_client_pose_is_not_mistaken_for_received_pose(self):
        self.world.instances[self.root_key]['properties'].pop('CFrame')
        state = inspect_movement_state(self.world, 123)
        self.assertFalse(state['public']['received_pose_available'])
        self.assertIsNone(state['private']['received_pose'])
        self.assertIn('received_pose_unavailable', state['public']['reasons'])

    def test_only_sanitized_public_flags_are_returned_separately_from_handles(self):
        state = inspect_movement_state(self.world, 123)
        self.assertEqual(set(state), {'public', 'private'})
        self.assertNotIn('root', state['public'])
        self.assertNotIn('player_key', state['public'])
        self.assertEqual(state['public']['server_ownership'], 'unknown')

    def test_bad_pose_components_are_not_published(self):
        self.world.instances[self.root_key]['properties']['CFrame']['position'] = [1.0, float('nan'), 3.0]
        state = inspect_movement_state(self.world, 123)
        self.assertIsNone(state['private']['received_pose'])
        self.assertFalse(state['public']['received_pose_available'])


if __name__ == '__main__':
    unittest.main()
