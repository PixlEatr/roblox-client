"""Bridge regressions for local owned-part physics feedback updates."""
from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from persistent_runtime import PersistentClientScriptRuntime
from test_live_physics_trial import ready_world


def runtime_with_engine_updates(world, updates):
    runtime = PersistentClientScriptRuntime(lambda: world, user_id=1)
    runtime.host = Mock()
    runtime.host.call.return_value = {'ok': True}
    runtime.host.initial_result = {'engine_owned_part_feedback_version': 1}
    runtime.authored_part_poses = SimpleNamespace(engine_updates=lambda current: updates,
                                                 simulation=object())
    runtime._ensure = lambda: world
    runtime._world_update = lambda current: ({'actions': [], 'sequence': 1}, False, False, True)
    runtime._take_engine_poses = lambda current: None
    runtime._record = lambda result: result
    runtime.script_catalog = {}
    runtime.initial_replication_complete = False
    runtime._catalog_catchup_needed = False
    return runtime


class RuntimeEngineOwnedPartsTests(unittest.TestCase):
    def test_synchronize_includes_owned_part_feedback_even_without_world_actions(self):
        world, _ = ready_world()
        updates = {'source_revision': world.revision, 'frames': [
            {'id': '1:50', 'cframe': {'$cframe': {
                'position': [1., 2., 3.],
                'rotation': [1., 0., 0., 0., 1., 0., 0., 0., 1.]}},
             'linear_velocity': [0., -1., 0.], 'angular_velocity': [0., 0., 0.]}
        ]}
        runtime = runtime_with_engine_updates(world, updates)
        result = runtime._synchronize_once()
        self.assertTrue(result['ok'])
        request = runtime.host.call.call_args.args[0]
        self.assertEqual(request['op'], 'update')
        self.assertEqual(request['delta']['actions'], [])
        self.assertEqual(request['delta']['engine_owned_part_updates'], updates)

    def test_background_update_includes_owned_part_feedback_without_world_actions(self):
        world, _ = ready_world()
        updates = {'source_revision': world.revision, 'frames': [{'id': '1:50'}]}
        runtime = runtime_with_engine_updates(world, updates)
        runtime._prepare_chat_responses = lambda: []
        runtime.remote_server_results = deque()
        runtime.stats = {}
        runtime.last_poll = 0.
        captured = []
        runtime._background_lane = Mock()
        runtime._background_lane.submit_owned.side_effect = lambda command: captured.append(command) or True
        runtime.poll_background()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['op'], 'update')
        self.assertEqual(captured[0]['delta']['actions'], [])
        self.assertEqual(captured[0]['delta']['engine_owned_part_updates'], updates)
        self.assertEqual(runtime._background_context['op'], 'update')

    def test_old_worker_without_feedback_capability_is_rejected(self):
        world, _ = ready_world()
        runtime = runtime_with_engine_updates(world, {'frames': []})
        runtime.host.initial_result = {'ok': True}
        with self.assertRaisesRegex(RuntimeError, 'requires a worker with engine feedback version 1'):
            runtime._take_engine_owned_parts(world)


if __name__ == '__main__':
    unittest.main()
