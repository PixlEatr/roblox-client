"""Requested shallow-overlap escape keeps neighboring collisions active."""
import math
import unittest

from movement_collision import shallow_overlap_allows_motion
from physics_body_collision import move_horizontal_body, step_vertical_body, _first_hit
from physics_ground import AABB, OBB, VoxelGrid, GroundGeometry

IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1.)


class OverlapEscapeTests(unittest.TestCase):
    def test_shallow_wall_allows_outward_and_tangent_but_not_inward_motion(self):
        for delta, expected in [((-.5, 0, 0), True), ((0, -1, 0), True),
                                ((.1, 0, 0), False), ((0, 0, 0), False)]:
            with self.subTest(delta=delta):
                self.assertEqual(shallow_overlap_allows_motion(
                    (.05, 3, 0), (1, 1, .5), delta, (2, 3, 0), (1, 10, 10),
                    IDENTITY), expected)

    def test_deep_and_centered_overlaps_remain_blocked(self):
        self.assertFalse(shallow_overlap_allows_motion(
            (.5, 3, 0), (1, 1, .5), (-1, 0, 0), (2, 3, 0), (1, 10, 10), IDENTITY))
        self.assertFalse(shallow_overlap_allows_motion(
            (0, 0, 0), (.02, .02, .02), (1, 0, 0), (0, 0, 0), (.02, .02, .02), IDENTITY))

    def test_rotated_wall_uses_its_contact_plane(self):
        c = math.sqrt(.5)
        rotation = (c, 0, c, 0, 1, 0, -c, 0, c)
        radius = .1+1.5*c
        point = (-(radius-.05)*c, 3, (radius-.05)*c)
        self.assertTrue(shallow_overlap_allows_motion(
            point, (1, 1, .5), (-c, 0, c), (0, 3, 0), (.1, 10, 10), rotation))
        self.assertFalse(shallow_overlap_allows_motion(
            point, (1, 1, .5), (c, 0, -c), (0, 3, 0), (.1, 10, 10), rotation))

    def test_escape_does_not_skip_another_wall(self):
        geometry = GroundGeometry((AABB((1, -10, -10), (3, 10, 10)),
                                   AABB((-3, -10, -10), (-2, 10, 10))), ())
        result = move_horizontal_body((.05, 3, 0), (-5, 0, 0), (1, 1, .5),
                                      geometry, grounded=False)
        self.assertAlmostEqual(result.position[0], -1., places=5)
        self.assertFalse(result.initial_overlap)
        self.assertEqual(result.normals, ((1., 0., 0.),))

    def test_gravity_continues_along_shallow_wall_overlap(self):
        wall = AABB((1, -10, -10), (3, 10, 10))
        result, contact = step_vertical_body((.05, 5, 0), 0., .1,
            GroundGeometry((wall,), ()), False, half_size=(1, 1, .5))
        self.assertLess(result.position[1], 5.)
        self.assertAlmostEqual(result.vertical_velocity, -19.62)
        self.assertIsNone(contact)

    def test_floor_penetration_does_not_allow_falling_through(self):
        result, contact = step_vertical_body((0, .95, 0), -1., .1,
            GroundGeometry((AABB((-10, -2, -10), (10, 0, 10)),), ()), False,
            half_size=(1, 1, .5))
        self.assertEqual(result.position[1], .95)
        self.assertTrue(contact.initial_overlap)

    def test_voxel_escape_checks_each_cell_including_new_obstacles(self):
        grid = VoxelGrid((1, -10, -10), (2, 20, 20), (1, 1, 1), (True,), 'wall')
        geometry = GroundGeometry((grid, AABB((-3, -10, -10), (-2, 10, 10))), ())
        result = move_horizontal_body((.05, 3, 0), (-5, 0, 0), (1, 1, .5),
                                      geometry, grounded=False)
        self.assertAlmostEqual(result.position[0], -1., places=5)

    def test_clearance_queries_still_report_existing_overlap(self):
        geometry = GroundGeometry((AABB((1, -10, -10), (3, 10, 10)),), ())
        hit = _first_hit((.05, 3, 0), (1, 1, .5), (0, 1, 0), geometry)
        self.assertEqual(hit.normal, (0., 0., 0.))


if __name__ == '__main__':
    unittest.main()
