"""Real local physics state; synthetic clocks and worlds only in fixtures."""
import copy
import unittest

from physics_job_statistics import DEFAULT_WORLD_BOUNDS, PhysicsJobStatistics, native_world_bounds_match
from physics_runtime_prediction import RuntimePhysicsPrediction
from session_integrity import SessionIntegrityState
from test_world_snapshot import fixture


class PhysicsPingIntegrityTests(unittest.TestCase):
    def test_compiled_runtime_and_solver_consume_the_same_received_gravity(self):
        from persistent_runtime import PersistentClientScriptRuntime
        world, _ = fixture()
        world.instances[(1, 6)]['properties']['Gravity'] = 100.0
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        state = SessionIntegrityState()
        try:
            with state.observe_containers():
                first = runtime.execute('return workspace.Gravity')
                self.assertTrue(first['ok'], first)
                self.assertEqual(first['values'], [100.0])
                self.assertEqual(runtime.movement_preview.bridge.controller.gravity, 100.0)
                world.instances[(1, 6)]['properties']['Gravity'] = 40.0
                world.touch((1, 6), ['Gravity'])
                second = runtime.execute('return workspace.Gravity')
                self.assertEqual(second['values'], [40.0])
                self.assertEqual(runtime.movement_preview.bridge.controller.gravity, 40.0)
            self.assertGreater(state.public_report()['physics_comparison_checks']['gravity'], 0)
            self.assertGreater(state.public_report()['physics_comparison_checks']['smoothing'], 0)
            self.assertEqual(state.public_report()['observed_global_flags'], 0)
            self.assertEqual(state.status_word(), state.public_report()['observed_global_flags'])
        finally:
            runtime.close()

    def test_job_intervals_use_native_average_recurrence_and_first_sample_rule(self):
        times = iter((1.0, 1.1, 1.3, 1.6))
        stats = PhysicsJobStatistics(clock=lambda: next(times))
        stats.completed()
        self.assertIsNone(stats.report()['mean_interval_seconds'])
        self.assertEqual(stats.report()['interval_samples'], 0)
        for _ in range(3):
            stats.completed()
        report = stats.report()
        self.assertEqual(report['completed_jobs'], 4)
        self.assertEqual(report['interval_samples'], 3)
        self.assertAlmostEqual(report['last_interval_seconds'], 0.3)
        self.assertAlmostEqual(report['mean_interval_seconds'], 0.11475)
        self.assertAlmostEqual(report['interval_variance_seconds_squared'], 0.002144565625)

    def test_changed_live_coefficient_affects_statistics_and_records_sticky_failure(self):
        state, other = SessionIntegrityState(), SessionIntegrityState()
        times = iter((0.0, 1.0, 3.0, 4.0, 5.0))
        stats = PhysicsJobStatistics(clock=lambda: next(times))
        with state.observe_containers():
            stats.completed()
            stats.completed()
            stats.smoothing = 0.2
            stats.completed()
            self.assertAlmostEqual(stats.mean_interval, 1.2)
            stats.smoothing = 0.05
            stats.completed()
        with other.observe_containers():
            stats.completed()
        report = state.public_report()
        self.assertEqual(report['physics_comparison_checks']['smoothing'], 4)
        self.assertEqual(report['physics_comparison_failures']['smoothing'], 1)
        self.assertEqual(report['observed_global_flags'], 0x80)
        self.assertEqual(other.public_report()['observed_global_flags'], 0)
        self.assertEqual(state.status_word(), 0x80)
        self.assertEqual(other.status_word(), 0)

    def test_invalid_completion_clock_does_not_create_a_successful_measurement(self):
        times = iter((2.0, 1.0, float('nan')))
        stats = PhysicsJobStatistics(clock=lambda: next(times))
        state = SessionIntegrityState()
        with state.observe_containers():
            stats.completed()
            before = stats.report()
            for _ in range(2):
                with self.assertRaises(ValueError):
                    stats.completed()
                self.assertEqual(stats.report(), before)
        self.assertEqual(state.public_report()['physics_comparison_checks']['smoothing'], 1)

    def test_received_gravity_changes_actual_fall_and_preserves_received_world(self):
        world, _ = fixture()
        workspace = world.instances[(1, 6)]
        workspace['properties']['Gravity'] = 0.0
        predictor = RuntimePhysicsPrediction()
        state = SessionIntegrityState()
        with state.observe_containers():
            predictor.advance(world, 123, 10.0)
            no_gravity = predictor.advance(world, 123, 10.1)
            self.assertEqual(no_gravity['predicted_position'][1], 2.0)
            workspace['properties']['Gravity'] = 100.0
            world.touch((1, 6), ['Gravity'])
            before = copy.deepcopy(world.instances)
            falling = predictor.advance(world, 123, 10.2)
        self.assertLess(falling['predicted_position'][1], 2.0)
        self.assertEqual(predictor.bridge.controller.gravity, 100.0)
        self.assertEqual(falling['gravity'], {'source': 'received_workspace',
                         'configured_gravity': 100.0, 'comparison_available': True})
        self.assertEqual(world.instances, before)
        self.assertEqual(state.public_report()['physics_comparison_checks']['gravity'], 3)
        self.assertEqual(state.public_report()['observed_global_flags'], 0)

    def test_solver_value_mismatch_is_observed_without_repairing_it_before_the_check(self):
        world, _ = fixture()
        world.instances[(1, 6)]['properties']['Gravity'] = 100.0
        predictor = RuntimePhysicsPrediction()
        state = SessionIntegrityState()
        with state.observe_containers():
            predictor.advance(world, 123, 1.0)
            predictor.bridge.controller.gravity = 25.0
            predictor.advance(world, 123, 1.1)
        self.assertEqual(predictor.bridge.controller.gravity, 25.0)
        self.assertEqual(state.public_report()['physics_comparison_failures']['gravity'], 1)
        self.assertEqual(state.public_report()['observed_global_flags'], 0x80)

    def test_unknown_gravity_is_not_a_check_and_world_replacement_resets_owned_state(self):
        world, _ = fixture()
        predictor = RuntimePhysicsPrediction()
        state = SessionIntegrityState()
        with state.observe_containers():
            predictor.advance(world, 123, 1.0)
            for invalid in (True, None, float('nan'), float('inf'), '100'):
                world.instances[(1, 6)]['properties']['Gravity'] = invalid
                predictor.advance(world, 123, 1.1)
        self.assertEqual(state.public_report()['physics_comparison_checks']['gravity'], 0)
        self.assertFalse(predictor.report()['gravity']['comparison_available'])
        replacement, _ = fixture()
        replacement.instances[(1, 6)]['properties']['Gravity'] = 40.0
        result = predictor.advance(replacement, 123, 20.0)
        self.assertEqual(result['gravity']['configured_gravity'], 40.0)
        self.assertEqual(result['job_statistics']['completed_jobs'], 1)
        self.assertIsNone(result['job_statistics']['mean_interval_seconds'])
        self.assertEqual(result['simulated_seconds'], 0.0)

    def test_native_world_bounds_defaults_and_comparison_thresholds(self):
        self.assertTrue(native_world_bounds_match(DEFAULT_WORLD_BOUNDS))
        for index, boundary in enumerate((-950000.0, -960000.0, -970000.0,
                                          960000.0, 970000.0)):
            with self.subTest(component=index, boundary=True):
                values = list(DEFAULT_WORLD_BOUNDS)
                values[index] = boundary
                self.assertTrue(native_world_bounds_match(tuple(values)))
            with self.subTest(component=index, boundary=False):
                values[index] = boundary + (1.0 if index < 3 else -1.0)
                self.assertFalse(native_world_bounds_match(tuple(values)))

    def test_world_bounds_failure_is_sticky_and_session_owned(self):
        times = iter((0.0, 1.0, 2.0, 3.0))
        stats = PhysicsJobStatistics(clock=lambda: next(times))
        state, other = SessionIntegrityState(), SessionIntegrityState()
        with state.observe_containers():
            stats.completed()
            values = list(DEFAULT_WORLD_BOUNDS)
            values[0] = 0.0
            stats.world_bounds = tuple(values)
            stats.completed()
            stats.world_bounds = DEFAULT_WORLD_BOUNDS
            stats.completed()
        with other.observe_containers():
            stats.completed()
        report = state.public_report()
        self.assertEqual(report['physics_comparison_checks']['world_bounds'], 3)
        self.assertEqual(report['physics_comparison_failures']['world_bounds'], 1)
        self.assertEqual(state.status_word(), 0x80)
        self.assertEqual(other.status_word(), 0)
        self.assertEqual(other.public_report()['physics_comparison_checks']['world_bounds'], 1)

    def test_finite_received_gravity_uses_clamp_and_float32_storage(self):
        world, _ = fixture()
        predictor = RuntimePhysicsPrediction()
        for raw, expected in ((-4.0, 0.0), (1001.0, 1000.0), (10**400, 1000.0),
                              (196.2, 196.1999969482422)):
            world.instances[(1, 6)]['properties']['Gravity'] = raw
            predictor.advance(world, 123, 1.0)
            self.assertEqual(predictor.bridge.controller.gravity, expected)


if __name__ == '__main__':
    unittest.main()
