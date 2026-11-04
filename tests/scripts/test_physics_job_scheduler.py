"""Exercise actual pacing decisions and their session-scoped observations."""
import unittest

from physics_job_scheduler import PhysicsJobScheduler
from physics_runtime_prediction import RuntimePhysicsPrediction
from session_integrity import SessionIntegrityState
from test_world_snapshot import fixture


class PhysicsJobSchedulerTests(unittest.TestCase):
    def test_mode_changes_actual_pacing_and_records_sticky_native_bit(self):
        state, clean = SessionIntegrityState(), SessionIntegrityState()
        paced, fast = PhysicsJobScheduler(), PhysicsJobScheduler()
        fast.configured_interval_enabled = False
        with state.observe_containers():
            self.assertTrue(fast.admit(0.0))
            self.assertTrue(fast.admit(0.005))
            fast.configured_interval_enabled = True
            self.assertTrue(fast.admit(0.010))
        with clean.observe_containers():
            self.assertTrue(paced.admit(0.0))
            self.assertFalse(paced.admit(0.005))
            self.assertFalse(paced.admit(0.010))
            self.assertTrue(paced.admit(0.017))
        self.assertEqual(state.public_report()['physics_comparison_checks']['scheduler'], 3)
        self.assertEqual(state.public_report()['physics_comparison_failures']['scheduler'], 2)
        self.assertEqual(state.public_report()['observed_global_flags'], 0x200000)
        self.assertEqual(clean.public_report()['observed_global_flags'], 0)

    def test_late_job_skips_missed_deadlines_without_creating_a_burst(self):
        scheduler = PhysicsJobScheduler()
        self.assertTrue(scheduler.admit(0.0))
        self.assertTrue(scheduler.admit(10.0))
        self.assertGreater(scheduler.skipped_intervals, 500)
        self.assertFalse(scheduler.admit(10.0))
        self.assertEqual(scheduler.admitted_jobs, 2)

    def test_configured_interval_controls_admission_without_changing_mode(self):
        scheduler = PhysicsJobScheduler()
        scheduler.configured_interval_seconds = 0.05
        self.assertTrue(scheduler.admit(1.0))
        self.assertFalse(scheduler.admit(1.02))
        self.assertTrue(scheduler.admit(1.05))
        self.assertEqual(scheduler.report()['effective_interval_seconds'], 0.05)
        self.assertTrue(scheduler.configured_interval_enabled)

    def test_invalid_clock_or_configuration_does_not_claim_a_scheduled_job(self):
        scheduler = PhysicsJobScheduler()
        scheduler.admit(2.0)
        for value in (1.0, -1.0, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                scheduler.admit(value)
        self.assertEqual(scheduler.admitted_jobs, 1)
        scheduler.configured_interval_enabled = 1
        with self.assertRaises(ValueError):
            scheduler.admit(3.0)
        self.assertEqual(scheduler.admitted_jobs, 1)

    def test_prediction_steps_are_paced_and_between_job_intents_are_preserved(self):
        world, _ = fixture()
        predictor = RuntimePhysicsPrediction()
        predictor.advance(world, 123, 0.0)
        result = predictor.advance(world, 123, 0.005, intents=[
            {'sequence': 1, 'kind': 'Move', 'instance_id': '1:5',
             'vector': [1.0, 0.0, 0.0], 'relative_to_camera': False}])
        self.assertEqual(result['job_statistics']['completed_jobs'], 1)
        self.assertEqual(result['accepted_intents'], 1)
        self.assertEqual(result['predicted_position'][0], 1.0)
        result = predictor.advance(world, 123, 0.017)
        self.assertEqual(result['job_statistics']['completed_jobs'], 2)
        self.assertGreater(result['predicted_position'][0], 1.0)
        predictor._job_scheduler.configured_interval_enabled = False
        predictor.advance(world, 123, 0.034)
        result = predictor.advance(world, 123, 0.039)
        self.assertEqual(result['job_statistics']['completed_jobs'], 4)
        self.assertFalse(result['job_scheduler']['configured_interval_enabled'])


if __name__ == '__main__':
    unittest.main()
