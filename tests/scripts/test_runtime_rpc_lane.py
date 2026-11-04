import threading
import unittest

from runtime_rpc_lane import RuntimeRpcLane


class RuntimeRpcLaneTests(unittest.TestCase):
    def test_take_is_nonblocking_until_worker_finishes(self):
        started, release = threading.Event(), threading.Event()

        def host_call(command):
            started.set()
            release.wait()
            return {'value': command['value']}

        lane = RuntimeRpcLane(host_call)
        self.addCleanup(lane.close)
        self.assertTrue(lane.submit({'value': 4}))
        self.assertTrue(started.wait(1))
        self.assertIsNone(lane.take())
        release.set()
        self.assertEqual(lane.wait(), {'value': 4})
        self.assertIsNone(lane.take())

    def test_submit_deepcopies_command_before_handoff(self):
        started, release = threading.Event(), threading.Event()
        received = []

        def host_call(command):
            started.set()
            release.wait()
            received.append(command)
            return {'ok': True}

        lane = RuntimeRpcLane(host_call)
        self.addCleanup(lane.close)
        command = {'delta': {'actions': [{'fields': {'x': [1, 2]}}]}}
        self.assertTrue(lane.submit(command))
        self.assertTrue(started.wait(1))
        command['delta']['actions'][0]['fields']['x'].append(3)
        release.set()
        lane.wait()
        self.assertEqual(received[0]['delta']['actions'][0]['fields']['x'], [1, 2])

    def test_submit_owned_transfers_exact_object_without_copying(self):
        started, release = threading.Event(), threading.Event()
        received = []

        class NoDeepcopy:
            def __deepcopy__(self, memo):
                raise AssertionError('owned payload must not be deep-copied')

        def host_call(command):
            started.set()
            release.wait()
            received.append(command)
            return {'ok': True}

        lane = RuntimeRpcLane(host_call)
        self.addCleanup(lane.close)
        command = {'delta': {'actions': [NoDeepcopy()]}}
        self.assertTrue(lane.submit_owned(command))
        self.assertTrue(started.wait(1))
        release.set()
        self.assertEqual(lane.wait(), {'ok': True})
        self.assertIs(received[0], command)
        self.assertIs(received[0]['delta']['actions'][0], command['delta']['actions'][0])

    def test_submit_owned_keeps_single_flight_and_close_contract(self):
        started, release = threading.Event(), threading.Event()

        def host_call(command):
            started.set()
            release.wait()
            return command

        lane = RuntimeRpcLane(host_call)
        self.assertTrue(lane.submit_owned({'sequence': 1}))
        self.assertTrue(started.wait(1))
        self.assertFalse(lane.submit_owned({'sequence': 2}))
        release.set()
        self.assertEqual(lane.wait(), {'sequence': 1})
        lane.close()
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            lane.submit_owned({'sequence': 3})

    def test_single_flight_preserves_order_and_refuses_overlap(self):
        first_started, release_first = threading.Event(), threading.Event()
        active = 0
        max_active = 0
        calls = []
        guard = threading.Lock()

        def host_call(command):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
                calls.append(command['sequence'])
            if command['sequence'] == 1:
                first_started.set()
                release_first.wait()
            with guard:
                active -= 1
            return command['sequence']

        lane = RuntimeRpcLane(host_call)
        self.addCleanup(lane.close)
        self.assertTrue(lane.submit({'sequence': 1}))
        self.assertTrue(first_started.wait(1))
        self.assertFalse(lane.submit({'sequence': 2}))
        release_first.set()
        self.assertEqual(lane.wait(), 1)
        self.assertTrue(lane.submit({'sequence': 2}))
        self.assertEqual(lane.wait(), 2)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(max_active, 1)

    def test_worker_error_is_raised_and_lane_can_be_reused(self):
        calls = []

        def host_call(command):
            calls.append(command)
            if command == 'fail':
                raise ValueError('host failed')
            return 'ok'

        lane = RuntimeRpcLane(host_call)
        self.addCleanup(lane.close)
        lane.submit('fail')
        with self.assertRaisesRegex(ValueError, 'host failed'):
            lane.wait()
        self.assertTrue(lane.submit('next'))
        self.assertEqual(lane.wait(), 'ok')
        self.assertEqual(calls, ['fail', 'next'])

    def test_close_cancels_pending_work_and_rejects_submissions(self):
        started, release = threading.Event(), threading.Event()

        def host_call(command):
            started.set()
            release.wait()
            return command

        lane = RuntimeRpcLane(host_call)
        self.assertTrue(lane.submit('in-flight'))
        self.assertTrue(started.wait(1))
        lane.close()
        self.assertTrue(lane.closed)
        with self.assertRaisesRegex(RuntimeError, 'closed'):
            lane.submit('later')
        release.set()


if __name__ == '__main__':
    unittest.main()
