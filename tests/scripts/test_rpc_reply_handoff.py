"""Urgent RPC replies use the just-freed single-flight host lane."""
import collections
import unittest
from unittest import mock

from persistent_runtime import PersistentClientScriptRuntime


class FakeOwnedLane:
    """Deterministic RuntimeRpcLane stand-in with observable ownership."""

    def __init__(self, *results):
        self.results = collections.deque(results)
        self.submitted = []

    def take(self):
        return self.results.popleft() if self.results else None

    def submit_owned(self, command):
        self.submitted.append(command)
        return True


class FakeHost:
    failure_context = None


class RpcReplyHandoffTests(unittest.TestCase):
    def make_runtime(self, lane, responses):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.failure = None
        runtime.failure_context = None
        runtime.host = FakeHost()
        runtime._background_context = {'op': 'poll'}
        runtime._background_lane = lane
        runtime.remote_server_results = collections.deque(responses)
        runtime.last_poll = 100.0
        runtime.background_budget_ms = 50
        runtime.stats = {}
        runtime.submitted = {}
        runtime.results = {}
        runtime.recorded_results = []
        runtime._record = runtime.recorded_results.append
        return runtime

    def test_completed_operation_returns_result_and_submits_fifo_capped_batch_same_poll(self):
        prior = {'ok': True, 'values': ['prior operation']}
        responses = [dict(node='node', request_id=i, ok=True, values=[i])
                     for i in range(1, 131)]
        lane = FakeOwnedLane(prior)
        runtime = self.make_runtime(lane, responses)

        with mock.patch('persistent_runtime.time.monotonic', return_value=100.0):
            returned = runtime.poll_background()

        self.assertIs(returned, prior)
        self.assertEqual(runtime.recorded_results, [prior])
        self.assertEqual(len(lane.submitted), 1)
        command = lane.submitted[0]
        self.assertEqual(command['op'], 'remote_server_results')
        self.assertEqual(command['timeout_ms'], 20)
        self.assertEqual(command['responses'], responses[:128])
        self.assertEqual(list(runtime.remote_server_results), responses[128:])
        self.assertEqual(runtime._background_context, {'op': 'remote_server_results'})
        # The submitted host call is now the single in-flight operation. A
        # subsequent poll must neither submit it twice nor consume more replies.
        self.assertIsNone(runtime.poll_background())
        self.assertEqual(len(lane.submitted), 1)
        self.assertEqual(list(runtime.remote_server_results), responses[128:])

    def test_completed_operation_without_reply_returns_normally(self):
        prior = {'ok': True, 'values': ['prior operation']}
        lane = FakeOwnedLane(prior)
        runtime = self.make_runtime(lane, [])

        returned = runtime.poll_background()

        self.assertIs(returned, prior)
        self.assertEqual(runtime.recorded_results, [prior])
        self.assertEqual(lane.submitted, [])
        self.assertIsNone(runtime._background_context)

    def test_inflight_operation_does_not_overlap_or_consume_queued_reply(self):
        prior = {'ok': True, 'values': ['prior operation']}
        response = dict(node='node', request_id=1, ok=True, values=['reply'])
        lane = FakeOwnedLane(None)
        runtime = self.make_runtime(lane, [response])

        self.assertIsNone(runtime.poll_background())
        self.assertEqual(lane.submitted, [])
        self.assertEqual(list(runtime.remote_server_results), [response])
        self.assertEqual(runtime._background_context, {'op': 'poll'})

        lane.results.append(prior)
        # Keep the last poll timestamp recent: finishing the outstanding call
        # should hand the newly free lane directly to the queued reply.
        runtime.last_poll = 100.0
        with mock.patch('persistent_runtime.time.monotonic', return_value=100.0):
            returned = runtime.poll_background()

        self.assertIs(returned, prior)
        self.assertEqual(runtime.recorded_results, [prior])
        self.assertEqual(len(lane.submitted), 1)
        self.assertEqual(lane.submitted[0]['op'], 'remote_server_results')
        self.assertEqual(lane.submitted[0]['responses'], [response])
        self.assertFalse(runtime.remote_server_results)
        self.assertEqual(runtime._background_context, {'op': 'remote_server_results'})


if __name__ == '__main__':
    unittest.main()
