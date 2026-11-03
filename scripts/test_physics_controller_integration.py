"""Regression tests for helper settings passed through the live controller."""
import unittest
import math
from types import SimpleNamespace
from unittest.mock import patch

from physics_controller import OwnCharacterPhysicsController
from physics_ground import GroundGeometry
from physics_ground import AABB
from physics_platforms import PlatformPose
from test_physics_controller import snapshot


class ControllerIntegrationTests(unittest.TestCase):
    def _moving(self, slope=89.0, *, max_step_height=1.0):
        state = snapshot()
        state['nodes'][2]['properties']['MaxSlopeAngle'] = slope
        state['nodes'][3]['properties']['Size'] = {'$vector3': [2., 2., 1.]}
        controller = OwnCharacterPhysicsController(
            state, geometry=GroundGeometry((), ()), gravity=0.,
            max_step_height=max_step_height)
        controller.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'humanoid',
                             'vector': [1., 0., 0.]}])
        return controller

    def test_humanoid_slope_limit_and_step_height_reach_body_solver(self):
        controller = self._moving(32.5, max_step_height=.75)
        body = SimpleNamespace(position=(0., 0., 0.), initial_overlap=False,
                               limited=False, normals=())
        with patch('physics_controller.move_horizontal_body', return_value=body) as move:
            controller.step(.1)
        self.assertEqual(move.call_args.kwargs['max_slope_angle'], 32.5)
        self.assertEqual(move.call_args.kwargs['max_step_height'], .75)

    def test_malformed_slope_uses_safe_default(self):
        controller = self._moving(float('nan'))
        self.assertEqual(controller.max_slope_angle, 89.0)

    def test_step_height_validation(self):
        with self.assertRaises(ValueError):
            OwnCharacterPhysicsController(snapshot(), max_step_height=-1)
        with self.assertRaises(ValueError):
            OwnCharacterPhysicsController(snapshot(), max_step_height=True)

    def test_impulse_requires_received_mass_and_applies_bounded_velocity(self):
        state = snapshot()
        root = state['nodes'][3]
        root['origins'] = {'AssemblyMass': 'received', 'Anchored': 'received'}
        root['properties']['AssemblyMass'] = 2.0
        controller = OwnCharacterPhysicsController(state, geometry=GroundGeometry((), ()), gravity=0.)
        outcome = {'sequence': 1, 'kind': 'Impulse', 'instance_id': 'root', 'vector': [20., 0., 0.]}
        controller.consume([outcome])
        report = controller.step(.1)
        self.assertAlmostEqual(report['predicted_state']['position'][0], 1.0)
        self.assertAlmostEqual(report['predicted_state']['linear_velocity'][0], 10.0)

    def test_impulse_rejects_unreceived_mass_and_point_torque(self):
        state = snapshot()
        state['nodes'][3]['origins'] = {'Anchored': 'received'}
        controller = OwnCharacterPhysicsController(state)
        controller.consume([{'sequence': 1, 'kind': 'Impulse', 'instance_id': 'root',
                             'vector': [1., 0., 0.]}])
        self.assertEqual(controller.last_reason, 'received_mass_unavailable')
        root = state['nodes'][3]
        root['properties']['AssemblyMass'] = 1.0
        root['origins'] = {'AssemblyMass': 'received', 'Anchored': 'received'}
        controller.update_snapshot(state)
        controller.consume([{'sequence': 2, 'kind': 'Impulse', 'instance_id': 'root',
                             'vector': [1., 0., 0.], 'position': [0., 0., 0.]}])
        self.assertEqual(controller.last_reason, 'point_impulse_rotation_unavailable')

    def test_impulse_uses_strict_received_member_mass_when_assembly_mass_missing(self):
        state = snapshot()
        state['initial_replication_complete'] = True
        root = state['nodes'][3]
        root['properties'].update(Mass=2., Massless=False,
                                  AssemblyRootPart={'$ref': 'root'})
        root['origins'] = {'Anchored': 'received', 'Mass': 'received',
                           'Massless': 'received', 'AssemblyRootPart': 'received'}
        controller = OwnCharacterPhysicsController(state, geometry=GroundGeometry((), ()), gravity=0.)
        self.assertEqual(controller.assembly_mass_source, 'received_assembly_parts')
        controller.consume([{'sequence': 1, 'kind': 'Impulse', 'instance_id': 'root',
                             'vector': [2., 0., 0.]}])
        self.assertEqual(controller._impulse_velocity[0], 1.)

    def test_received_ladder_and_water_regions_drive_vertical_motion_and_state(self):
        for region_kind, state_name in (('ladder_regions', 'Climbing'),
                                        ('water_regions', 'Swimming')):
            with self.subTest(state=state_name):
                controller = OwnCharacterPhysicsController(
                    snapshot((0., 0., 0.)), geometry=GroundGeometry((), ()), gravity=0.)
                controller.update_environment(**{
                    region_kind: [{'min': [-2., -2., -2.], 'max': [2., 2., 2.],
                                   'source': 'received_geometry', 'id': state_name}]})
                controller.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'humanoid',
                                     'vector': [0., 1., 0.]}])
                report = controller.step(.1)
                self.assertAlmostEqual(report['predicted_state']['position'][1], 1.)
                self.assertEqual(report['predicted_state']['humanoid_state']['name'], state_name)
                self.assertEqual(report['predicted_state']['humanoid_state']['source'],
                                 'local_physics_model')

    def test_received_seat_link_suppresses_movement(self):
        controller = OwnCharacterPhysicsController(snapshot(), geometry=GroundGeometry((), ()))
        controller.update_environment(seat={'id': 'seat', 'source': 'received_seat_link'})
        controller.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'humanoid',
                             'vector': [1., 0., 0.]}])
        self.assertEqual(controller.last_reason, 'seated')
        state = controller.step(.1)['predicted_state']
        self.assertEqual(state['humanoid_state']['name'], 'Seated')
        self.assertEqual(state['position'][0], 0.)

    def test_received_seat_pose_updates_carry_root_without_initial_snap(self):
        controller = OwnCharacterPhysicsController(snapshot((2., 3., 0.)),
                                                    geometry=GroundGeometry((), ()), gravity=0.)
        seat0 = {'id': 'seat', 'source': 'received_seat_link', 'position': [0., 0., 0.]}
        controller.update_environment(seat=seat0, timestamp=1.)
        self.assertEqual(controller.step(.1)['predicted_state']['position'], [2., 3., 0.])
        seat1 = {'id': 'seat', 'source': 'received_seat_link', 'position': [1., 0., 0.]}
        controller.update_environment(seat=seat1, timestamp=1.1)
        state = controller.step(.1)['predicted_state']
        self.assertEqual(state['position'], [3., 3., 0.])
        self.assertEqual(state['humanoid_state']['name'], 'Seated')

    def test_change_state_requires_matching_contact_evidence(self):
        controller = OwnCharacterPhysicsController(snapshot(), geometry=GroundGeometry((), ()))
        controller.consume([{'sequence': 1, 'kind': 'ChangeState', 'instance_id': 'humanoid',
                             'state': 'Swimming'}])
        self.assertEqual(controller.last_reason, 'swimming_state_not_supported')
        controller.update_environment(water_regions=[{'min': [-2., -2., -2.],
            'max': [2., 2., 2.], 'source': 'received_geometry'}])
        controller.consume([{'sequence': 2, 'kind': 'ChangeState', 'instance_id': 'humanoid',
                             'state': 'Swimming'}])
        self.assertEqual(controller.accepted, 1)
        self.assertIsNone(controller.last_reason)

    def test_disabled_states_block_requests_and_automatic_modes(self):
        controller = OwnCharacterPhysicsController(
            snapshot(), geometry=GroundGeometry((), ()), gravity=0.)
        controller.consume([{'sequence': 1, 'kind': 'SetStateEnabled',
                             'instance_id': 'humanoid', 'state': 'Jumping',
                             'state_value': 3, 'enabled': False}])
        controller.consume([{'sequence': 2, 'kind': 'Jump', 'instance_id': 'humanoid'}])
        self.assertEqual(controller.last_reason, 'jump_state_disabled')
        controller.consume([{'sequence': 3, 'kind': 'SetStateEnabled',
                             'instance_id': 'humanoid', 'state': 'Swimming',
                             'state_value': 4, 'enabled': False}])
        controller.update_environment(water_regions=[{'min': [-2., -2., -2.],
            'max': [2., 2., 2.], 'source': 'received_geometry'}])
        report = controller.step(.1)
        self.assertEqual(report['predicted_state']['humanoid_state']['name'], 'Freefall')
        climbing = OwnCharacterPhysicsController(
            snapshot(), geometry=GroundGeometry((), ()), gravity=0.)
        climbing.consume([{'sequence': 1, 'kind': 'SetStateEnabled',
                           'instance_id': 'humanoid', 'state': 'Climbing',
                           'state_value': 12, 'enabled': False}])
        climbing.update_environment(ladder_regions=[{'min': [-2., -2., -2.],
            'max': [2., 2., 2.], 'source': 'received_geometry'}])
        report = climbing.step(.1)
        self.assertEqual(report['predicted_state']['humanoid_state']['name'], 'Freefall')

    def test_disabled_change_state_is_rejected_before_transition(self):
        controller = OwnCharacterPhysicsController(snapshot(), geometry=GroundGeometry((), ()))
        controller.consume([{'sequence': 1, 'kind': 'SetStateEnabled',
                             'instance_id': 'humanoid', 'state': 'Climbing',
                             'state_value': 12, 'enabled': False}])
        controller.update_environment(ladder_regions=[{'min': [-2., -2., -2.],
            'max': [2., 2., 2.], 'source': 'received_geometry'}])
        controller.consume([{'sequence': 2, 'kind': 'ChangeState', 'instance_id': 'humanoid',
                             'state': 'Climbing', 'state_value': 12}])
        self.assertEqual(controller.last_reason, 'humanoid_state_disabled')

    def test_feedback_events_accumulate_until_explicit_acknowledgement(self):
        geometry = GroundGeometry((AABB((-100., -1., -100.), (100., 0., 100.)),), ())
        controller = OwnCharacterPhysicsController(snapshot((0., 3., 0.)),
                                                    geometry=geometry, gravity=100.)
        controller.step(0.)
        controller.consume([{'sequence': 1, 'kind': 'Jump',
                             'instance_id': 'humanoid', 'jump_settings': {'JumpPower': 50.}}])
        controller.step(.1)
        for _ in range(5):
            controller.step(.1)
        events = controller.feedback_events()
        self.assertGreater(events['through_sequence'], 0)
        self.assertIn({'name': 'Jumping', 'value': 3}, events['transitions'])
        self.assertEqual(events['state'], {'name': 'Freefall', 'value': 5})
        self.assertEqual(events['transitions'][-1]['name'], events['state']['name'])
        self.assertIsInstance(events['linear_velocity'], list)
        self.assertIn({'name': 'Jumping', 'active': True}, events['signals'])
        controller.step(0.)
        self.assertEqual(controller.feedback_events(), events)
        controller.ack_feedback(events['through_sequence'])
        self.assertEqual(controller.feedback_events()['transitions'], [])
        self.assertEqual(controller.feedback_events()['signals'], [])

    def test_received_platform_pose_carries_grounded_root(self):
        geometry = GroundGeometry((AABB((-10., -1., -10.), (10., .02, 10.),
                                        source='platform'),), ())
        controller = OwnCharacterPhysicsController(snapshot((0., 3., 0.)),
                                                    geometry=geometry, gravity=0.)
        controller.grounded = True
        controller._ground_source = 'platform'
        controller.update_platform_poses({'platform': PlatformPose((0., 0., 0.))},
                                         {'platform': PlatformPose((0., .02, 0.))}, .1)
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][1], 3.02)
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][1], 3.02)
        controller.consume([{'sequence': 1, 'kind': 'Jump', 'instance_id': 'humanoid'}])
        self.assertAlmostEqual(controller.vertical_velocity, 50.2)

    def test_received_platform_rotation_carries_off_center_rider(self):
        geometry = GroundGeometry((AABB((-20., -1., -20.), (20., 0., 20.),
                                        source='platform'),), ())
        controller = OwnCharacterPhysicsController(snapshot((2., 3., 0.)),
                                                    geometry=geometry, gravity=0.)
        controller.grounded = True
        controller._ground_source = 'platform'
        controller.update_platform_poses(
            {'platform': PlatformPose((0., 0., 0.))},
            {'platform': PlatformPose((0., 0., 0.), (0., 0., 1., 0., 1., 0., -1., 0., 0.))},
            .1)
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0], 0.)
        self.assertAlmostEqual(state['position'][2], -2.)

    def test_platform_carry_is_swept_against_wall(self):
        from test_physics_body_collision import body_snapshot, walls
        data = body_snapshot((0., 3., 0.))
        root = data['nodes'][-1]
        root.setdefault('origins', {}).update(Anchored='received', Size='received')
        geometry = walls(AABB((-20., -1., -20.), (20., 0., 20.), source='platform'),
                         AABB((3., -10., -100.), (3.1, 20., 100.)))
        controller = OwnCharacterPhysicsController(data, geometry=geometry, gravity=0.)
        controller.grounded = True
        controller._ground_source = 'platform'
        controller.update_platform_poses({'platform': PlatformPose((0., 0., 0.))},
                                         {'platform': PlatformPose((2.5, 0., 0.))}, .1)
        state = controller.step(.1)['predicted_state']
        self.assertLessEqual(state['position'][0], 2.00001)

    def test_received_conveyor_velocity_carries_only_while_supported(self):
        geometry = GroundGeometry((AABB((-.25, -1., -10.), (.25, 0., 10.),
                                        source='belt'),), ())
        controller = OwnCharacterPhysicsController(snapshot((0., 3., 0.)),
                                                    geometry=geometry, gravity=0.)
        controller.grounded = True
        controller._ground_source = 'belt'
        controller.update_surface_velocities({'belt': (4., 0., 0.)})
        state = controller.step(.25)['predicted_state']
        self.assertAlmostEqual(state['position'][0], 1.)
        self.assertFalse(state['grounded'])
        state = controller.step(.25)['predicted_state']
        self.assertAlmostEqual(state['position'][0], 2.)

    def test_impulse_is_clipped_by_wall_collision(self):
        from test_physics_body_collision import body_snapshot, walls
        state = body_snapshot()
        root = state['nodes'][-1]
        root['properties']['AssemblyMass'] = 2.0
        root.setdefault('origins', {}).update(AssemblyMass='received', Anchored='received',
                                               Size='received')
        wall = AABB((2., -10., -100.), (2.1, 20., 100.))
        controller = OwnCharacterPhysicsController(
            state, geometry=walls(wall), gravity=0.)
        controller.consume([{'sequence': 1, 'kind': 'Impulse', 'instance_id': 'root',
                             'vector': [20., 0., 0.] }])
        state = controller.step(.25)['predicted_state']
        self.assertLessEqual(state['position'][0], 1.00001)
        self.assertEqual(state['linear_velocity'][0], 0.)

    def test_received_world_force_integrates_with_assembly_mass(self):
        state = snapshot()
        root = state['nodes'][3]
        root['properties']['AssemblyMass'] = 2.0
        root.setdefault('origins', {}).update(AssemblyMass='received', Anchored='received')
        controller = OwnCharacterPhysicsController(state, geometry=GroundGeometry((), ()), gravity=0.)
        controller.update_force_sources([{'id': 'force', 'part_id': 'root',
            'kind': 'BodyForce', 'source': 'received_force', 'relative_to': 'World',
            'force': [20., 0., 0.]}])
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0], .05)
        self.assertAlmostEqual(state['linear_velocity'][0], 1.)

    def test_over_limit_received_slope_slides_down_without_intent(self):
        from test_physics_slopes import ramp_world
        from physics_ground import extract_ground
        angle = math.pi/6
        initial = 3 + 1/math.cos(angle)
        data = snapshot((0., initial, 0.))
        data['nodes'][2]['properties']['MaxSlopeAngle'] = 20.
        controller = OwnCharacterPhysicsController(
            data, geometry=extract_ground(ramp_world()), gravity=100.)
        self.assertTrue(controller.grounded)
        before = controller.controller.position
        state = controller.step(.1)['predicted_state']
        self.assertLess(state['position'][0], before[0])
        self.assertEqual(state['environment_evidence']['ladder'], None)


if __name__ == '__main__':
    unittest.main()
