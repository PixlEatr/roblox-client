import unittest

from animation_intents import AnimationIntentOutbox


def intent(sequence, kind='Stop', **parameters):
    state = {'playing': kind != 'Stop', 'priority': 2, 'looped': True,
             'time_position': 0., 'speed': 1., 'weight_current': 0., 'weight_target': 1.}
    return {'sequence': sequence, 'kind': kind, 'animator_id': '1:20',
            'track_id': 'local:track1', 'animation_reference': 'rbxassetid://123',
            'parameters': {**(parameters or {'fade_time': .1}), 'effective_state': state}}


class AnimationIntentOutboxTests(unittest.TestCase):
    def test_ordered_calls_drain_once_and_response_redelivery_does_not_replay(self):
        outbox = AnimationIntentOutbox()
        batch = [intent(1, 'Play', fade_time=.1, weight=1, speed=1),
                 intent(2, 'AdjustSpeed', speed=.8), intent(3)]
        outbox.submit(batch)
        batch[0]['parameters']['speed'] = 99
        drained = outbox.drain()
        self.assertEqual([i['kind'] for i in drained], ['Play', 'AdjustSpeed', 'Stop'])
        self.assertEqual(drained[0]['parameters']['speed'], 1)
        self.assertEqual(outbox.drain(), [])
        outbox.submit(batch)
        self.assertEqual(outbox.drain(), [])
        self.assertEqual(outbox.report()['duplicate_sequences'], 3)

    def test_invalid_batch_is_atomic_and_backpressure_preserves_pending_calls(self):
        outbox = AnimationIntentOutbox()
        with self.assertRaises(ValueError):
            outbox.submit([intent(1), intent(2, 'AdjustSpeed', speed=float('nan'))])
        self.assertEqual(outbox.last_sequence, 0)
        outbox.submit([intent(i) for i in range(1, 1025)])
        with self.assertRaises(ValueError):
            outbox.submit([intent(1025)])
        self.assertEqual(outbox.last_sequence, 1024)
        self.assertEqual(len(outbox.drain()), 1024)
        outbox.submit([intent(1025)])
        self.assertEqual(len(outbox.drain()), 1)

    def test_invalid_priority_loop_flag_and_reordered_batch_are_rejected(self):
        for batch in ([intent(2), intent(1)],
                      [intent(1, 'Priority', priority=True)],
                      [intent(1, 'Looped', looped=1)]):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                AnimationIntentOutbox().submit(batch)

    def test_runtime_records_prior_operations_even_when_the_luau_job_fails(self):
        from persistent_runtime import PersistentClientScriptRuntime
        runtime = PersistentClientScriptRuntime(lambda: None, 123)
        self.addCleanup(runtime.close)
        outbox = runtime.enable_animation_intents()
        runtime._record({'ok': False, 'animation_intents': [intent(1)],
                         'error': 'later authored operation failed'})
        self.assertEqual([entry['sequence'] for entry in outbox.drain()], [1])
        runtime._record({'ok': True, 'animation_intents': [intent(1)]})
        self.assertEqual(outbox.drain(), [])


if __name__ == '__main__':
    unittest.main()
