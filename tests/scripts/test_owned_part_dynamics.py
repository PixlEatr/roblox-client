"""Offline contract tests for the bounded isolated-block dynamics primitive."""
import math
import unittest

from owned_part_dynamics import _single_source_servo_acceleration, step_owned_part
from physics_forces import velocity_servo_values
from physics_ground import AABB, OBB, GroundGeometry
from physics_velocity_servo import servo_acceleration


IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1.)
ZERO = (0., 0., 0.)


def geometry(*colliders):
    return GroundGeometry(tuple(colliders), ())


class OwnedPartDynamicsTests(unittest.TestCase):
    def step(self, position, velocity, dt, colliders=(), *, gravity=0., size=(1., 1., 1.),
             rotation=IDENTITY, angular=ZERO):
        return step_owned_part(position, rotation, velocity, angular, size, dt, gravity,
                               geometry(*colliders))

    def test_free_fall_matches_analytic_state(self):
        result = self.step((0., 10., 0.), (0., 4., 0.), .2, gravity=10.)
        self.assertIsNone(result.unsupported_reason)
        self.assertAlmostEqual(result.position[1], 10.6, places=10)
        self.assertAlmostEqual(result.linear_velocity[1], 2., places=10)
        self.assertAlmostEqual(result.simulated_time, .2, places=12)

    def test_capped_servo_opposes_only_its_available_share_of_gravity(self):
        source = velocity_servo_values((0., 0., 0.), (0., 5., 0.), 1e9)
        result = step_owned_part((0., 10., 0.), IDENTITY, ZERO, ZERO,
            (1., 1., 1.), .1, 10., geometry(), velocity_sources=(source,), mass=2.)
        self.assertAlmostEqual(result.linear_velocity[1], -.75, places=5)
        self.assertAlmostEqual(result.position[1], 9.9625, places=5)

    def test_single_source_closed_form_matches_bisection_reference(self):
        cases = (
            # Uncapped moderate gain.
            ((0., 0., 0.), (12., -3., 4.), 2., .01, (0., -9.8, 0.), (1000., 1000., 1000.), 10.),
            # Capped force in opposite directions on separate axes.
            ((20., -5., 3.), (-100., 100., 0.), 1.5, .02, (0., -2., 0.), (3., 4., 0.), 25.),
            # Very high gain approaches target with finite implicit force.
            ((-2., 1., 0.), (10., -8., 5.), 2., 1./240., (0., -10., 0.),
             (1e6, 1e6, 1e6), 1e30),
            # High gain with a low cap exercises the capped limiting branch.
            ((0., 2., 0.), (100., -100., 20.), .7, 1./240., (0., -196.2, 0.),
             (2., 3., 1.), 1e30),
        )
        for velocity, target, mass, dt, external, limits, gain in cases:
            with self.subTest(gain=gain, limits=limits):
                source = velocity_servo_values(target, limits, gain)
                expected = servo_acceleration(velocity, (source,), mass, dt, external)
                actual = _single_source_servo_acceleration(velocity, source, mass, dt, external)
                for got, reference in zip(actual, expected):
                    self.assertAlmostEqual(got, reference, delta=2e-8*max(1., abs(reference)))

    def test_high_gain_terminal_velocity_is_stable_across_timestep_partitioning(self):
        source = velocity_servo_values((10., 0., 0.), (1e6, 1e6, 1e6), 1e30)
        one = step_owned_part(ZERO, IDENTITY, ZERO, ZERO, (1., 1., 1.), .25, 0.,
                              geometry(), velocity_sources=(source,), mass=2.)
        many_pos, many_velocity = ZERO, ZERO
        for _ in range(10):
            part = step_owned_part(many_pos, IDENTITY, many_velocity, ZERO,
                (1., 1., 1.), .025, 0., geometry(), velocity_sources=(source,), mass=2.)
            many_pos, many_velocity = part.position, part.linear_velocity
        self.assertLessEqual(one.linear_velocity[0], 10.+1e-9)
        self.assertAlmostEqual(one.linear_velocity[0], 10., places=6)
        self.assertAlmostEqual(one.position[0], many_pos[0], places=7)
        self.assertAlmostEqual(one.linear_velocity[0], many_velocity[0], places=8)

    def test_zero_gravity_zero_target_does_not_move_stationary_body(self):
        source = velocity_servo_values(ZERO, (100., 100., 100.), 1000.)
        result = step_owned_part((2., 3., 4.), IDENTITY, ZERO, ZERO,
            (1., 1., 1.), .2, 0., geometry(), velocity_sources=(source,), mass=1.)
        self.assertEqual(result.position, (2., 3., 4.))
        self.assertEqual(result.linear_velocity, ZERO)

    def test_servo_cannot_drive_body_through_floor(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.), 'floor')
        source = velocity_servo_values((0., -10., 0.), (100., 100., 100.), 1000.)
        result = step_owned_part((0., .5, 0.), IDENTITY, ZERO, ZERO,
            (1., 1., 1.), .1, 10., geometry(floor), velocity_sources=(source,), mass=1.)
        self.assertAlmostEqual(result.position[1], .5, places=8)
        self.assertAlmostEqual(result.linear_velocity[1], 0., places=8)
        self.assertTrue(any(normal[1] > .99 for normal in result.contacts))

    def test_servo_requires_known_mass_and_validated_source_fields(self):
        source = velocity_servo_values((1., 0., 0.), (10., 10., 10.), 20.)
        for mass in (None, 0., -1., math.inf, math.nan, True):
            with self.subTest(mass=mass), self.assertRaisesRegex(ValueError, 'mass'):
                step_owned_part(ZERO, IDENTITY, ZERO, ZERO, (1., 1., 1.), .01, 0.,
                                geometry(), velocity_sources=(source,), mass=mass)
        for invalid in ({'target_velocity': (0., 0., 0.), 'max_force': (-1., 0., 0.), 'gain': 1.},
                        {'target_velocity': (math.nan, 0., 0.), 'max_force': (1., 1., 1.), 'gain': 1.},
                        {'target_velocity': ZERO, 'max_force': (1., 1., 1.), 'gain': math.inf}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'invalid_velocity_source'):
                step_owned_part(ZERO, IDENTITY, ZERO, ZERO, (1., 1., 1.), .01, 0.,
                                geometry(), velocity_sources=(invalid,), mass=1.)

    def test_fast_body_does_not_tunnel_through_thin_floor(self):
        floor = AABB((-10., -.01, -10.), (10., 0., 10.), 'thin-floor')
        result = self.step((0., 2.5, 0.), (0., -30., 0.), .1, (floor,), gravity=20.)
        self.assertIsNone(result.unsupported_reason)
        self.assertAlmostEqual(result.position[1], .5, places=5)
        self.assertAlmostEqual(result.linear_velocity[1], 0., places=9)
        self.assertTrue(any(normal[1] > .99 for normal in result.contacts))

    def test_wall_contact_preserves_tangent_and_does_not_gain_energy(self):
        wall = AABB((2., -5., -5.), (2.1, 5., 5.), 'wall')
        initial_speed = math.hypot(10., 4.)
        result = self.step((0., 0., 0.), (10., 0., 4.), .2, (wall,))
        self.assertIsNone(result.unsupported_reason)
        self.assertAlmostEqual(result.position[0], 1.5, places=5)
        self.assertAlmostEqual(result.position[2], .8, places=5)
        self.assertAlmostEqual(result.linear_velocity[0], 0., places=9)
        self.assertAlmostEqual(result.linear_velocity[2], 4., places=9)
        self.assertLessEqual(math.hypot(*result.linear_velocity), initial_speed + 1e-9)

    def test_corner_contacts_project_both_inward_components(self):
        x_wall = AABB((2., -2., -2.), (2.1, 2., 4.), 'x-wall')
        z_wall = AABB((-2., -2., 2.), (4., 2., 2.1), 'z-wall')
        result = self.step((0., 0., 0.), (20., 0., 20.), .1, (x_wall, z_wall))
        self.assertIsNone(result.unsupported_reason)
        self.assertAlmostEqual(result.position[0], 1.5, places=5)
        self.assertAlmostEqual(result.position[2], 1.5, places=5)
        self.assertAlmostEqual(result.linear_velocity[0], 0., places=8)
        self.assertAlmostEqual(result.linear_velocity[2], 0., places=8)
        self.assertGreaterEqual(len(result.contacts), 2)
        self.assertLessEqual(math.hypot(*result.linear_velocity), math.sqrt(800.) + 1e-9)

    def test_centerline_overlap_with_no_separating_side_is_reported(self):
        wall = AABB((-.25, -2., -2.), (.25, 2., 2.), 'wall')
        result = self.step((0., 0., 0.), (3., 0., 0.), .1, (wall,))
        self.assertEqual(result.unsupported_reason, 'ambiguous_separation_direction')
        self.assertEqual(result.position, ZERO)
        self.assertEqual(result.linear_velocity, (3., 0., 0.))
        self.assertEqual(result.simulated_time, 0.)

    def test_known_box_overlap_is_corrected_by_minimum_translation(self):
        target = AABB((-2., -222.5, 449.), (2., -221.5, 451.), 'listener')
        position = (0., -221.52352, 451.1351)
        velocity = (1., -3., 4.)
        result = self.step(position, velocity, 0., (target,), gravity=196.2,
                           size=(4., 1.2, 2.))
        self.assertIsNone(result.unsupported_reason)
        self.assertAlmostEqual(result.position[0], position[0])
        self.assertAlmostEqual(result.position[1], -220.9, places=5)
        self.assertAlmostEqual(result.position[2], position[2])
        self.assertEqual(len(result.corrections), 1)
        correction = result.corrections[0]
        self.assertEqual(correction.normal, (0., 1., 0.))
        self.assertAlmostEqual(correction.penetration_depth, .62352, places=5)
        self.assertAlmostEqual(correction.displacement[1], correction.penetration_depth)
        self.assertEqual(result.linear_velocity, (1., 0., 4.))
        self.assertAlmostEqual(math.hypot(*result.linear_velocity), math.sqrt(17.))

    def test_sat_axis_tie_break_is_deterministic(self):
        # X is the unique minimum translation axis; sign follows center offset.
        box = AABB((-.5, -.5, -.5), (.5, .5, .5), 'unit-box')
        result = self.step((.2, .1, 0.), (0., 0., 0.), 0., (box,))
        self.assertIsNone(result.unsupported_reason)
        self.assertEqual(result.corrections[0].normal, (1., 0., 0.))
        self.assertAlmostEqual(result.position[0], 1.0, places=9)

    def test_overlap_correction_guard_leaves_input_untouched(self):
        target = AABB((-2., -222.5, 449.), (2., -221.5, 451.), 'listener')
        position = (0., -221.52352, 451.1351)
        result = step_owned_part(position, IDENTITY, (0., -3., 0.), ZERO,
            (4., 1.2, 2.), 0., 196.2, geometry(target), max_overlap_correction=.5)
        self.assertEqual(result.unsupported_reason, 'overlap_correction_limit')
        self.assertEqual(result.position, position)
        self.assertEqual(result.linear_velocity, (0., -3., 0.))
        self.assertEqual(result.corrections, ())

    def test_four_correction_budget_stops_trapped_overlap_without_partial_move(self):
        positive = AABB((.4, -3., -3.), (2., 3., 3.), 'positive-wall')
        negative = AABB((-2., -3., -3.), (-.4, 3., 3.), 'negative-wall')
        result = step_owned_part(ZERO, IDENTITY, ZERO, ZERO, (1., 1., 1.), 0., 0.,
            geometry(positive, negative), max_overlap_correction=10.)
        self.assertEqual(result.unsupported_reason, 'overlap_correction_budget_exhausted')
        self.assertEqual(result.position, ZERO)
        self.assertEqual(result.corrections, ())

    def test_overlapping_rotated_obstacle_uses_bounded_sat_mtv(self):
        angle = math.pi/4
        rotation = (math.cos(angle), 0., -math.sin(angle),
                    0., 1., 0., math.sin(angle), 0., math.cos(angle))
        obstacle = OBB((0., .2, 0.), (1., .5, 1.), rotation, 'rotated-box')
        result = self.step((0., 0., 0.), (0., -1., 0.), 0., (obstacle,), size=(1., 1., 1.))
        self.assertIsNone(result.unsupported_reason)
        self.assertEqual(result.corrections[0].normal, (0., -1., 0.))
        self.assertAlmostEqual(result.position[1], -0.8, places=8)
        self.assertEqual(result.linear_velocity, (0., -1., 0.))

    def test_non_axis_aligned_rotation_is_rejected(self):
        angle = math.pi/4
        rotation = (math.cos(angle), 0., math.sin(angle),
                    0., 1., 0., -math.sin(angle), 0., math.cos(angle))
        with self.assertRaisesRegex(ValueError, 'rotation_not_axis_aligned'):
            self.step(ZERO, ZERO, .01, rotation=rotation)

    def test_nonzero_angular_velocity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'nonzero_angular_velocity_unsupported'):
            self.step(ZERO, ZERO, .01, angular=(0., 0.01, 0.))

    def test_invalid_time_gravity_velocity_and_geometry_are_rejected(self):
        for dt in (-.01, .250001, math.inf, math.nan, True):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                self.step(ZERO, ZERO, dt)
        for gravity in (-1., math.inf, math.nan, True):
            with self.subTest(gravity=gravity), self.assertRaises(ValueError):
                self.step(ZERO, ZERO, .01, gravity=gravity)
        with self.assertRaises(ValueError):
            self.step(ZERO, (math.nan, 0., 0.), .01)
        with self.assertRaises(ValueError):
            step_owned_part(ZERO, IDENTITY, ZERO, ZERO, (1., 1., 1.), .01, 0., ())


if __name__ == '__main__':
    unittest.main()
