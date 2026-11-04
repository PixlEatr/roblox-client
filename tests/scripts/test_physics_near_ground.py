"""Received foot-height contact remains stable through root-box recovery."""
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, GroundGeometry
from test_physics_foot_offset import r15_snapshot


class NearGroundContactTests(unittest.TestCase):
    def controller(self, y, geometry):
        snapshot = r15_snapshot()
        root = snapshot['nodes'][3]
        root['properties']['Position'] = {'$vector3': [0., y, 0.]}
        root['properties']['CFrame'] = {'$cframe': {'position': [0., y, 0.]}}
        return OwnCharacterPhysicsController(snapshot, geometry=geometry,
                                             gravity=196.2)

    def test_root_box_floor_contact_aligns_to_received_humanoid_foot_height(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.), 'floor')
        controller = self.controller(1.2, GroundGeometry((floor,), ()))
        self.assertFalse(controller.grounded)

        landed = controller.step(.05)['predicted_state']
        self.assertTrue(landed['grounded'])
        self.assertAlmostEqual(landed['position'][1], 3.2248413563, places=8)
        for _ in range(4):
            landed = controller.step(.05)['predicted_state']
            self.assertTrue(landed['grounded'])
            self.assertAlmostEqual(landed['position'][1], 3.2248413563, places=8)

    def test_shallow_floor_overlap_recovers_only_with_direct_upward_support(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.), 'floor')
        controller = self.controller(.95, GroundGeometry((floor,), ()))
        recovered = controller.step(.05)['predicted_state']
        self.assertTrue(recovered['grounded'])
        self.assertAlmostEqual(recovered['position'][1], 3.2248413563, places=8)

        wall = AABB((-.5, 0., -.5), (.5, 2., .5), 'wall')
        blocked = self.controller(.95, GroundGeometry((wall,), ())).step(.05)
        self.assertFalse(blocked['predicted_state']['grounded'])
        self.assertTrue(blocked['body_collision']['initial_overlap'])
        self.assertAlmostEqual(blocked['predicted_state']['position'][1], .95)

    def test_thin_ceiling_between_clear_endpoints_blocks_ground_alignment_sweep(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.), 'floor')
        ceiling = AABB((-2., 2.1, -2.), (2., 2.15, 2.), 'thin_ceiling')
        geometry = GroundGeometry((floor, ceiling), ())
        controller = self.controller(1., geometry)
        landed = controller.step(.05)['predicted_state']
        self.assertTrue(landed['grounded'])
        self.assertAlmostEqual(landed['position'][1], 1.000001, places=6)
        # At the target foot height, the root box would be above the thin
        # ceiling; only a swept path detects the ceiling between endpoints.
        self.assertGreater(landed['position'][1]-1., 0.)
        for _ in range(3):
            landed = controller.step(.05)['predicted_state']
            self.assertTrue(landed['grounded'])
            self.assertAlmostEqual(landed['position'][1], 1.000001, places=6)

    def test_wall_initial_overlap_does_not_ground_or_cancel_upward_jump(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.), 'floor')
        wall = AABB((-.5, 2.5, -.5), (.5, 4., .5), 'wall')
        controller = self.controller(3.2248413563,
                                     GroundGeometry((floor, wall), ()))
        controller.step(0.)
        self.assertTrue(controller.grounded)
        controller.consume([{'sequence': 1, 'kind': 'Jump',
                             'instance_id': 'humanoid',
                             'jump_settings': {'JumpPower': 50.}}])
        state = controller.step(.05)['predicted_state']
        self.assertFalse(state['grounded'])
        self.assertAlmostEqual(state['vertical_velocity'], 50.-196.2*.05)
        self.assertEqual(state['humanoid_state']['name'], 'Jumping')
        for _ in range(6):
            state = controller.step(.05)['predicted_state']
        self.assertNotEqual(state['humanoid_state']['name'], 'Jumping')


if __name__ == '__main__':
    unittest.main()
