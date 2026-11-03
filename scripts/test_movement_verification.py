import unittest

from movement_verification import MovementVerifier


class MovementVerifierTests(unittest.TestCase):
    def pose(self, verifier, x, t, **kwargs):
        verifier.add_pose((x, 0, 0), timestamp=t, source='received', own_root=True, **kwargs)

    def test_sustained_received_movement_has_explicit_evidence_label(self):
        verifier = MovementVerifier(min_duration=1, min_distance=2, min_samples=4)
        for t, x in ((0, 0), (.5, 2), (1, 4), (1.5, 6)):
            self.pose(verifier, x, t)
        report = verifier.report()
        self.assertTrue(report['sustained_received_movement'])
        self.assertEqual(report['classification'], 'sustained_received_movement')
        self.assertEqual(report['evidence_label'], 'server_received_own_root_poses')
        self.assertEqual(report['sample_count'], 4)
        self.assertAlmostEqual(report['observed_distance_studs'], 6)
        self.assertFalse(report['local_prediction_used'])

    def test_local_prediction_and_non_own_instance_are_rejected(self):
        verifier = MovementVerifier()
        with self.assertRaisesRegex(ValueError, 'received pose'):
            verifier.add_pose((0, 0, 0), timestamp=1, source='local_prediction', own_root=True)
        with self.assertRaisesRegex(ValueError, 'own character root'):
            verifier.add_pose((0, 0, 0), timestamp=1, source='received', own_root=False)
        self.assertEqual(verifier.report()['sample_count'], 0)
        self.assertEqual(verifier.report()['rejected_sample_count'], 2)

    def test_single_teleport_is_not_classified_as_sustained_movement(self):
        verifier = MovementVerifier()
        self.pose(verifier, 0, 1)
        self.pose(verifier, 1000, 1.1)
        report = verifier.report()
        self.assertEqual(report['classification'], 'teleport_or_implausible_speed')
        self.assertFalse(report['sustained_received_movement'])

    def test_stale_samples_do_not_confirm_sustained_movement(self):
        verifier = MovementVerifier(max_gap=.5)
        for t, x in ((0, 0), (1, 2), (2, 4), (3, 6)):
            self.pose(verifier, x, t)
        report = verifier.report()
        self.assertEqual(report['classification'], 'stale_or_gapped_samples')
        self.assertFalse(report['sustained_received_movement'])
        self.assertEqual(report['stale_gap_segments'], 3)

    def test_displacement_shorter_than_duration_or_sample_threshold_is_not_confirmed(self):
        verifier = MovementVerifier(min_duration=1, min_samples=4)
        self.pose(verifier, 0, 0)
        self.pose(verifier, 10, .2)
        self.assertEqual(verifier.report()['classification'], 'observed_displacement')
        self.assertFalse(verifier.report()['sustained_received_movement'])

    def test_rejects_nonmonotonic_or_invalid_data(self):
        verifier = MovementVerifier()
        self.pose(verifier, 0, 1)
        with self.assertRaisesRegex(ValueError, 'monotonically'):
            self.pose(verifier, 1, 1)
        with self.assertRaisesRegex(ValueError, 'bounded finite'):
            verifier.add_pose((float('nan'), 0, 0), timestamp=2, source='received', own_root=True)
        self.assertEqual(verifier.report()['sample_count'], 1)

    def test_bounds_history_for_long_sessions(self):
        verifier = MovementVerifier(history_limit=4, min_samples=2)
        for i in range(10):
            self.pose(verifier, i, i + 1)
        report = verifier.report()
        self.assertEqual(report['sample_count'], 4)
        self.assertAlmostEqual(report['observed_distance_studs'], 3)
        self.assertAlmostEqual(report['observed_duration_seconds'], 3)


if __name__ == '__main__':
    unittest.main()
