"""Controller support falls back to root-footprint probes only when center misses."""
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, GroundGeometry, TriangleMesh, ground_raycast
from physics_platforms import PlatformPose
from test_physics_foot_offset import r15_snapshot


def actor_at(y, *, max_slope=None):
    data = r15_snapshot()
    humanoid, root = data['nodes'][2:4]
    root['properties']['Position'] = {'$vector3': [0., y, 0.]}
    root['properties']['CFrame'] = {'$cframe': {'position': [0., y, 0.]}}
    if max_slope is not None:
        humanoid['properties']['MaxSlopeAngle'] = max_slope
    return data


def sloped_edge():
    vertices = ((.7, 0., -1.), (2., 1.3, -1.),
                (2., 1.3, 1.), (.7, 0., 1.))
    triangles = ((0, 2, 1), (0, 3, 2))
    return TriangleMesh(vertices, triangles, 'sloped_edge')


class FootprintControllerTests(unittest.TestCase):
    def test_edge_support_grounds_controller_and_allows_jump(self):
        ledge = AABB((.7, -1., -2.), (5., 0., 2.), 'ledge')
        geometry = GroundGeometry((ledge,), ())
        controller = OwnCharacterPhysicsController(
            actor_at(3.2248413563), geometry=geometry, gravity=0.)
        self.assertIsNone(ground_raycast((0., 3.2248413563, 0.), geometry,
                                         foot_offset=controller.foot_offset,
                                         max_distance=.05))
        self.assertTrue(controller.grounded)
        self.assertEqual(controller._ground_source, 'ledge')

        controller.consume([{'sequence': 1, 'kind': 'Jump',
                             'instance_id': 'humanoid', 'jump_settings': {'JumpPower': 50.}}])
        self.assertEqual(controller.accepted, 1)
        self.assertIsNone(controller.last_reason)
        self.assertEqual(controller.vertical_velocity, 50.)

    def test_footprint_jump_inherits_received_moving_platform_velocity(self):
        ledge = AABB((.7, -1., -2.), (5., 0., 2.), 'moving_ledge')
        controller = OwnCharacterPhysicsController(
            actor_at(3.2248413563), geometry=GroundGeometry((ledge,), ()), gravity=0.)
        self.assertTrue(controller.grounded)
        controller.update_platform_poses(
            {'moving_ledge': PlatformPose((0., 0., 0.))},
            {'moving_ledge': PlatformPose((0., 1., 0.))}, .1)
        controller.update_geometry(GroundGeometry((
            AABB((.7, 0., -2.), (5., 1., 2.), 'moving_ledge'),), ()))
        controller.consume([{'sequence': 1, 'kind': 'Jump',
                             'instance_id': 'humanoid', 'jump_settings': {'JumpPower': 50.}}])
        self.assertAlmostEqual(controller.vertical_velocity, 60.)

    def test_falling_edge_support_uses_highest_footprint_contact_and_retains_source(self):
        low = AABB((.7, .5, -2.), (5., .6, 2.), 'low_support')
        high = AABB((-5., .9, -2.), (-.7, 1., 2.), 'high_support')
        geometry = GroundGeometry((low, high), ())
        controller = OwnCharacterPhysicsController(
            actor_at(6.2248413563), geometry=geometry, gravity=196.2)
        self.assertFalse(controller.grounded)
        state = controller.step(.15)['predicted_state']
        self.assertTrue(state['grounded'])
        self.assertAlmostEqual(state['position'][1], 4.2248413563, places=7)
        self.assertEqual(controller._ground_source, 'high_support')

    def test_footprint_fallback_respects_max_slope_without_changing_center_contact(self):
        mesh = sloped_edge()
        geometry = GroundGeometry((mesh,), ())
        steep = OwnCharacterPhysicsController(
            actor_at(3.7248413563, max_slope=30.), geometry=geometry, gravity=0.)
        self.assertIsNone(steep._ground_support((0., 3.7248413563, 0.), max_distance=.5))

        walkable = OwnCharacterPhysicsController(
            actor_at(3.7248413563, max_slope=60.), geometry=geometry, gravity=0.)
        support = walkable._ground_support((0., 3.7248413563, 0.), max_distance=.5)
        self.assertIsNotNone(support)
        self.assertEqual(support.collider.source, 'sloped_edge')

        center_slope = TriangleMesh(((-1., 0., -1.), (1., 1.3, -1.),
                                     (1., 1.3, 1.), (-1., 0., 1.)),
                                    ((0, 2, 1), (0, 3, 2)), 'center_slope')
        center_controller = OwnCharacterPhysicsController(
            actor_at(4.2248413563, max_slope=30.),
            geometry=GroundGeometry((center_slope,), ()), gravity=0.)
        # Center hits remain visible to the controller's established steep
        # contact/slide path; only off-center fallback is slope-filtered.
        center = center_controller._ground_support((0., 4.2248413563, 0.),
                                                   max_distance=.5)
        self.assertIsNotNone(center)
        self.assertEqual(center.collider.source, 'center_slope')


if __name__ == '__main__':
    unittest.main()
