import copy
import math
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_foot_offset import derive_foot_offset
from physics_ground import AABB, GroundGeometry
from physics_script_bridge import PhysicsScriptBridge
from test_physics_controller import snapshot


def r15_snapshot(*, hip_height=2.2248413563, root_y=2.):
    world = snapshot((0., 10., 0.))
    humanoid, root = world['nodes'][2:4]
    humanoid['properties'].update({'RigType': 1, 'HipHeight': hip_height})
    humanoid['origins'] = {'RigType': 'received', 'HipHeight': 'received'}
    root['properties']['Size'] = {'$vector3': [2., root_y, 1.]}
    root.setdefault('origins', {}).update({'Size': 'derived_received',
                                           'Anchored': 'received'})
    return world


def r6_snapshot(*, hip_height=.5, root_y=2., left_y=2., left_ref=True):
    world = snapshot((0., 10., 0.))
    humanoid, root = world['nodes'][2:4]
    humanoid['properties'].update({'RigType': 0, 'HipHeight': hip_height})
    humanoid['origins'] = {'RigType': 'received', 'HipHeight': 'received'}
    root['properties']['Size'] = {'$vector3': [2., root_y, 1.]}
    root.setdefault('origins', {}).update({'Size': 'derived_received',
                                           'Anchored': 'received'})
    if left_ref:
        humanoid['properties']['LeftLeg'] = {'$ref': 'left-leg'}
        humanoid['origins']['LeftLeg'] = 'received'
        world['nodes'].append({
            'id': 'left-leg', 'class_name': 'Part', 'name': 'Left Leg',
            'parent': 'char', 'properties': {'Size': {'$vector3': [1., left_y, 1.]}},
            'origins': {'Size': 'received'},
        })
    return world


class FootOffsetTests(unittest.TestCase):
    def test_r15_uses_received_hip_height_and_derived_root_size(self):
        offset, origin = derive_foot_offset(r15_snapshot())
        self.assertAlmostEqual(offset, 3.2248413563)
        self.assertEqual(origin, 'r15_received_root_size_and_hip_height')

    def test_r6_requires_received_left_leg_reference_and_size(self):
        offset, origin = derive_foot_offset(r6_snapshot())
        self.assertEqual(offset, 3.5)
        self.assertEqual(origin, 'r6_received_root_left_leg_size_and_hip_height')
        fallback, origin = derive_foot_offset(r6_snapshot(left_ref=False))
        self.assertEqual(fallback, 3.)
        self.assertIn('fallback_default_3', origin)
        self.assertIn('missing_received_r6_left_leg_size', origin)

    def test_missing_or_invalid_provenance_and_nonfinite_values_fall_back(self):
        world = r15_snapshot()
        world['nodes'][2]['origins']['RigType'] = 'class_default'
        self.assertEqual(derive_foot_offset(world), (3., 'fallback_default_3_missing_received_rig_size'))

        for update in (
            lambda data: data['nodes'][2]['properties'].__setitem__('HipHeight', math.inf),
            lambda data: data['nodes'][3]['properties'].__setitem__('Size', {'$vector3': [2., math.nan, 1.]}),
            lambda data: data['nodes'][2]['properties'].__setitem__('RigType', True),
        ):
            data = r15_snapshot()
            update(data)
            offset, origin = derive_foot_offset(data)
            self.assertEqual(offset, 3.)
            self.assertTrue(origin.startswith('fallback_default_3_'))

    def test_controller_updates_offset_on_scaling_without_resetting_motion(self):
        original = r15_snapshot()
        controller = OwnCharacterPhysicsController(original)
        controller.step(.1)
        before_position = controller.controller.position
        before_y = controller.vertical_position
        before_vy = controller.vertical_velocity
        before_velocity = controller.linear_velocity

        scaled = copy.deepcopy(original)
        scaled['nodes'][2]['properties']['HipHeight'] = 3.0
        scaled['nodes'][3]['properties']['Size'] = {'$vector3': [2., 4., 1.]}
        controller.update_snapshot(scaled)
        self.assertEqual(controller.foot_offset, 5.)
        self.assertEqual(controller.foot_offset_origin, 'r15_received_root_size_and_hip_height')
        self.assertEqual(controller.controller.position, before_position)
        self.assertEqual(controller.vertical_position, before_y)
        self.assertEqual(controller.vertical_velocity, before_vy)
        self.assertEqual(controller.linear_velocity, before_velocity)
        self.assertEqual(controller.report()['foot_offset'], 5.)

    def test_grounded_controller_applies_grown_received_offset_without_falling(self):
        floor = AABB((-10., -1., -10.), (10., 0., 10.))
        original = r15_snapshot(hip_height=2., root_y=2.)
        original['nodes'][3]['properties']['Position'] = {'$vector3': [0., 3., 0.]}
        controller = OwnCharacterPhysicsController(
            original, geometry=GroundGeometry((floor,), ()))
        self.assertTrue(controller.grounded)
        self.assertEqual(controller.foot_offset, 3.)

        scaled = copy.deepcopy(original)
        scaled['nodes'][2]['properties']['HipHeight'] = 4.
        controller.update_snapshot(scaled)
        report = controller.step(.1)
        self.assertEqual(controller.foot_offset, 5.)
        self.assertEqual(report['predicted_state']['position'], [0., 5., 0.])
        self.assertTrue(report['predicted_state']['grounded'])
        self.assertEqual(controller.vertical_velocity, 0.)

    def test_bridge_refresh_applies_received_character_scaling(self):
        original = r15_snapshot()
        bridge = PhysicsScriptBridge()
        bridge.update(original)
        bridge.step(.1)
        controller = bridge.controller
        before_position = controller.controller.position
        before_y = controller.vertical_position
        before_vy = controller.vertical_velocity

        scaled = copy.deepcopy(original)
        scaled['nodes'][2]['properties']['HipHeight'] = 3.0
        scaled['nodes'][3]['properties']['Size'] = {'$vector3': [2., 4., 1.]}
        bridge.update(scaled)
        self.assertEqual(controller.foot_offset, 5.)
        self.assertEqual(controller.controller.position, before_position)
        self.assertEqual(controller.vertical_position, before_y)
        self.assertEqual(controller.vertical_velocity, before_vy)
        self.assertEqual(bridge.report()['controller']['foot_offset_origin'],
                         'r15_received_root_size_and_hip_height')


if __name__ == '__main__':
    unittest.main()
