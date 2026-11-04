import unittest

from physics_reconciliation import PhysicsReconciler


class PhysicsReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.r = PhysicsReconciler(correction_distance=0.5, prediction_max_age=2.0)

    def receive(self, pos, seq, stamp, **extra):
        return self.r.record_received_pose(pos, sequence=seq, timestamp=stamp,
                                           source="received", own_root=True, **extra)

    def test_prediction_and_outbound_intent_never_confirm(self):
        self.r.record_prediction((5, 0, 0), timestamp=1)
        self.r.record_outbound_intent((6, 0, 0), timestamp=2)
        report = self.r.report()
        self.assertIsNone(report["confirmed_position"])
        self.assertFalse(report["prediction_is_confirmation"])

    def test_matching_received_pose_confirms_prediction(self):
        self.r.record_prediction((5, 0, 0), timestamp=1)
        result = self.receive((5.2, 0, 0), 10, 1.1)
        self.assertEqual(result["status"], "prediction_confirmed_by_received_pose")
        self.assertTrue(result["confirmed"])
        self.assertEqual(self.r.confirmed_position, (5.2, 0, 0))

    def test_mismatch_is_correction_not_rejection(self):
        self.r.record_prediction((5, 0, 0), timestamp=1)
        result = self.receive((1, 0, 0), 10, 1.1)
        self.assertEqual(result["status"], "server_correction_observed")
        self.assertTrue(result["confirmed"])
        self.assertFalse(result.get("server_rejected", False))

    def test_received_pose_prunes_reconciled_history_and_keeps_later_predictions(self):
        self.r.record_prediction((5, 0, 0), timestamp=1)
        self.r.record_prediction((6, 0, 0), timestamp=2)
        self.r.record_prediction((7, 0, 0), timestamp=3)
        result = self.receive((6.2, 0, 0), 10, 2.5)
        self.assertEqual(result["status"], "prediction_confirmed_by_received_pose")
        self.assertAlmostEqual(result["prediction_error"], 0.2)
        self.assertEqual(result["pruned_prediction_count"], 2)
        self.assertEqual(self.r.predicted_position, (7.0, 0.0, 0.0))
        self.assertEqual(self.r.report()["local_prediction_count"], 1)

        later = self.receive((8, 0, 0), 11, 6)
        self.assertEqual(later["status"], "received_pose_confirmed")
        self.assertEqual(later["pruned_prediction_count"], 1)
        self.assertIsNone(self.r.predicted_position)

    def test_stale_or_out_of_order_samples_cannot_roll_back(self):
        self.receive((3, 0, 0), 10, 1.0)
        for result in (self.receive((2, 0, 0), 9, 1.1),
                       self.receive((2, 0, 0), 11, 0.9)):
            self.assertFalse(result["confirmed"])
            self.assertTrue(result["status"].startswith("stale_or_out_of_order"))
        self.assertEqual(self.r.confirmed_position, (3.0, 0.0, 0.0))

    def test_untrusted_local_data_does_not_confirm(self):
        result = self.r.record_received_pose((1, 2, 3), sequence=1, timestamp=1,
                                             source="local_prediction", own_root=True)
        self.assertEqual(result["status"], "untrusted_provenance")
        self.assertIsNone(self.r.confirmed_position)

    def test_generation_reset_discards_old_character_evidence(self):
        self.receive((3, 0, 0), 10, 1.0)
        self.r.reset_character()
        self.assertIsNone(self.r.confirmed_position)
        stale = self.receive((4, 0, 0), 11, 1.1, generation=0)
        self.assertEqual(stale["status"], "stale_character_generation")
        self.assertIsNone(self.r.confirmed_position)

    def test_explicit_rejection_only_marks_rejection_and_keeps_pose(self):
        self.receive((3, 0, 0), 10, 1.0)
        result = self.r.record_server_rejection(sequence=11, timestamp=1.1,
                                                source="received", own_root=True)
        self.assertTrue(result["server_rejected"])
        self.assertTrue(result["confirmed_position_unchanged"])
        self.assertEqual(self.r.confirmed_position, (3.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
