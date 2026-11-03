"""Actual native Luau intent delivery into composed own-root physics frames."""
import time
import unittest
from pathlib import Path

from local_runtime import RUST_HOST
from live_physics_trial import LivePhysicsTrial
from live_character_physics import LiveCharacterPhysics
from persistent_runtime import PersistentClientScriptRuntime
from physics_runtime_prediction import RuntimePhysicsPrediction
from test_live_physics_trial import ready_world


@unittest.skipUnless(RUST_HOST.is_file(), 'build the native host first')
class LuauPhysicsPipelineTests(unittest.TestCase):
    def test_walk_deadline_does_not_accumulate_late_resumptions(self):
        world, _ = ready_world()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        self.addCleanup(runtime.close)
        source = (Path(__file__).resolve().parents[1]/
                  'runtime/scripts/walk_for_minute.luau').read_text()
        # Execute the real script with a deterministic scheduler that resumes
        # 400ms late every time. Relative one-second waits would take 84s.
        result = runtime.execute('''
local clock = 0
local elapsedTime = function() return clock end
local waits = 0
local task = {wait = function(seconds)
    clock += seconds + 0.4
    waits += 1
end}
local walk = function()
''' + source + '''
end
local status = walk()
return status, clock, waits
''')
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['values'][0], 'walk_completed')
        self.assertGreaterEqual(result['values'][1], 60.)
        self.assertLess(result['values'][1], 60.5)
        self.assertEqual(result['movement_intents'][-1]['vector'], [0,0,0])

    def test_native_move_and_stop_reach_continuous_physics_packets(self):
        from physics_packet import decode_transform_component
        from physics_root_record import encode_instance_reference
        world, root = ready_world()
        predictor = RuntimePhysicsPrediction()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        runtime.movement_preview = predictor
        self.addCleanup(runtime.close)
        driver = LiveCharacterPhysics(experimental_enabled=True, prediction=predictor,
                                      use_predicted_humanoid_state=True,
                                      use_predicted_rotation=True)
        sent = []
        start = time.monotonic()

        def tick(now):
            return driver.tick(world=world, own_user_id=123, now=now, delta=.05,
                               timestamp_ms=int(now*1000), connected=True,
                               send_unreliable=lambda channel, payload, **kw: sent.append(payload))

        tick(start)
        source = 'game:GetService("Players").LocalPlayer.Character.Humanoid:Move(Vector3.new(%s,0,0),false)'
        moving = runtime.execute(source % '1')
        self.assertTrue(moving['ok'], moving.get('error'))
        time.sleep(.05)
        moved = tick(time.monotonic())
        self.assertGreater(moved['predicted_position'][0], 1.)
        stopped = runtime.execute(source % '0')
        self.assertTrue(stopped['ok'], stopped.get('error'))
        time.sleep(.05)
        tick(time.monotonic())

        offset = 18 + len(encode_instance_reference(world.client_scope, root)) + 2
        def transform(payload):
            flags = payload[offset]
            tail = 2 if flags & 0x20 else 3
            return decode_transform_component(payload[offset+2:-tail],
                                               velocity_mode='short' if flags & 0x40 else 'compact')
        self.assertGreater(transform(sent[-2]).linear_velocity.vector[0], 0.)
        self.assertAlmostEqual(transform(sent[-1]).linear_velocity.vector[0], 0., delta=.01)
        self.assertEqual(world.instances[root]['properties']['CFrame']['position'], [1.,2.,3.])
        self.assertFalse(driver.report()['server_acceptance_confirmed'])

    def test_actual_minute_walk_source_starts_and_yields_with_a_move_intent(self):
        world, _ = ready_world()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        self.addCleanup(runtime.close)
        source = (Path(__file__).resolve().parents[1]/
                  'runtime/scripts/walk_for_minute.luau').read_text()
        result = runtime.execute(source)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['state'], 'running')
        self.assertEqual(result['local_movement']['accepted_intents'], 1)
        self.assertGreater(result['pending_tasks'], 0)

    def test_native_move_drives_shared_prediction_without_changing_received_pose(self):
        world, root = ready_world()
        predictor = RuntimePhysicsPrediction()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        runtime.movement_preview = predictor
        self.addCleanup(runtime.close)
        result = runtime.execute('''
game:GetService("Players").LocalPlayer.Character.Humanoid:Move(
    Vector3.new(1,0,0),false)
return true
''')
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['local_movement']['accepted_intents'], 1)
        trial = LivePhysicsTrial(experimental_enabled=True,
                                 local_space_max_send_index=255,
                                 settle_ticks=3, prediction=predictor,
                                 use_predicted_humanoid_state=True,
                                 use_predicted_rotation=True)
        sent = []
        start = time.monotonic()
        for index in range(5):
            now = start+.05*(index+1)
            report = trial.tick(
                world=world, own_user_id=123, now=now, delta=.05,
                timestamp_ms=int(now*1000), connected=True,
                send_unreliable=lambda *args, **kwargs: sent.append((args, kwargs)))
        self.assertEqual(len(sent), 2)
        self.assertGreater(report['predicted_position'][0], 1.)
        self.assertEqual(world.instances[root]['properties']['CFrame']['position'], [1.,2.,3.])
        self.assertFalse(report['server_acceptance_confirmed'])
        from physics_root_record import encode_instance_reference
        flags_offset = 18 + len(encode_instance_reference(world.client_scope, root)) + 2
        self.assertEqual([entry[0][1][flags_offset] & 0x1f for entry in sent], [5, 5])
        self.assertEqual(report['humanoid_states_submitted'], {'5': 2})
        from physics_packet import decode_transform_component
        for entry in sent:
            payload = entry[0][1]
            # Outer flags + zero motor count precede the transform. Suffixes
            # remain the root reference frame, inner child null and outer null.
            transform = decode_transform_component(payload[flags_offset+2:-3],
                                                   velocity_mode='compact')
            self.assertEqual(transform.header.rotation_selector, 0)
            x, y, z, w = transform.rotation.value
            look = (-2*(x*z+y*w), -2*(y*z-x*w), -(1-2*(x*x+y*y)))
            self.assertAlmostEqual(look[0], 1., delta=.0003)
            self.assertAlmostEqual(look[1], 0., delta=.0003)
            self.assertAlmostEqual(look[2], 0., delta=.0003)
        self.assertEqual(report['rotation_source'], 'local_physics_model_yaw')


if __name__ == '__main__':
    unittest.main()
