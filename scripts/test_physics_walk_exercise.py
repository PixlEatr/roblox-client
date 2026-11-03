import unittest
from unittest.mock import patch

from physics_walk_exercise import PhysicsWalkExercise


SOURCE = 'task.wait(60); return "walk_completed"'


class PhysicsWalkExerciseTests(unittest.TestCase):
    def test_world_sync_wait_does_not_consume_the_luau_job_timeout(self):
        exercise, _, closes, execute, close = self.make()
        with patch('physics_walk_exercise.time.monotonic', side_effect=[100.,108.]):
            exercise.tick(0.,{'network_send_attempt_count':1},execute,{},True,close)
        pending=exercise.tick(65.,{},execute,{},True,close)
        self.assertEqual(pending['state'],'running')
        self.assertEqual(pending['submission_preparation_seconds'],8.)
        complete=exercise.tick(68.,{},execute,{7:{'ok':True,'state':'completed',
            'completed':True,'values':['walk_completed']}},True,close)
        self.assertEqual(complete['state'],'completed')
        self.assertEqual(closes,['closed'])

    def make(self):
        exercise = PhysicsWalkExercise(SOURCE)
        submissions = []
        closes = []

        def execute(source, *, timeout_ms, label):
            submissions.append((source, timeout_ms, label))
            return {'ok': True, 'job_id': 7, 'state': 'running', 'completed': False}

        def close():
            closes.append('closed')

        return exercise, submissions, closes, execute, close

    def test_waits_for_actual_send_then_submits_exactly_once_and_completes_locally(self):
        exercise, submissions, closes, execute, close = self.make()
        waiting = exercise.tick(1.0, {'network_send_attempt_count': 0}, execute, {}, True, close)
        self.assertEqual(waiting['state'], 'waiting_for_physics_send')
        self.assertEqual(submissions, [])
        running = exercise.tick(2.0, {'network_send_attempt_count': 1}, execute, {}, True, close)
        self.assertEqual(running['state'], 'running')
        self.assertEqual(submissions, [(SOURCE, 1000, '<physics-walk>')])
        exercise.tick(3.0, {'network_send_attempt_count': 9}, execute, {}, True, close)
        self.assertEqual(len(submissions), 1)
        complete = exercise.tick(62.0, {}, execute,
                                 {7: {'ok': True, 'state': 'completed', 'completed': True,
                                      'values': ['walk_completed']}}, True, close)
        self.assertEqual(complete['state'], 'completed')
        self.assertTrue(complete['luau_completed'])
        self.assertFalse(complete['server_movement_confirmed'])
        self.assertEqual(complete['walk_seconds_verified'], 0)
        self.assertEqual(closes, ['closed'])
        exercise.tick(63.0, {}, execute, {}, True, close)
        self.assertEqual(closes, ['closed'])

    def test_submission_exception_is_not_retried(self):
        exercise, _, closes, _, close = self.make()
        calls = []

        def ambiguous(*args, **kwargs):
            calls.append(1)
            raise OSError('ambiguous transport result')

        report = exercise.tick(10, {'network_send_attempt_count': 1}, ambiguous, {}, True, close)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['reason'], 'luau_submission_error')
        self.assertEqual(report['error_type'], 'OSError')
        self.assertEqual(calls, [1])
        exercise.tick(11, {'network_send_attempt_count': 2}, ambiguous, {}, True, close)
        self.assertEqual(calls, [1])
        self.assertEqual(closes, ['closed'])

    def test_failed_job_stops_and_closes_once(self):
        exercise, submissions, closes, execute, close = self.make()
        exercise.tick(0, {'network_send_attempt_count': 1}, execute, {}, True, close)
        report = exercise.tick(1, {}, execute,
                               {7: {'ok': False, 'state': 'failed', 'error_type': 'RuntimeError'}},
                               True, close)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['reason'], 'luau_job_failed')
        self.assertEqual(report['error_type'], 'RuntimeError')
        self.assertEqual(len(submissions), 1)
        self.assertEqual(closes, ['closed'])

    def test_timeout_is_bounded_to_65_seconds(self):
        exercise, _, closes, execute, close = self.make()
        exercise.tick(10, {'network_send_attempt_count': 1}, execute, {}, True, close)
        report = exercise.tick(75, {}, execute, {}, True, close)
        self.assertEqual(report['state'], 'timed_out')
        self.assertEqual(report['reason'], 'luau_job_timeout')
        self.assertEqual(report['elapsed_since_submission_seconds'], 65.0)
        self.assertEqual(closes, ['closed'])

    def test_disconnect_and_driver_stop_close_without_script_submission(self):
        for stop_case in ('disconnect', 'driver'):
            with self.subTest(stop_case=stop_case):
                exercise, submissions, closes, execute, close = self.make()
                connected = stop_case != 'disconnect'
                trial_report = {'stop_reason': 'max_frames'} if stop_case == 'driver' else {}
                report = exercise.tick(1, trial_report, execute, {}, connected, close)
                self.assertEqual(report['state'], 'stopped')
                self.assertEqual(len(submissions), 0)
                self.assertEqual(closes, ['closed'])

    def test_driver_stop_before_job_completion_is_terminal(self):
        exercise, submissions, closes, execute, close = self.make()
        exercise.tick(1, {'network_send_attempt_count': 1}, execute, {}, True, close)
        report = exercise.tick(2, {'stop_reason': 'max_frames'}, execute, {}, True, close)
        self.assertEqual(report['state'], 'stopped')
        self.assertEqual(report['reason'], 'physics_driver_stopped')
        self.assertEqual(len(submissions), 1)
        self.assertEqual(closes, ['closed'])

    def test_early_or_unexpected_completion_never_claims_walk(self):
        exercise, _, closes, _, close = self.make()

        def early_execute(*args, **kwargs):
            return {'ok': True, 'job_id': 7, 'state': 'completed',
                    'completed': True, 'values': ['walk_completed']}

        report = exercise.tick(1, {'network_send_attempt_count': 1}, early_execute, {}, True, close)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['reason'], 'luau_completed_too_early')
        self.assertFalse(report['luau_completed'])
        self.assertEqual(report['walk_seconds_verified'], 0)
        self.assertEqual(closes, ['closed'])

        exercise, _, closes, execute, close = self.make()
        exercise.tick(1, {'network_send_attempt_count': 1}, execute, {}, True, close)
        report = exercise.tick(61, {}, execute,
                               {7: {'ok': True, 'completed': True, 'state': 'completed',
                                    'values': ['wrong']}}, True, close)
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['reason'], 'unexpected_luau_result')
        self.assertFalse(report['luau_completed'])
        self.assertEqual(report['walk_seconds_verified'], 0)
        self.assertEqual(closes, ['closed'])


if __name__ == '__main__':
    unittest.main()
