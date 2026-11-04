import math
import unittest

from movement_collision import Hit, ray_aabb, sweep_aabb_horizontal


class RayAabbTests(unittest.TestCase):
    box = ((0, 0, 0), (2, 2, 2))

    def test_entry_distance_and_face_normal(self):
        self.assertEqual(ray_aabb((-1, 1, 1), (4, 0, 0), *self.box, 10),
                         Hit(1.0, (-1.0, 0.0, 0.0)))

    def test_ground_contact_surface(self):
        self.assertEqual(ray_aabb((1, 3, 1), (0, -1, 0), *self.box, 3),
                         Hit(1.0, (0.0, 1.0, 0.0)))

    def test_inside_is_reported_as_initial_overlap(self):
        self.assertEqual(ray_aabb((1, 1, 1), (0, 1, 0), *self.box, 0),
                         Hit(0.0, (0.0, 0.0, 0.0)))

    def test_boundary_inward_hits_at_zero_and_outward_does_not(self):
        self.assertEqual(ray_aabb((0, 1, 1), (1, 0, 0), *self.box, 1),
                         Hit(0.0, (-1.0, 0.0, 0.0)))
        self.assertIsNone(ray_aabb((0, 1, 1), (-1, 0, 0), *self.box, 1))

    def test_parallel_axes_and_miss(self):
        self.assertIsNone(ray_aabb((-1, 3, 1), (1, 0, 0), *self.box, 10))
        self.assertEqual(ray_aabb((1, 3, 1), (0, -1, 0), *self.box, 10).distance, 1)

    def test_distance_limit_is_in_world_units(self):
        self.assertIsNone(ray_aabb((-1, 1, 1), (20, 0, 0), *self.box, .99))
        self.assertEqual(ray_aabb((-1, 1, 1), (20, 0, 0), *self.box, 1).distance, 1)

    def test_invalid_arguments_are_rejected(self):
        bad_calls = [
            lambda: ray_aabb((0, 0), (1, 0, 0), *self.box, 1),
            lambda: ray_aabb((0, 0, 0), (0, 0, 0), *self.box, 1),
            lambda: ray_aabb((0, 0, 0), (1, 0, 0), (1, 0, 0), (1, 2, 2), 1),
            lambda: ray_aabb((0, 0, 0), (1, 0, 0), *self.box, math.inf),
            lambda: ray_aabb((0, 0, 0), (1, 0, 0), *self.box, -1),
            lambda: ray_aabb((0, math.nan, 0), (1, 0, 0), *self.box, 1),
        ]
        for call in bad_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()


class HorizontalSweepTests(unittest.TestCase):
    box = ((0, 0, 0), (2, 10, 2))

    def test_sweep_uses_actor_half_extents_and_returns_fraction(self):
        hit = sweep_aabb_horizontal((-3, 100, 1), (0.5, 1, 0.5), (4, -99, 0), *self.box)
        self.assertEqual(hit, Hit(0.625, (-1.0, 0.0, 0.0)))

    def test_ground_height_is_ignored(self):
        a = sweep_aabb_horizontal((-3, 2, 1), (0.5, 1, 0.5), (4, 0, 0), *self.box)
        b = sweep_aabb_horizontal((-3, 200, 1), (0.5, 40, 0.5), (4, 0, 0), *self.box)
        self.assertEqual(a, b)

    def test_initial_overlap_and_touching_moving_away(self):
        self.assertEqual(sweep_aabb_horizontal((1, 0, 1), (0.5, 1, 0.5), (3, 0, 0), *self.box),
                         Hit(0.0, (0.0, 0.0, 0.0)))
        self.assertIsNone(sweep_aabb_horizontal((-0.5, 0, 1), (0.5, 1, 0.5), (-1, 0, 0), *self.box))

    def test_no_horizontal_displacement_only_hits_if_overlapping(self):
        self.assertIsNone(sweep_aabb_horizontal((-3, 0, 1), (0.5, 1, 0.5), (0, 100, 0), *self.box))

    def test_invalid_half_extents_are_rejected(self):
        with self.assertRaises(ValueError):
            sweep_aabb_horizontal((0, 0, 0), (-1, 0, 1), (1, 0, 0), *self.box)


if __name__ == '__main__':
    unittest.main()
