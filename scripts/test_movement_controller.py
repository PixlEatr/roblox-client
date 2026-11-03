import math
import unittest

from movement_controller import Controller


class MovementControllerTests(unittest.TestCase):
    def test_direction_is_normalized_and_steps_hold_height(self):
        controller = Controller((1, 12, 2), walk_speed=10)
        controller.set_direction((3, 999, 4))
        state = controller.step(.5 / 2)
        self.assertEqual(state['position'], (2.5, 12, 4))
        self.assertEqual(state['velocity'], (6, 0, 8))
        self.assertTrue(state['moving'])

    def test_equal_elapsed_time_is_independent_of_step_partition(self):
        one = Controller(walk_speed=8)
        many = Controller(walk_speed=8)
        one.set_direction((1, 0, 1))
        many.set_direction((1, 0, 1))
        one.step(.2)
        for _ in range(10):
            many.step(.02)
        for left, right in zip(one.position, many.position):
            self.assertTrue(math.isclose(left, right, rel_tol=0, abs_tol=1e-12))

    def test_stop_and_zero_direction_stop_immediately(self):
        controller = Controller()
        controller.set_direction((1, 0, 0))
        controller.step(.1)
        controller.stop()
        state = controller.step(.1)
        self.assertFalse(state['moving'])
        self.assertEqual(state['velocity'], (0, 0, 0))
        self.assertEqual(state['position'], (1.6, 0, 0))
        controller.set_direction((0, 5, 0))
        self.assertFalse(controller.step(.1)['moving'])

    def test_validation_rejects_nonfinite_and_unbounded_inputs(self):
        for position in ((0, math.inf, 0), (1, 2), (True, 0, 0)):
            with self.subTest(position=position), self.assertRaises(ValueError):
                Controller(position)
        for speed in (-1, math.nan, 129, True):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                Controller(walk_speed=speed)
        controller = Controller()
        for direction in ((math.inf, 0, 0), (1, 0), (0, False, 0)):
            with self.subTest(direction=direction), self.assertRaises(ValueError):
                controller.set_direction(direction)
        for dt in (-.1, math.inf, math.nan, .251, True):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                controller.step(dt)

    def test_zero_time_updates_velocity_without_moving(self):
        controller = Controller(walk_speed=3)
        controller.set_direction((0, 0, -1))
        state = controller.step(0)
        self.assertEqual(state['position'], (0, 0, 0))
        self.assertEqual(state['velocity'], (0, 0, -3))


if __name__ == '__main__':
    unittest.main()
