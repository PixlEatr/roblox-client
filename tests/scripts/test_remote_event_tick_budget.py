"""Contract tests for sharing one remote-event attempt budget across tick flushes.

The game-session tick currently has two flush sites: a pre-scheduler flush and
a post-scheduler flush.  These tests exercise the bounded queue primitive and
the split-budget contract without mirroring the nested tick function in an AST.
"""
import collections
import unittest

from remote_server_events import RemoteServerEvents
from world_replication import World


def fixture():
    schema = {
        'classes': [{'id': 0, 'name': 'RemoteEvent', 'properties': [], 'events': [0]}],
        'events': [{'id': 0, 'class': 0, 'name': 'OnRemoteServerEvent', 'arguments': [{'type': 29}]}],
    }
    world = World(schema)
    world.instances[(1, 77)] = {'class': 0, 'parent': None, 'properties': {}}
    return world


def event(index):
    return {'node': '1:77', 'arguments': [index]}


class RemoteEventTickBudgetTests(unittest.TestCase):
    def test_pre_and_post_flush_share_sixteen_attempts_and_keep_fifo(self):
        world = fixture()
        queue = collections.deque([
            {'node': '1:999', 'arguments': ['rejected first']},
            *(event(i) for i in range(1, 20)),
        ])
        sender = RemoteServerEvents()
        sent = []

        pre_attempts = sender.flush(
            world, queue, lambda channel, payload: sent.append(payload), max_events=10)
        # A callback/scheduler-produced event arrives between the two flushes.
        queue.append(event(99))
        post_attempts = sender.flush(
            world, queue, lambda channel, payload: sent.append(payload), max_events=16-pre_attempts)

        self.assertEqual(pre_attempts, 10, 'rejected dequeued records consume the budget')
        self.assertEqual(post_attempts, 6)
        self.assertEqual(pre_attempts + post_attempts, 16)
        self.assertEqual(len(sent), 15)
        self.assertEqual(sum(sender.rejected.values()), 1)
        self.assertEqual(len(queue), 5)

        decoded_arguments = []
        for payload in sent:
            received = fixture()
            received.packet(payload)
            decoded_arguments.extend(received.events[-1]['arguments'][0])
        self.assertEqual(decoded_arguments, list(range(1, 16)))
        self.assertEqual([queued['arguments'][0] for queued in queue], [16, 17, 18, 19, 99])

    def test_zero_budget_is_noop_and_default_budget_remains_sixteen(self):
        world = fixture()
        sender = RemoteServerEvents()
        queue = collections.deque(event(i) for i in range(20))
        sent = []

        self.assertEqual(sender.flush(world, queue, sent.append, max_events=0), 0)
        self.assertEqual(len(queue), 20)
        self.assertEqual(sender.flush(world, queue, lambda _channel, payload: sent.append(payload)), 16)
        self.assertEqual(len(sent), 16)
        self.assertEqual(len(queue), 4)

    def test_invalid_max_events_values_are_rejected_before_queue_mutation(self):
        world = fixture()
        sender = RemoteServerEvents()
        for limit in (-1, 17, 1.5, True, None):
            with self.subTest(limit=limit):
                queue = collections.deque([event(1)])
                with self.assertRaises(ValueError):
                    sender.flush(world, queue, lambda *_: None, max_events=limit)
                self.assertEqual(list(queue), [event(1)])

    def test_transport_failure_retains_failed_record_and_later_fifo_items(self):
        world = fixture()
        sender = RemoteServerEvents()
        queue = collections.deque(event(i) for i in range(4))
        sent_indices = []

        def flaky_transport(_channel, payload):
            decoded = fixture()
            decoded.packet(payload)
            value = decoded.events[-1]['arguments'][0][0]
            if value == 1:
                raise OSError('transient transport failure')
            sent_indices.append(value)

        with self.assertRaisesRegex(OSError, 'transient transport failure'):
            sender.flush(world, queue, flaky_transport, max_events=4)

        self.assertEqual(sent_indices, [0])
        self.assertEqual([item['arguments'][0] for item in queue], [1, 2, 3])
        self.assertEqual(sender.sent, 1)

        retried = []
        attempts = sender.flush(
            world, queue,
            lambda _channel, payload: (
                fixture().packet(payload),
                retried.append(payload),
            ),
            max_events=3,
        )
        self.assertEqual(attempts, 3)
        self.assertEqual(len(retried), 3)
        self.assertFalse(queue)
        self.assertEqual(sender.sent, 4)

    def test_rejected_record_counts_as_an_attempt_and_is_not_retried(self):
        world = fixture()
        sender = RemoteServerEvents()
        queue = collections.deque([
            {'node': '1:999', 'arguments': [0]},
            event(1),
        ])
        sent = []

        attempts = sender.flush(world, queue, lambda channel, payload: sent.append(payload), max_events=1)

        self.assertEqual(attempts, 1)
        self.assertEqual(len(sender.rejected), 1)
        self.assertEqual([item['arguments'][0] for item in queue], [1])
        self.assertEqual(sent, [])


if __name__ == '__main__':
    unittest.main()
