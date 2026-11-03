import unittest

from physics_script_bridge import PhysicsScriptBridge


def snapshot(character='char1', humanoid='hum1'):
    return {'local_player': 'player1', 'nodes': [
        {'id': 'player1', 'properties': {'Character': {'$ref': character}}},
        {'id': character, 'parent': None, 'class_name': 'Model', 'properties': {}},
        {'id': humanoid, 'parent': character, 'class_name': 'Humanoid', 'properties': {}},
    ]}


class FakeController:
    def __init__(self, world):
        self.world = world
        self.received = []

    def update_snapshot(self, world):
        self.world = world

    def consume(self, intents):
        self.received.extend(intents)

    def step(self, dt, timestamp=None):
        return {'dt': dt, 'timestamp': timestamp}

    def report(self):
        return {'received_count': len(self.received)}


class PhysicsScriptBridgeTests(unittest.TestCase):
    def make_bridge(self):
        instances = []

        def factory(world):
            controller = FakeController(world)
            instances.append(controller)
            return controller

        return PhysicsScriptBridge(factory), instances

    def test_preserves_sequence_and_actual_receiver(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        intent = {'sequence': 8, 'kind': 'Move', 'instance_id': 'hum1',
                  'vector': [1, 0, 0], 'relative_to_camera': False}
        self.assertEqual(bridge.consume([intent]), [
            {'sequence': 8, 'instance_id': 'hum1', 'accepted': True, 'reason': None}])
        self.assertEqual(instances[0].received, [intent])
        self.assertEqual(bridge.last_sequence, 8)
        self.assertEqual(bridge.report()['accepted_intents'], 1)
        with self.assertRaises(ValueError):
            bridge.consume([intent])

    def test_forwards_humanoid_state_requests_without_retargeting(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        intent = {'sequence': 1, 'kind': 'ChangeState', 'instance_id': 'hum1',
                  'state': 'Jumping', 'state_value': 3}
        self.assertEqual(bridge.consume([intent]), [
            {'sequence': 1, 'instance_id': 'hum1', 'accepted': True, 'reason': None}])
        self.assertEqual(instances[0].received, [intent])
        rejected = bridge.consume([{'sequence': 2, 'kind': 'ChangeState',
                                    'instance_id': 'player1', 'state': 'Jumping',
                                    'state_value': 3}])
        self.assertFalse(rejected[0]['accepted'])
        self.assertEqual(rejected[0]['reason'], 'state_change_requires_humanoid')

    def test_forwards_state_enablement_requests_to_controller(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        intent = {'sequence': 1, 'kind': 'SetStateEnabled', 'instance_id': 'hum1',
                  'state': 'Jumping', 'state_value': 3, 'enabled': False}
        self.assertTrue(bridge.consume([intent])[0]['accepted'])
        self.assertEqual(instances[0].received, [intent])

    def test_sit_override_is_local_and_reaches_prediction_view(self):
        bridge, instances = self.make_bridge()
        received = snapshot()
        bridge.update(received)
        self.assertEqual(bridge.apply_mutations([
            {'kind': 'set', 'id': 'hum1', 'property': 'Sit', 'value': True}]), 1)
        effective = instances[0].world
        humanoid = next(node for node in effective['nodes'] if node.get('id') == 'hum1')
        self.assertTrue(humanoid['properties']['Sit'])
        self.assertEqual(humanoid['origins']['Sit'], 'local')
        self.assertNotIn('Sit', received['nodes'][2]['properties'])

    def test_state_enablement_survives_root_rebuild_for_same_character_only(self):
        def world(character='char1', humanoid='hum1', root='root1'):
            return {'local_player': 'player1', 'nodes': [
                {'id': 'player1', 'class_name': 'Player', 'properties': {'Character': {'$ref': character}}},
                {'id': character, 'class_name': 'Model', 'properties': {}},
                {'id': humanoid, 'class_name': 'Humanoid', 'parent': character, 'properties': {}},
                {'id': root, 'class_name': 'Part', 'name': 'HumanoidRootPart', 'parent': character,
                 'properties': {'Anchored': False, 'Position': {'$vector3': [0, 0, 0]}}},
            ]}
        bridge = PhysicsScriptBridge()
        bridge.update(world())
        bridge.consume([{'sequence': 1, 'kind': 'SetStateEnabled', 'instance_id': 'hum1',
                         'state': 'Swimming', 'state_value': 4, 'enabled': False}])
        self.assertFalse(bridge.controller.state_enabled['Swimming'])
        bridge.update(world(root='root2'))
        self.assertFalse(bridge.controller.state_enabled['Swimming'])
        bridge.update(world(character='char2', humanoid='hum2', root='root3'))
        self.assertTrue(bridge.controller.state_enabled['Swimming'])

    def test_camera_relative_is_explicitly_unsupported_and_consumed(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        outcome = bridge.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'player1',
                                   'vector': [0, 0, -1], 'relative_to_camera': True}])
        self.assertEqual(outcome[0]['reason'], 'camera_transform_unavailable')
        self.assertEqual(instances[0].received, [])
        self.assertEqual(bridge.last_sequence, 1)

    def test_camera_relative_moveto_is_explicitly_unsupported(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        outcome = bridge.consume([{'sequence': 1, 'kind': 'MoveTo', 'instance_id': 'hum1',
                                   'vector': [1, 0, 0], 'relative_to_camera': True}])
        self.assertFalse(outcome[0]['accepted'])
        self.assertEqual(outcome[0]['reason'], 'camera_transform_unavailable')
        self.assertEqual(instances[0].received, [])

    def test_wrong_receiver_is_never_retargeted(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        result = bridge.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'other',
                                  'vector': [1, 0, 0], 'relative_to_camera': False}])
        self.assertEqual(result[0]['reason'], 'different_character')
        self.assertEqual(instances[0].received, [])

    def test_character_replacement_rebuilds_controller(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        bridge.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'hum1',
                         'vector': [1, 0, 0], 'relative_to_camera': False}])
        bridge.update(snapshot('char2', 'hum2'))
        self.assertEqual(len(instances), 2)
        self.assertEqual(bridge.last_sequence, 1)
        bridge.consume([{'sequence': 2, 'kind': 'Move', 'instance_id': 'hum2',
                         'vector': [0, 0, 1], 'relative_to_camera': False}])
        self.assertEqual(instances[1].received[0]['instance_id'], 'hum2')

    def test_disconnect_clears_controller_but_preserves_sequence_guard(self):
        bridge, instances = self.make_bridge()
        bridge.update(snapshot())
        bridge.consume([{'sequence': 4, 'kind': 'Move', 'instance_id': 'player1',
                         'vector': [1, 0, 0], 'relative_to_camera': False}])
        bridge.update(None, connected=False)
        self.assertIsNone(bridge.controller)
        self.assertEqual(bridge.step(.1)['reason'], 'disconnected')
        bridge.update(snapshot())
        with self.assertRaises(ValueError):
            bridge.consume([{'sequence': 4, 'kind': 'Move', 'instance_id': 'player1',
                             'vector': [1, 0, 0], 'relative_to_camera': False}])

    def test_default_controller_runs_only_a_local_prediction(self):
        from physics_controller import OwnCharacterPhysicsController

        world = {'local_player': 'player1', 'nodes': [
            {'id': 'player1', 'class_name': 'Player', 'properties': {'Character': {'$ref': 'char1'}}},
            {'id': 'char1', 'class_name': 'Model', 'parent': None, 'properties': {}},
            {'id': 'hum1', 'class_name': 'Humanoid', 'parent': 'char1',
             'properties': {'WalkSpeed': 16}},
            {'id': 'root1', 'class_name': 'Part', 'name': 'HumanoidRootPart', 'parent': 'char1',
             'properties': {'Anchored': False, 'Position': {'$vector3': [0, 0, 0]}}},
        ]}
        bridge = PhysicsScriptBridge()
        bridge.update(world)
        self.assertIsInstance(bridge.controller, OwnCharacterPhysicsController)
        received_position = list(world['nodes'][3]['properties']['Position']['$vector3'])
        outcome = bridge.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'hum1',
                                   'vector': [0, 0, -1], 'relative_to_camera': False}])
        self.assertTrue(outcome[0]['accepted'], outcome)
        state = bridge.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0], 0.0)
        self.assertAlmostEqual(state['position'][1], -0.981)
        self.assertAlmostEqual(state['position'][2], -1.6)
        self.assertAlmostEqual(state['linear_velocity'][1], -19.62)
        self.assertFalse(state['grounded'])
        # Prediction is separate evidence; the received world pose stays intact.
        self.assertEqual(world['nodes'][3]['properties']['Position']['$vector3'], received_position)
        report = bridge.report()
        self.assertFalse(report['server_confirmed'])
        self.assertEqual(report['network_packets_sent'], 0)
        self.assertAlmostEqual(report['controller']['predicted_state']['position'][1], -0.981)

    def test_default_bridge_reports_controller_rejection_for_anchored_root(self):
        world = {'local_player': 'player1', 'nodes': [
            {'id': 'player1', 'class_name': 'Player', 'properties': {'Character': {'$ref': 'char1'}}},
            {'id': 'char1', 'class_name': 'Model', 'parent': None, 'properties': {}},
            {'id': 'hum1', 'class_name': 'Humanoid', 'parent': 'char1',
             'properties': {'WalkSpeed': 16}},
            {'id': 'root1', 'class_name': 'Part', 'name': 'HumanoidRootPart', 'parent': 'char1',
             'properties': {'Anchored': True, 'Position': {'$vector3': [0, 0, 0]}}},
        ]}
        bridge = PhysicsScriptBridge()
        bridge.update(world)
        outcome = bridge.consume([{'sequence': 1, 'kind': 'Move', 'instance_id': 'hum1',
                                  'vector': [1, 0, 0]}])
        self.assertFalse(outcome[0]['accepted'])
        self.assertEqual(outcome[0]['reason'], 'anchored')
        self.assertEqual(bridge.report()['accepted_intents'], 0)
        self.assertEqual(bridge.report()['rejected_intents'], 1)


if __name__ == '__main__':
    unittest.main()
