import copy
import math
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, GroundGeometry
from test_world_snapshot import fixture as world_fixture
from world_snapshot import make_world_snapshot


def snapshot(position=(0.0, 0.0, 0.0), *, anchored=False, character='char', root='root'):
    return {
        'local_player': 'player',
        'nodes': [
            {'id': 'player', 'class_name': 'Player', 'properties': {'Character': {'$ref': character}}},
            {'id': character, 'class_name': 'Model', 'parent': 'workspace', 'properties': {}},
            {'id': 'humanoid', 'class_name': 'Humanoid', 'parent': character,
             'properties': {'WalkSpeed': 10}},
            {'id': root, 'class_name': 'Part', 'name': 'HumanoidRootPart', 'parent': character,
             'properties': {'Anchored': anchored, 'Position': {'$vector3': list(position)}}},
        ],
    }


def intent(sequence, kind='Move', vector=(1, 0, 0), instance_id='humanoid', **extra):
    return {'sequence': sequence, 'kind': kind, 'instance_id': instance_id,
            'vector': list(vector), **extra}


class OwnCharacterPhysicsControllerTests(unittest.TestCase):
    def test_move_produces_separate_prediction_with_velocity_and_yaw(self):
        controller = OwnCharacterPhysicsController(snapshot())
        controller.consume([intent(1)])
        report = controller.step(.1, timestamp=12.5)
        # Constant-acceleration displacement: y = y0 - gravity * dt**2 / 2.
        self.assertEqual(report['predicted_state']['position'], [1.0, -0.9810000000000001, 0.0])
        self.assertEqual(report['predicted_state']['linear_velocity'], [10.0, -19.62, 0.0])
        self.assertAlmostEqual(report['predicted_state']['yaw'], -math.pi / 2)
        self.assertFalse(report['server_confirmed'])
        self.assertEqual(report['network_packets_sent'], 0)
        self.assertFalse(report['received_state_modified'])
        self.assertEqual(report['timestamp'], 12.5)

    def test_yaw_matches_roblox_cframe_look_vector_for_cardinal_motion(self):
        # Roblox CFrame.LookVector is the negated Z column. A yaw-only CFrame
        # therefore looks along (-sin(yaw), 0, -cos(yaw)).
        for direction in ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
                          (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)):
            with self.subTest(direction=direction):
                controller = OwnCharacterPhysicsController(snapshot())
                controller.consume([intent(1, vector=direction)])
                yaw = controller.step(.1)['predicted_state']['yaw']
                look = (-math.sin(yaw), 0.0, -math.cos(yaw))
                dot = sum(a*b for a, b in zip(look, direction))
                self.assertAlmostEqual(dot, 1.0, places=12)

        # Explicit cardinal angles make the sign regression easy to diagnose.
        expected = {
            (1.0, 0.0, 0.0): -math.pi / 2,
            (-1.0, 0.0, 0.0): math.pi / 2,
            (0.0, 0.0, 1.0): -math.pi,
            (0.0, 0.0, -1.0): 0.0,
        }
        for direction, angle in expected.items():
            controller = OwnCharacterPhysicsController(snapshot())
            controller.consume([intent(1, vector=direction)])
            yaw = controller.step(.1)['predicted_state']['yaw']
            self.assertAlmostEqual(abs(yaw), abs(angle), places=12)

    def test_move_to_clamps_at_target_and_stop_zeroes_velocity(self):
        controller = OwnCharacterPhysicsController(snapshot())
        controller.consume([intent(1, 'MoveTo', (1.5, 99, 0))])
        report = controller.step(.25)
        self.assertEqual(report['predicted_state']['position'], [1.5, -6.13125, 0.0])
        self.assertIsNone(report['predicted_state']['target'])
        controller.consume([intent(2, 'Move', (0, 0, -1))])
        controller.step(.1)
        controller.consume([intent(3, 'Stop', instance_id='humanoid')])
        velocity = controller.step(.1)['predicted_state']['linear_velocity']
        self.assertEqual(velocity[0], 0)
        self.assertEqual(velocity[2], 0)
        self.assertLess(velocity[1], 0)

    def test_refuses_foreign_camera_relative_and_anchored_intents(self):
        controller = OwnCharacterPhysicsController(snapshot())
        controller.consume([
            intent(1, instance_id='other-humanoid'),
            intent(2, relative_to_camera=True),
        ])
        report = controller.step(.1)
        self.assertEqual(report['predicted_state']['position'], [0.0, -0.9810000000000001, 0.0])
        self.assertEqual(report['rejected_intents'], 2)
        anchored = OwnCharacterPhysicsController(snapshot(anchored=True))
        anchored.consume([intent(1)])
        inactive = anchored.step(.1)['predicted_state']
        self.assertIsNone(inactive)
        self.assertEqual(anchored.last_reason, 'anchored')
        anchored.update_snapshot(snapshot((0, 4, 0), anchored=False))
        self.assertIsNotNone(anchored.step(.1)['predicted_state'])

    def test_unknown_anchored_state_and_invalid_dt_are_refused(self):
        data = snapshot()
        del data['nodes'][-1]['properties']['Anchored']
        controller = OwnCharacterPhysicsController(data)
        controller.consume([intent(1)])
        self.assertIsNone(controller.step(.1)['predicted_state'])
        class_default = snapshot()
        class_default['nodes'][-1]['origins'] = {'Anchored': 'class_default'}
        controller = OwnCharacterPhysicsController(class_default)
        self.assertIsNotNone(controller.step(.1)['predicted_state'])

    def test_verified_native_class_default_allows_gravity(self):
        world, _ = world_fixture()
        snapshot_with_verified_default = make_world_snapshot(world, 123)
        controller = OwnCharacterPhysicsController(snapshot_with_verified_default)
        state = controller.step(.1)['predicted_state']
        self.assertIsNotNone(state)
        self.assertLess(state['position'][1], 2.0)
        root = next(node for node in snapshot_with_verified_default['nodes']
                    if node.get('name') == 'HumanoidRootPart')
        self.assertIs(root['properties']['Anchored'], False)
        self.assertEqual(root['origins']['Anchored'], 'class_default')
        for dt in (-.01, .251, math.inf, True):
            with self.subTest(dt=dt), self.assertRaises(ValueError):
                OwnCharacterPhysicsController(snapshot()).step(dt)

    def test_character_replacement_resets_target_and_prediction(self):
        controller = OwnCharacterPhysicsController(snapshot())
        controller.consume([intent(1)])
        controller.step(.1)
        replacement = snapshot((30, 4, 20), character='char2', root='root2')
        controller.update_snapshot(replacement)
        reset = controller.step(0)['predicted_state']
        self.assertEqual(reset['position'], [30, 4, 20])
        self.assertEqual(reset['linear_velocity'], [0, 0, 0])
        self.assertIsNone(reset['target'])
        report = controller.step(.1)
        self.assertEqual(report['character_id'], 'char2')
        self.assertEqual(report['predicted_state']['position'], [30, 3.019, 20])
        self.assertEqual(report['predicted_state']['linear_velocity'], [0, -19.62, 0])

    def test_gravity_starts_on_spawn_without_a_movement_intent(self):
        controller = OwnCharacterPhysicsController(snapshot((0, 10, 0)))
        self.assertEqual(controller.accepted, 0)
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][1], 9.019)
        self.assertAlmostEqual(state['linear_velocity'][1], -19.62)

    def test_despawn_or_missing_received_pose_stops_stale_simulation(self):
        controller = OwnCharacterPhysicsController(snapshot((0, 10, 0)))
        controller.step(.1)
        absent = {'local_player': 'player', 'nodes': [
            {'id': 'player', 'class_name': 'Player', 'properties': {'Character': {}}}
        ]}
        controller.update_snapshot(absent)
        self.assertIsNone(controller.step(.1)['predicted_state'])
        self.assertEqual(controller.last_reason, 'character_unresolved')

        no_pose = snapshot((0, 10, 0))
        no_pose['nodes'][-1]['properties'].pop('Position', None)
        no_pose['nodes'][-1]['properties'].pop('CFrame', None)
        controller.update_snapshot(no_pose)
        self.assertIsNone(controller.step(.1)['predicted_state'])
        self.assertEqual(controller.last_reason, 'received_pose_unavailable')

    def test_same_character_snapshot_does_not_overwrite_local_prediction(self):
        received = snapshot()
        controller = OwnCharacterPhysicsController(received)
        controller.consume([intent(1)])
        controller.step(.1)
        controller.update_snapshot(copy.deepcopy(received))
        self.assertEqual(controller.step(.1)['predicted_state']['position'], [2.0, -3.9240000000000004, 0.0])

    def test_sequence_and_step_inputs_are_bounded(self):
        controller = OwnCharacterPhysicsController(snapshot())
        controller.consume([intent(2)])
        with self.assertRaises(ValueError):
            controller.consume([intent(2)])
        with self.assertRaises(ValueError):
            controller.consume([intent(3, 'Move', (math.nan, 0, 0))])
        with self.assertRaises(ValueError):
            controller.step(timestamp=math.nan, dt=.1)

    def test_airfall_uses_gravity_without_fabricating_ground(self):
        controller = OwnCharacterPhysicsController(snapshot((0, 10, 0)))
        report = controller.step(.1)
        self.assertAlmostEqual(report['predicted_state']['position'][1], 9.019)
        self.assertAlmostEqual(report['predicted_state']['linear_velocity'][1], -19.62)
        self.assertFalse(report['predicted_state']['grounded'])
        state = report['predicted_state']['humanoid_state']
        self.assertEqual((state['value'], state['name']), (5, 'Freefall'))
        self.assertEqual(state['source'], 'local_physics_model')
        self.assertEqual(state['model'], 'supported_ground_contact_subset')

    def test_ground_contact_and_landing(self):
        geometry = GroundGeometry((AABB((-5., -1., -5.), (5., 1., 5.), 'floor'),), ())
        standing = OwnCharacterPhysicsController(snapshot((0, 4, 0)), geometry=geometry)
        still = standing.step(.1)['predicted_state']
        self.assertEqual(still['position'][1], 4.0)
        self.assertTrue(still['grounded'])
        self.assertEqual(still['linear_velocity'][1], 0.0)
        self.assertEqual(still['humanoid_state']['value'], 8)
        self.assertEqual(still['humanoid_state']['name'], 'Running')

        falling = OwnCharacterPhysicsController(snapshot((0, 5, 0)), geometry=geometry)
        falling.vertical_velocity = -20.0
        landed = falling.step(.1)['predicted_state']
        self.assertEqual(landed['position'][1], 4.0)
        self.assertTrue(landed['grounded'])
        self.assertEqual(landed['linear_velocity'][1], 0.0)
        self.assertEqual(landed['humanoid_state']['name'], 'Running')

    def test_humanoid_state_tracks_fall_landing_stationary_and_walking(self):
        geometry = GroundGeometry((AABB((-50., -1., -50.), (50., 1., 50.), 'floor'),), ())
        controller = OwnCharacterPhysicsController(snapshot((0, 10, 0)), geometry=geometry)
        first = controller.step(.1)['predicted_state']
        self.assertEqual(first['humanoid_state']['name'], 'Freefall')

        state = first
        for _ in range(10):
            state = controller.step(.1)['predicted_state']
            if state['grounded']:
                break
        self.assertTrue(state['grounded'])
        self.assertEqual(state['humanoid_state']['value'], 8)
        self.assertEqual(state['humanoid_state']['name'], 'Running')

        stationary = controller.step(.1)['predicted_state']
        self.assertEqual(stationary['humanoid_state']['name'], 'Running')
        controller.consume([intent(1, vector=(1, 0, 0))])
        walking = controller.step(.1)['predicted_state']
        self.assertTrue(walking['grounded'])
        self.assertEqual(walking['humanoid_state']['name'], 'Running')
        self.assertGreater(walking['position'][0], stationary['position'][0])

    def test_horizontal_move_off_edge_transitions_to_fall(self):
        geometry = GroundGeometry((AABB((-5., -1., -5.), (5., 1., 5.), 'floor'),), ())
        controller = OwnCharacterPhysicsController(snapshot((4.5, 4, 0)), geometry=geometry)
        controller.consume([intent(1)])
        state = controller.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0], 5.5)
        self.assertLess(state['position'][1], 4.0)
        self.assertAlmostEqual(state['linear_velocity'][1], -19.62)
        self.assertFalse(state['grounded'])


if __name__ == '__main__':
    unittest.main()
