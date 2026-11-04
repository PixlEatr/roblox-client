import unittest
from types import SimpleNamespace

from physics_received_evidence import aggregate_reports, summarize_world


class ReceivedEvidenceTests(unittest.TestCase):
    @staticmethod
    def world(anchor=None, actions=None, *, own_owner=7):
        properties = {
            "UserId": 42,
            "Character": (1, 2),
        }
        root_properties = {
            "NetworkOwnerV3": own_owner,
            "CFrame": {"position": [0.0, 10.0, 0.0]},
        }
        if anchor is not None:
            root_properties["Anchored"] = anchor
        return SimpleNamespace(
            schema={"classes": [{"name": "Player"}, {"name": "Model"}, {"name": "Part"}]},
            instances={
                (1, 1): {"class": 0, "properties": properties},
                (1, 2): {"class": 1, "properties": {"PrimaryPart": (1, 3)}},
                (1, 3): {"class": 2, "properties": root_properties},
            },
            client_scope=7,
            creation={(1, 3): {"fresh": True, "complete": True, "ordinary": True}},
            actions=actions or [],
        )

    def test_user_id_resolves_root_before_owner_comparison(self):
        world = self.world()
        world.instances[(2, 1)] = {"class": 0, "properties": {"UserId": 99, "Character": (2, 2)}}
        world.instances[(2, 2)] = {"class": 1, "properties": {"PrimaryPart": (2, 3)}}
        world.instances[(2, 3)] = {"class": 2, "properties": {
            "NetworkOwnerV3": 88, "CFrame": {"position": [2.0, 10.0, 0.0]}}}
        world.creation[(2, 3)] = {"fresh": True, "complete": True, "ordinary": True}
        result = summarize_world(world, 20, own_user_id=42)
        self.assertTrue(result["network_owner_matches_client_scope"])
        self.assertEqual(result["other_player_root_count"], 1)
        self.assertEqual(result["other_player_roots_matching_client_scope"], 0)

    def test_class_default_does_not_claim_received_anchor_or_gravity(self):
        result = summarize_world(self.world(), 20, own_user_id=42)
        self.assertEqual(result["anchored"], False)
        self.assertEqual(result["anchored_origin"], "exact_build_class_default")
        self.assertEqual(result["received_root_pose_update_count"], 0)
        self.assertFalse(result["server_received_motion_observed"])
        self.assertFalse(result["local_prediction_used"])

    def test_received_anchor_and_received_pose_are_distinct_evidence(self):
        actions = [{"kind": "set", "key": (1, 3),
                    "fields": {"CFrame": {"position": [0.0, 9.0, 0.0]}}}]
        result = summarize_world(self.world(anchor=True, actions=actions), 21, own_user_id=42)
        self.assertTrue(result["anchored"])
        self.assertEqual(result["anchored_origin"], "server_replication")
        self.assertEqual(result["received_root_pose_update_count"], 1)
        self.assertTrue(result["server_received_motion_observed"])

    def test_owner_mismatch_is_reported_without_changing_identity_selection(self):
        result = summarize_world(self.world(own_owner=88), 3, own_user_id=42)
        self.assertEqual(result["status"], "own_root_resolved")
        self.assertFalse(result["network_owner_matches_client_scope"])

    def test_multi_capture_aggregate_reports_counts_without_claiming_universal_semantics(self):
        result = aggregate_reports([
            {"status": "own_root_resolved", "network_owner_matches_client_scope": True,
             "other_player_root_count": 1, "other_player_roots_matching_client_scope": 0},
            {"status": "own_root_resolved", "network_owner_matches_client_scope": False,
             "other_player_root_count": 2, "other_player_roots_matching_client_scope": 1},
        ])
        self.assertEqual(result["capture_count"], 2)
        self.assertEqual(result["own_owner_matches_client_scope_count"], 1)
        self.assertEqual(result["other_player_root_count"], 3)
        self.assertFalse(result["all_resolved_own_roots_match_scope"])


if __name__ == "__main__":
    unittest.main()
