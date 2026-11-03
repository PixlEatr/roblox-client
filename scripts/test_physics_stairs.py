"""Offline stair and slope limits for the local body proxy."""
import math
import unittest

from physics_body_collision import move_horizontal_body
from physics_ground import AABB, GroundGeometry
from physics_stairs import walkable_normal, slope_slide_acceleration


def geometry(*boxes):
    return GroundGeometry(tuple(boxes), ())


class PhysicsStairsTests(unittest.TestCase):
    def test_slope_limit_uses_surface_normal_angle(self):
        thirty = (-.5, math.sqrt(3)/2, 0)
        self.assertTrue(walkable_normal(thirty, 30))
        self.assertFalse(walkable_normal(thirty, 29.9))
        self.assertFalse(walkable_normal((0, -1, 0), 89))

    def test_steep_slope_slide_acceleration_is_gravity_tangent(self):
        accel = slope_slide_acceleration((-.5, math.sqrt(3)/2, 0), 100)
        self.assertAlmostEqual(accel[0], -100*.5*math.sqrt(3)/2)
        self.assertAlmostEqual(accel[1], -25)
        self.assertAlmostEqual(accel[2], 0)
        self.assertEqual(slope_slide_acceleration((0, 1, 0)), (0., 0., 0.))

    def test_stair_step_is_bounded_and_requires_support(self):
        floor = AABB((-10, -1, -10), (10, 0, 10))
        step = AABB((1, 0, -2), (2, .5, 2))
        result = move_horizontal_body((0, 1, 0), (2, 0, 0), (.5, 1, .5),
            geometry(floor, step), grounded=True, foot_offset=1, max_step_height=.6)
        self.assertAlmostEqual(result.position[0], 2)
        self.assertAlmostEqual(result.position[1], 1.5)
        too_low = move_horizontal_body((0, 1, 0), (2, 0, 0), (.5, 1, .5),
            geometry(floor, step), grounded=True, foot_offset=1, max_step_height=.49)
        self.assertLess(too_low.position[0], 1)
        unsupported = move_horizontal_body((0, 1, 0), (2, 0, 0), (.5, 1, .5),
            geometry(step), grounded=True, foot_offset=1, max_step_height=.6)
        self.assertLess(unsupported.position[0], 1)

    def test_step_rejects_overhead_clearance_and_tall_second_wall(self):
        floor = AABB((-10, -1, -10), (10, 0, 10))
        step = AABB((1, 0, -2), (2, .5, 2))
        low_ceiling = AABB((-.5, 2.1, -2), (4, 2.2, 2))
        result = move_horizontal_body((0, 1, 0), (2, 0, 0), (.5, 1, .5),
            geometry(floor, step, low_ceiling), grounded=True, foot_offset=1,
            max_step_height=.6)
        self.assertLess(result.position[0], 1)
        tall_wall = AABB((2.1, 0, -2), (2.2, 4, 2))
        result = move_horizontal_body((0, 1, 0), (3, 0, 0), (.5, 1, .5),
            geometry(floor, step, tall_wall), grounded=True, foot_offset=1,
            max_step_height=.6)
        self.assertLess(result.position[0], 2)

    def test_zero_step_height_preserves_wall_blocking(self):
        floor = AABB((-10, -1, -10), (10, 0, 10))
        step = AABB((1, 0, -2), (2, .5, 2))
        result = move_horizontal_body((0, 1, 0), (2, 0, 0), (.5, 1, .5),
            geometry(floor, step), grounded=True, foot_offset=1, max_step_height=0)
        self.assertLess(result.position[0], 1)

    def test_ground_follow_does_not_walk_up_a_slope_over_limit(self):
        from physics_ground import extract_ground
        from test_physics_slopes import ramp_world
        angle = math.pi/6
        start = (0, 3+1/math.cos(angle), 0)
        result = move_horizontal_body(start, (.5, 0, 0), (1, 1, .5),
            extract_ground(ramp_world()), grounded=True, max_slope_angle=20)
        self.assertAlmostEqual(result.position[1], start[1])


if __name__ == '__main__':
    unittest.main()
