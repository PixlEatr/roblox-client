"""Cross-module contracts for offline physics evidence and movement claims.

These tests intentionally use synthetic replicated state. They do not connect
to Roblox or treat local movement as server authority.
"""
import unittest

from movement_verification import MovementVerifier
from physics_assemblies import resolve_own_assembly
from physics_authority import assess_physics_authority
from physics_reconciliation import PhysicsReconciler


class SyntheticWorld:
    """Small decoded-world stand-in with explicit Player/Character/joint links."""
    schema = {"classes": [{"name": name} for name in
                          ("Player", "Model", "Part", "Humanoid", "WeldConstraint")]}
    class_parents = {"Part": "BasePart", "BasePart": "Instance",
                     "WeldConstraint": "Instance"}

    def __init__(self):
        self.player = (1, 1)
        self.character = (1, 2)
        self.root = (1, 3)
        self.torso = (1, 4)
        self.weld = (1, 5)
        self.instances = {
            self.player: {"class": 0, "parent": (1, 10),
                          "properties": {"UserId": 987, "Character": self.character}},
            self.character: {"class": 1, "parent": (1, 11), "properties": {"PrimaryPart": self.root}},
            self.root: {"class": 2, "parent": self.character,
                        "properties": {"Name": "HumanoidRootPart"}},
            self.torso: {"class": 2, "parent": self.character,
                         "properties": {"Name": "UpperTorso"}},
            self.weld: {"class": 4, "parent": self.character,
                        "properties": {"Part0": self.root, "Part1": self.torso, "Enabled": True}},
        }

    def avatar(self, user_id):
        return {"player_key": self.player if user_id == 987 else None,
                "character_key": self.character, "root_key": self.root}


class PhysicsPipelineContractTests(unittest.TestCase):
    def test_connected_assembly_never_implies_network_ownership(self):
        result = resolve_own_assembly(SyntheticWorld(), 987)
        self.assertEqual(result["evidence"]["connected_part_count"], 2)
        self.assertEqual(result["evidence"]["network_ownership"], "unknown")
        gate = assess_physics_authority({
            "own_player_confirmed": True,
            "character_model_confirmed": True,
            "humanoid_confirmed": True,
            "root_part_confirmed": True,
            "root_in_workspace_confirmed": True,
            "assembly_root_confirmed": True,
            "streaming_region_confirmed": True,
            "anchored": False,
            "anchored_source": "server_replication",
        })
        self.assertFalse(gate["eligible_for_physics_submission"])
        self.assertIn("network_ownership_unconfirmed", gate["reasons"])
        self.assertFalse(gate["server_acceptance_confirmed"])

    def test_unconnected_or_unresolved_joints_are_not_bridged(self):
        world = SyntheticWorld()
        world.instances[world.weld]["properties"]["Part1"] = (1, 999)
        result = resolve_own_assembly(world, 987)
        self.assertEqual(result["evidence"]["connected_part_count"], 1)
        self.assertEqual(result["evidence"]["incomplete_or_unresolved_joints"], 1)
        self.assertTrue(result["evidence"]["streaming_or_missing_data_possible"])

    def test_prediction_cannot_flow_into_received_movement_claim(self):
        reconciler = PhysicsReconciler()
        reconciler.record_prediction((0, 0, 0), timestamp=1.0)
        reconciler.record_prediction((10, 0, 0), timestamp=2.0)
        self.assertIsNone(reconciler.report()["confirmed_position"])

        verifier = MovementVerifier(min_samples=2, min_duration=0.1, min_distance=1)
        with self.assertRaisesRegex(ValueError, "received pose"):
            verifier.add_pose((0, 0, 0), timestamp=1.0,
                              source="local_prediction", own_root=True)
        self.assertFalse(verifier.report()["sustained_received_movement"])
        self.assertEqual(verifier.report()["sample_count"], 0)

    def test_pose_displacement_is_never_labeled_walking(self):
        verifier = MovementVerifier(min_samples=2, min_duration=0.1,
                                    min_distance=1, max_gap=1)
        verifier.add_pose((0, 0, 0), timestamp=1.0, source="received", own_root=True)
        verifier.add_pose((3, 0, 0), timestamp=1.5, source="received", own_root=True)
        report = verifier.report()
        self.assertTrue(report["sustained_received_movement"])
        self.assertEqual(report["classification"], "sustained_received_movement")
        self.assertFalse(any("walk" in key for key in report))
        self.assertFalse(report["local_prediction_used"])

    def test_missing_provenance_stays_unknown_through_reconciliation(self):
        reconciler = PhysicsReconciler()
        result = reconciler.record_received_pose(
            (5, 0, 0), sequence=1, timestamp=1.0,
            source="local_cache", own_root=True)
        self.assertEqual(result["status"], "untrusted_provenance")
        report = reconciler.report()
        self.assertIsNone(report["confirmed_position"])
        self.assertEqual(report["received_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
