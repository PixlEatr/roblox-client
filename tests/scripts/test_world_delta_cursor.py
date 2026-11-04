"""Regression for avoiding full retained-history scans on small world deltas."""
import unittest

from world_snapshot import make_world_delta


class CountingDeque:
    """Deque-shaped action journal that counts record visits, not wall time."""

    def __init__(self, records):
        self.items = list(records)
        self.visits = 0

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)

    def __getitem__(self, index):
        if isinstance(index, int):
            self.visits += 1
        return self.items[index]

    def __iter__(self):
        for record in self.items:
            self.visits += 1
            yield record

    def __reversed__(self):
        for record in reversed(self.items):
            self.visits += 1
            yield record

    def reset_visits(self):
        self.visits = 0


def action_record(sequence, arguments=()):
    return {'sequence': sequence, 'kind': 'event', 'key': (1, 10),
            'event': 0, 'arguments': list(arguments)}


class WorldDeltaCursorTests(unittest.TestCase):
    def world(self, records, *, event_arguments=()):
        journal = CountingDeque(records)
        return type('FakeWorld', (), {
            'actions': journal,
            'revision': records[-1]['sequence'] if records else 0,
            'schema': {'events': [{'name': 'Changed', 'arguments': list(event_arguments)}]},
        })(), journal

    def test_no_pending_actions_does_not_visit_retained_history(self):
        records = [action_record(i) for i in range(1, 100_001)]
        world, journal = self.world(records)

        result = make_world_delta(world, world.revision,
                                  max_actions=2048, max_bytes=4 * 1024 * 1024)

        self.assertEqual(result['actions'], [])
        self.assertEqual(result['sequence'], world.revision)
        self.assertLessEqual(journal.visits, 8,
                             'an idle poll should inspect only the cursor boundary')

    def test_tiny_fresh_tail_visits_only_the_tail_and_cursor_boundary(self):
        records = [action_record(i) for i in range(1, 100_001)]
        world, journal = self.world(records)
        cursor = world.revision - 3

        result = make_world_delta(world, cursor,
                                  max_actions=2048, max_bytes=4 * 1024 * 1024)

        self.assertEqual([item['sequence'] for item in result['actions']],
                         [99_998, 99_999, 100_000])
        self.assertEqual(result['sequence'], world.revision)
        self.assertLessEqual(journal.visits, 8,
                             'a three-action tail should not scan the old prefix')

    def test_large_backlog_keeps_chronological_prefix_and_action_cap(self):
        records = [action_record(i) for i in range(1, 100_001)]
        world, journal = self.world(records)

        result = make_world_delta(world, 0, max_actions=64)

        self.assertEqual([item['sequence'] for item in result['actions']],
                         list(range(1, 65)))
        self.assertEqual(result['sequence'], 64)
        # The overrun guard and direction probe each touch one record before
        # the bounded chronological prefix is iterated.
        self.assertLessEqual(journal.visits, 67)

    def test_byte_budget_keeps_only_chronological_prefix_and_captured_sequence(self):
        records = [action_record(i) for i in range(1, 99_998)]
        records.extend((action_record(99_998, ('first',)),
                        action_record(99_999, ('x' * 1200,)),
                        action_record(100_000, ('last',))))
        world, _journal = self.world(records, event_arguments=({'type': 2},))

        result = make_world_delta(world, 99_997, max_bytes=1024)

        self.assertEqual([item['sequence'] for item in result['actions']], [99_998])
        self.assertEqual(result['sequence'], 99_998)

    def test_overrun_failure_is_preserved_for_expired_cursor(self):
        records = [action_record(i) for i in range(2, 100_002)]
        world, _journal = self.world(records)

        with self.assertRaisesRegex(ValueError, 'journal was overrun'):
            make_world_delta(world, 0, max_actions=64)


if __name__ == '__main__':
    unittest.main()
