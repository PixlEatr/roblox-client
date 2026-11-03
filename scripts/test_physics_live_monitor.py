import unittest

from physics_live_monitor import PhysicsLiveMonitor


def snapshot(*, y=10.0, vy=None, anchored=False, anchored_origin="received", pos_origin="derived_received", grounded=None):
    props = {"Character": {"$ref": "char"}}
    rootprops = {"Anchored": anchored, "Position": {"$vector3": [0.0, y, 0.0]}}
    origins = {"Anchored": anchored_origin, "Position": pos_origin}
    if vy is not None:
        rootprops["AssemblyLinearVelocity"] = {"$vector3": [0.0, vy, 0.0]}
        origins["AssemblyLinearVelocity"] = "received"
    if grounded is not None:
        rootprops["Grounded"] = grounded
        origins["Grounded"] = "received"
    return {"local_player": "player", "nodes": [
        {"id": "player", "class_name": "Player", "properties": props, "origins": {}},
        {"id": "char", "class_name": "Model", "name": "Character", "parent": "players", "properties": {}, "origins": {}},
        {"id": "root", "class_name": "Part", "name": "HumanoidRootPart", "parent": "char", "properties": rootprops, "origins": origins},
        {"id": "humanoid", "class_name": "Humanoid", "name": "Humanoid", "parent": "char", "properties": {"WalkSpeed": 16}, "origins": {"WalkSpeed": "received"}},
    ]}


class PhysicsLiveMonitorTests(unittest.TestCase):
    def test_received_fall_and_motion_are_separate_from_prediction(self):
        m = PhysicsLiveMonitor()
        first = m.observe(snapshot(y=10), timestamp=1, local_prediction={"position": [100, 100, 100]})
        self.assertTrue(first["movement_ready_to_test"])
        self.assertFalse(first["local_prediction_used_for_received_evidence"])
        second = m.observe(snapshot(y=8, vy=-20), timestamp=2)
        self.assertTrue(second["downward_motion_observed"])
        self.assertTrue(second["received_downward_motion_seen"])
        self.assertFalse(second["received_horizontal_motion_observed"])

    def test_unknown_or_predicted_anchor_does_not_claim_readiness(self):
        m = PhysicsLiveMonitor()
        report = m.observe(snapshot(anchored=False, anchored_origin="class_default_guess"), timestamp=1)
        self.assertFalse(report["movement_ready_to_test"])
        self.assertIsNone(report["anchored"])
        report = m.observe(snapshot(anchored=True), timestamp=2)
        self.assertEqual(report["readiness_reason"], "server_reports_anchored")
        self.assertFalse(report["movement_ready_to_test"])

    def test_no_grounded_claim_without_supported_received_geometry(self):
        m = PhysicsLiveMonitor()
        report = m.observe(snapshot(y=0), timestamp=1,
                           support_evidence={"supported": True, "source": "local_prediction:box"})
        self.assertFalse(report["supported_by_received_geometry"])
        self.assertIsNone(report["grounded"])
        report = m.observe(snapshot(y=0), timestamp=2,
                           support_evidence={"supported": True, "source": "received_geometry:part"})
        self.assertTrue(report["supported_by_received_geometry"])
        self.assertEqual(report["support_source"], "received_geometry:part")

    def test_root_change_resets_temporal_motion_evidence(self):
        m = PhysicsLiveMonitor()
        s = snapshot(y=10)
        m.observe(s, timestamp=1)
        newer = snapshot(y=0)
        newer["nodes"][2]["id"] = "new-root"
        report = m.observe(newer, timestamp=2)
        self.assertFalse(report["received_pose_changed"])
        self.assertFalse(report["received_downward_motion_seen"])

    def test_bounds_samples_and_validates_time(self):
        m = PhysicsLiveMonitor(max_samples=2)
        for index in range(3):
            report = m.observe(snapshot(y=10-index), timestamp=index+1)
        self.assertEqual(report["sample_count"], 2)
        with self.assertRaises(ValueError):
            m.observe(snapshot(), timestamp=3)


if __name__ == "__main__":
    unittest.main()
