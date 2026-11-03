import unittest

from physics_authority import assess_physics_authority


class PhysicsAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            'own_player_confirmed': True,
            'character_model_confirmed': True,
            'humanoid_confirmed': True,
            'root_part_confirmed': True,
            'root_in_workspace_confirmed': True,
            'assembly_root_confirmed': True,
            'streaming_region_confirmed': True,
            'anchored': False,
            'anchored_source': 'server_replication',
            'ownership_granted': True,
            'ownership_source': 'server_replication',
        }

    def test_explicit_complete_evidence_passes_only_prerequisite_gate(self):
        result = assess_physics_authority(self.evidence)
        self.assertTrue(result['eligible_for_physics_submission'])
        self.assertEqual(result['authority_evidence'], 'explicit_server_replication')
        self.assertFalse(result['server_acceptance_confirmed'])

    def test_own_player_and_unanchored_root_do_not_imply_ownership(self):
        self.evidence.pop('ownership_granted')
        self.evidence.pop('ownership_source')
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertIn('network_ownership_unconfirmed', result['reasons'])

    def test_class_default_unanchored_is_not_received_evidence(self):
        self.evidence['anchored_source'] = 'class_default'
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertIn('anchored_state_unconfirmed', result['reasons'])

    def test_missing_assembly_or_streaming_confirmation_blocks(self):
        self.evidence['assembly_root_confirmed'] = False
        self.evidence['streaming_region_confirmed'] = False
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertIn('assembly_root_confirmed_unconfirmed', result['reasons'])
        self.assertIn('streaming_region_confirmed_unconfirmed', result['reasons'])

    def test_server_owned_or_anchored_root_blocks(self):
        self.evidence['ownership_granted'] = False
        self.evidence['anchored'] = True
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertIn('root_anchored', result['reasons'])
        self.assertIn('network_ownership_not_granted', result['reasons'])

    def test_truthy_non_booleans_are_rejected(self):
        self.evidence['ownership_granted'] = 1
        self.evidence['anchored'] = 0
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertIn('network_ownership_unconfirmed', result['reasons'])
        self.assertIn('anchored_state_unconfirmed', result['reasons'])

    def test_received_server_authority_mode_overrides_ownership_grant(self):
        self.evidence['workspace_authority_mode'] = 'Server'
        self.evidence['workspace_authority_mode_source'] = 'server_replication'
        result = assess_physics_authority(self.evidence)
        self.assertFalse(result['eligible_for_physics_submission'])
        self.assertEqual(result['authority_evidence'], 'insufficient')
        self.assertIn('workspace_server_authority', result['reasons'])
        self.assertFalse(result['server_acceptance_confirmed'])

    def test_unreceived_server_authority_label_does_not_replace_grant_evidence(self):
        self.evidence['workspace_authority_mode'] = 'Server'
        self.evidence['workspace_authority_mode_source'] = 'class_default'
        result = assess_physics_authority(self.evidence)
        self.assertTrue(result['eligible_for_physics_submission'])
        self.assertNotIn('workspace_server_authority', result['reasons'])


if __name__ == '__main__':
    unittest.main()
