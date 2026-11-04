"""Footprint samples only exact, nearby walkable ground surfaces."""
import unittest

from physics_ground import (AABB, GroundGeometry, TriangleMesh, footprint_ground_raycast,
                            ground_raycast, step_vertical)


def square_ring(inner=.25):
    points = [(-1., 0., -1.), (1., 0., -1.), (1., 0., 1.), (-1., 0., 1.),
              (-inner, 0., -inner), (inner, 0., -inner),
              (inner, 0., inner), (-inner, 0., inner)]
    triangles = [(0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                 (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    oriented = []
    for a, b, c in triangles:
        pa, pb, pc = points[a], points[b], points[c]
        normal_y = ((pb[2]-pa[2])*(pc[0]-pa[0])
                    -(pb[0]-pa[0])*(pc[2]-pa[2]))
        oriented.append((a, c, b) if normal_y < 0 else (a, b, c))
    return TriangleMesh(tuple(points), tuple(oriented), 'ring')


class FootprintGroundRaycastTests(unittest.TestCase):
    def query(self, geometry, *, position=(0., 3., 0.), half=(1., 1., 1.), **kwargs):
        kwargs.setdefault('max_distance', .5)
        return footprint_ground_raycast(position, geometry, half_size=half,
                                        foot_offset=3., **kwargs)

    def test_center_probe_is_preferred_over_higher_edge_surfaces(self):
        lower = AABB((-2., -1., -2.), (2., 0., 2.), 'center_floor')
        higher = AABB((.7, .7, -.2), (2., .8, .2), 'edge_ledge')
        result = self.query(GroundGeometry((lower, higher), ()),
                            position=(0., 4., 0.), max_distance=1.1)
        self.assertIsNotNone(result)
        self.assertEqual(result.collider.source, 'center_floor')
        self.assertEqual(result.y, 0.)

    def test_center_miss_uses_nearest_footprint_support(self):
        ledge = AABB((.7, -.2, -.3), (2., 0., .3), 'ledge')
        geometry = GroundGeometry((ledge,), ())
        self.assertIsNone(ground_raycast((0., 3., 0.), geometry,
                                         foot_offset=3., max_distance=.5))
        result = self.query(geometry)
        self.assertIsNotNone(result)
        self.assertEqual(result.collider.source, 'ledge')

    def test_falling_footprint_hits_high_edge_before_lower_center(self):
        floor = AABB((-2., -1., -2.), (2., 0., 2.), 'floor')
        ledge = AABB((.7, .5, -.2), (2., .8, .2), 'ledge')
        geometry = GroundGeometry((floor, ledge), ())
        result = step_vertical((0., 5., 0.), -20., .1, geometry, False,
            support_half_size=(1., 1., 1.), gravity=0.)
        self.assertTrue(result.grounded)
        self.assertAlmostEqual(result.position[1], 3.8)
        self.assertIs(result.hit, ledge)

    def test_edge_support_holds_at_foot_height_then_falls_after_leaving(self):
        ledge = AABB((.7, -.2, -.3), (2., 0., .3), 'ledge')
        geometry = GroundGeometry((ledge,), ())
        result = step_vertical((0., 3., 0.), 0., .05, geometry, True,
                              support_half_size=(1., 1., 1.))
        self.assertTrue(result.grounded)
        self.assertEqual(result.position[1], 3.)
        departed = step_vertical((-1., 3., 0.), 0., .05, geometry, True,
                                 support_half_size=(1., 1., 1.))
        self.assertFalse(departed.grounded)
        self.assertLess(departed.position[1], 3.)

    def test_upward_jump_is_not_recaptured_by_edge_support(self):
        ledge = AABB((.7, -.2, -.3), (2., 0., .3), 'ledge')
        result = step_vertical((0., 3., 0.), 50., .05, GroundGeometry((ledge,), ()),
                              False, support_half_size=(1., 1., 1.))
        self.assertFalse(result.grounded)
        self.assertGreater(result.position[1], 3.)

    def test_nearest_equal_radius_candidate_prefers_highest_support(self):
        left = AABB((-2., -.1, -.2), (-.7, 0., .2), 'left_low')
        right = AABB((.7, .7, -.2), (2., .8, .2), 'right_high')
        result = self.query(GroundGeometry((left, right), ()),
                            position=(0., 4., 0.), max_distance=1.1)
        self.assertIsNotNone(result)
        self.assertEqual(result.collider.source, 'right_high')
        self.assertAlmostEqual(result.y, .8)

    def test_max_slope_filters_steep_center_and_all_other_probes(self):
        points = ((-2., -3.464101615, -2.), (2., 3.464101615, -2.),
                  (2., 3.464101615, 2.), (-2., -3.464101615, 2.))
        mesh = TriangleMesh(points, ((0, 2, 1), (0, 3, 2)), 'steep_ramp')
        geometry = GroundGeometry((mesh,), ())
        self.assertIsNone(self.query(geometry, max_slope_angle=45.))
        accepted = self.query(geometry, max_slope_angle=65.)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.collider.source, 'steep_ramp')

    def test_vertical_range_does_not_snap_across_drop_or_to_overhead_surface(self):
        far_below = AABB((-2., -2., -2.), (2., -1., 2.), 'drop')
        overhead = AABB((-2., 4., -2.), (2., 5., 2.), 'overhead')
        self.assertIsNone(self.query(GroundGeometry((far_below, overhead), ())))

    def test_triangle_mesh_hole_is_not_replaced_by_mesh_bounds(self):
        ring = square_ring(inner=.25)
        geometry = GroundGeometry((ring,), ())
        self.assertIsNone(ground_raycast((0., 3., 0.), geometry,
                                         foot_offset=3., max_distance=.1))
        supported_ring = self.query(geometry, half=(.5, 1., .5))
        self.assertIsNotNone(supported_ring)
        self.assertIs(supported_ring.collider, ring)

        large_hole = GroundGeometry((square_ring(inner=.5),), ())
        self.assertIsNone(self.query(large_hole, half=(.5, 1., .5)))

    def test_probe_count_and_dimensions_are_bounded(self):
        geometry = GroundGeometry((), ())
        for count in (0, 4, 6, 10, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.query(geometry, probe_count=count)
        for half in ((0., 1., 1.), (1., 1., -1.)):
            with self.subTest(half=half), self.assertRaises(ValueError):
                self.query(geometry, half=half)


if __name__ == '__main__':
    unittest.main()
