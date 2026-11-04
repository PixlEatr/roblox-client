import unittest

from physics_trial_finalization import (
    finalize_physics_reports,
    session_end_reason,
)


class TrialStub:
    def __init__(self, *, already_stopped=False, reason=None):
        self.stopped = already_stopped
        self.reason = reason
        self.stop_calls = []
        self.tick_calls = 0

    def stop(self, reason):
        self.stop_calls.append(reason)
        if not self.stopped:
            self.stopped = True
            self.reason = reason
        return {
            'running': not self.stopped,
            'stop_reason': self.reason,
            'network_send_attempt_count': 1,
            'local_prediction': {'private': True},
        }

    def tick(self, **kwargs):
        self.tick_calls += 1
        raise AssertionError('finalization must not advance or send another physics frame')


class WalkStub:
    def __init__(self):
        self.calls = []

    def tick(self, **kwargs):
        self.calls.append(kwargs)
        return {'state': 'stopped', 'reason': 'disconnected',
                'trial_running_at_finalization': kwargs['trial_report']['running']}


class PhysicsTrialFinalizationTests(unittest.TestCase):
    def test_server_disconnect_terminalizes_fall_and_walk_without_advancing(self):
        trial = TrialStub()
        walk = WalkStub()
        report = {
            'run_id': 'run-1',
            'termination': {'reason': 'peer_closed'},
            'experimental_physics': {'running': True, 'stop_reason': None},
        }

        finalize_physics_reports(report, trial, walk, now=12.5)

        self.assertEqual(trial.stop_calls, ['server_disconnected'])
        self.assertEqual(trial.tick_calls, 0)
        self.assertFalse(report['experimental_physics']['running'])
        self.assertEqual(report['experimental_physics']['stop_reason'], 'server_disconnected')
        self.assertNotIn('local_prediction', report['experimental_physics'])
        self.assertEqual(report['experimental_physics_walk']['reason'], 'disconnected')
        self.assertFalse(report['experimental_physics_walk']['trial_running_at_finalization'])
        self.assertEqual(walk.calls[0]['connected'], False)
        self.assertIsNone(walk.calls[0]['execute_source'])
        self.assertEqual(walk.calls[0]['results'], {})

    def test_existing_terminal_reason_is_preserved(self):
        trial = TrialStub(already_stopped=True, reason='send_result_ambiguous_no_retry')
        report = {'termination': {'reason': 'peer_closed'}}

        finalize_physics_reports(report, trial, now=14.0)

        self.assertEqual(trial.stop_calls, ['server_disconnected'])
        self.assertEqual(report['experimental_physics']['stop_reason'],
                         'send_result_ambiguous_no_retry')
        self.assertFalse(report['experimental_physics']['running'])

    def test_duration_disconnect_classification(self):
        self.assertEqual(session_end_reason({'duration_limit_reached': True}),
                         'session_duration_reached')
        self.assertEqual(session_end_reason({'application_close': True}),
                         'server_disconnected')

    def test_continuous_character_physics_uses_its_public_report_key(self):
        trial = TrialStub()
        report = {}
        finalize_physics_reports(report, trial, now=14.0,
                                 report_key='character_physics')
        self.assertIn('character_physics', report)
        self.assertNotIn('experimental_physics', report)
        self.assertFalse(report['character_physics']['running'])

    def test_normal_minute_walk_uses_character_walk_report_key_on_shutdown(self):
        trial = TrialStub()
        walk = WalkStub()
        report = {}
        finalize_physics_reports(report, trial, walk, now=14.0,
                                 report_key='character_physics',
                                 walk_report_key='character_walk')
        self.assertIn('character_physics', report)
        self.assertIn('character_walk', report)
        self.assertNotIn('experimental_physics_walk', report)
        self.assertEqual(report['character_walk']['reason'], 'disconnected')


if __name__ == '__main__':
    unittest.main()
