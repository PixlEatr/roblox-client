"""Offline replay regressions; no Roblox process or network is used."""
import math
import unittest

from physics_wire import position, velocity
from replay_physics import OfflinePhysicsReplay, synthetic_fixture
from physics_velocity import decode as decode_velocity
from wire_reader import Reader
from world_replication import World


class OfflinePhysicsReplayTests(unittest.TestCase):
    def test_compact_position_encoder_parses_with_independent_world_reader(self):
        source = (13.25, -7.5, 0.125)
        packed = position(source)
        reader = Reader(packed)
        decoded = World.compact_position(reader)
        reader.finish()
        self.assertEqual(len(decoded), 3)
        for actual, expected in zip(decoded, source):
            self.assertAlmostEqual(actual, expected, delta=0.02)

    def test_compact_velocity_roundtrip_is_a_field_primitive_only(self):
        source = (2.0, -1.0, 0.5)
        packed = velocity(source)
        decoded = decode_velocity(packed)
        self.assertEqual(decoded.packed, packed)
        for actual, expected in zip(decoded.vector, source):
            self.assertAlmostEqual(actual, expected, delta=0.01)
        replay = OfflinePhysicsReplay()
        result = replay.apply({"kind": "compact_velocity", "bytes": packed})
        self.assertEqual(result["event"], "compact_velocity_decoded")
        self.assertFalse(replay.report()["complete_physics_packet_codec"])
        self.assertEqual(replay.report()["received_pose_count"], 0)

    def test_local_prediction_never_confirms_received_pose(self):
        replay = OfflinePhysicsReplay()
        replay.apply({"kind": "intent", "direction": [1, 0, 0]})
        result = replay.apply({"kind": "step", "dt": 0.25, "timestamp": 1.0})
        before = replay.report()
        self.assertFalse(result["confirmed"])
        self.assertEqual(before["reconciliation"]["confirmed_position"], None)
        self.assertEqual(before["received_pose_count"], 0)
        replay.apply({"kind": "received_pose", "position": [1, 0, 0], "sequence": 1,
                      "timestamp": 1.1, "source": "local_prediction", "own_root": True})
        after = replay.report()
        self.assertEqual(after["reconciliation"]["confirmed_position"], None)
        self.assertEqual(after["received_pose_count"], 0)

    def test_received_pose_confirms_separately_and_local_stop_holds_pose(self):
        replay = OfflinePhysicsReplay()
        replay.apply({"kind": "intent", "direction": [1, 0, 0]})
        replay.apply({"kind": "step", "dt": 0.25, "timestamp": 1.0})
        replay.apply({"kind": "received_pose", "position": [3.9, 0, 0], "sequence": 1,
                      "timestamp": 1.1, "source": "received", "own_root": True})
        self.assertEqual(replay.report()["reconciliation"]["confirmed_position"], (3.9, 0.0, 0.0))
        replay.apply({"kind": "stop"})
        stopped_at = replay.controller.controller.position
        for n in range(2, 5):
            replay.apply({"kind": "step", "dt": 0.25, "timestamp": float(n)})
        self.assertEqual(replay.controller.controller.position, stopped_at)
        report = replay.report()
        self.assertTrue(report["controller_stopped"])
        self.assertEqual(report["reconciliation"]["confirmed_position"], (3.9, 0.0, 0.0))
        self.assertEqual(report["received_pose_count"], 1)

    def test_fixture_is_explicitly_offline_and_separates_received_evidence(self):
        replay = OfflinePhysicsReplay()
        replay.replay(synthetic_fixture())
        report = replay.report()
        self.assertFalse(report["network_io"])
        self.assertFalse(report["complete_physics_packet_codec"])
        self.assertEqual(report["received_pose_count"], 5)
        self.assertEqual(report["reconciliation"]["received_sample_count"], 5)
        self.assertGreater(report["local_steps"], 0)
        self.assertTrue(report["controller_stopped"])
        self.assertTrue(math.isfinite(report["received_movement"]["observed_distance_studs"]))


if __name__ == "__main__":
    unittest.main()
