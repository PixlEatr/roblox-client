"""Retain the first worker failure without retrying a lost script state."""
import queue
import threading
import unittest
from unittest.mock import Mock

from persistent_runtime import PersistentLuauHost


class PersistentWorkerFailureTests(unittest.TestCase):
    def host(self, failure):
        host = object.__new__(PersistentLuauHost)
        host.closed = False
        host.text_service = None
        host.text_service_factory = None
        host.failure_reason = host.failure_context = None
        host.lock = threading.RLock()
        host.process = Mock()
        host.process.poll.return_value = None
        host.responses = Mock()
        host.responses.get.side_effect = failure
        host.close = Mock(side_effect=lambda **_: setattr(host, 'closed', True))
        return host

    def test_response_timeout_retains_nonempty_cause_and_stops_followups(self):
        host = self.host(queue.Empty())
        with self.assertRaisesRegex(TimeoutError, 'response deadline exceeded'):
            host.call({'op': 'update', 'delta': {'actions': []}})
        host.close.assert_called_once_with(graceful=False)
        self.assertEqual(host.failure_context['operation'], 'update')
        self.assertEqual(host.failure_context['error_type'], 'Empty')
        self.assertNotIn('delta', host.failure_context)
        with self.assertRaisesRegex(RuntimeError, 'response deadline exceeded'):
            host.call({'op': 'poll'})
        self.assertEqual(host.process.stdin.write.call_count, 1)

    def test_protocol_failure_is_not_replaced_by_generic_worker_loss(self):
        host = self.host(RuntimeError('Invalid persistent result frame'))
        with self.assertRaisesRegex(RuntimeError, 'Invalid persistent result frame'):
            host.call({'op': 'poll'})
        with self.assertRaisesRegex(RuntimeError, 'Invalid persistent result frame'):
            host.call({'op': 'poll'})
        self.assertEqual(host.failure_context['error_type'], 'RuntimeError')


if __name__ == '__main__':
    unittest.main()
