import unittest

from movement_intents import MovementIntentQueue


class MovementIntentQueueTests(unittest.TestCase):
    def test_actions_are_validated_and_drained_fifo(self):
        queue = MovementIntentQueue()
        self.assertEqual(queue.submit({"kind": "move", "direction": [1, 0, -1],
                                       "relative_to_camera": False}), 1)
        self.assertEqual(queue.submit({"kind": "move_to", "position": [3, 4, 5]}), 2)
        self.assertEqual(queue.submit({"kind": "stop"}), 3)
        first = queue.drain(2)
        self.assertEqual([row["sequence"] for row in first], [1, 2])
        self.assertEqual(first[0]["direction"], (1.0, 0.0, -1.0))
        self.assertTrue(first[0]["supported"])
        self.assertEqual(first[1]["position"], (3.0, 4.0, 5.0))
        self.assertEqual(queue.drain()[0]["kind"], "stop")
        self.assertEqual(len(queue), 0)

    def test_camera_relative_action_is_explicitly_unsupported(self):
        queue = MovementIntentQueue()
        queue.submit({"kind": "move", "direction": [0, 0, 1], "relative_to_camera": True})
        result = queue.drain()[0]
        self.assertFalse(result["supported"])
        self.assertEqual(result["unsupported"], ("relative_to_camera",))
        self.assertEqual(result["direction"], (0.0, 0.0, 1.0))

    def test_queue_capacity_and_sequence_only_advance_when_accepted(self):
        queue = MovementIntentQueue(max_size=1)
        action = {"kind": "stop"}
        self.assertEqual(queue.submit(action), 1)
        with self.assertRaises(BufferError):
            queue.submit(action)
        self.assertEqual(queue.drain()[0]["sequence"], 1)
        self.assertEqual(queue.submit(action), 2)

    def test_invalid_shapes_types_and_non_finite_values_fail(self):
        queue = MovementIntentQueue()
        invalid = [
            {"kind": "fly"},
            {"kind": "stop", "direction": [0, 0, 0]},
            {"kind": "move", "direction": [0, 0], "relative_to_camera": False},
            {"kind": "move", "direction": [0, float("nan"), 0], "relative_to_camera": False},
            {"kind": "move", "direction": [0, float("inf"), 0], "relative_to_camera": False},
            {"kind": "move", "direction": [0, True, 0], "relative_to_camera": False},
            {"kind": "move", "direction": [1_000_001, 0, 0], "relative_to_camera": False},
            {"kind": "move", "direction": [0, 0, 0], "relative_to_camera": 1},
        ]
        for action in invalid:
            with self.subTest(action=action), self.assertRaises(ValueError):
                queue.submit(action)
        self.assertEqual(len(queue), 0)
        self.assertEqual(queue.submit({"kind": "stop"}), 1)

    def test_drain_limits_and_constructor_bounds(self):
        for value in (True, 0, 4097, 1.5):
            with self.subTest(max_size=value), self.assertRaises(ValueError):
                MovementIntentQueue(value)
        queue = MovementIntentQueue(max_size=2)
        queue.submit({"kind": "stop"})
        for limit in (-1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                queue.drain(limit)
        self.assertEqual(queue.drain(0), [])


if __name__ == "__main__":
    unittest.main()
