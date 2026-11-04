import unittest
from types import SimpleNamespace
from unittest.mock import patch

import persistent_runtime
from persistent_runtime import PersistentClientScriptRuntime


class FakeHost:
    def __init__(self):
        self.initial_result = {'ok': True, 'values': []}
        self.calls = []

    def call(self, command):
        self.calls.append(command)
        return {'ok': True, 'values': [], 'completed_jobs': [], 'pending_tasks': 0,
                'task_errors': [], 'active_jobs': 0, 'output': ''}

    def close(self):
        pass


def fake_world(*, revision=0, tags=(), shared=None):
    return SimpleNamespace(revision=revision, tags=list(tags), shared=shared or {},
                           actions=[], instances={}, top_containers={},
                           schema={'classes': [], 'properties': []},
                           avatar=lambda user_id: {'player_key': None, 'character_key': None})


def ready_runtime(world, *, budget=50):
    runtime = PersistentClientScriptRuntime(lambda: world, 123,
                                            background_budget_ms=budget)
    runtime.host = FakeHost()
    runtime.world = world
    runtime.revision = world.revision
    runtime.script_catalog = {}
    runtime.initial_replication_complete = 13 in world.tags
    runtime.shared_entry_count = len(world.shared)
    return runtime


class StartupRuntimeBudgetTests(unittest.TestCase):
    def test_budget_is_bounded_to_keep_network_ticks_fair(self):
        for invalid in (0, 51, True, 1.5):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PersistentClientScriptRuntime(lambda: None, 123,
                                              background_budget_ms=invalid)

    def test_poll_uses_configured_budget_when_no_update_is_needed(self):
        world = fake_world(revision=4, tags=[13])
        runtime = ready_runtime(world, budget=37)
        try:
            with patch.object(persistent_runtime, 'script_catalog',
                              side_effect=AssertionError('catalog rescanned on idle tick')):
                result = runtime.poll(force=True)
            self.assertTrue(result['ok'])
            self.assertEqual([call['op'] for call in runtime.host.calls], ['poll'])
            self.assertEqual(runtime.host.calls[0]['timeout_ms'], 37)
        finally:
            runtime.close()

    def test_world_update_replaces_redundant_poll_in_same_tick(self):
        world = fake_world(revision=5, tags=[13])
        runtime = ready_runtime(world, budget=35)
        runtime.revision = 4
        try:
            with patch.object(persistent_runtime, 'make_world_delta', return_value={
                    'version': 1, 'delta': True, 'sequence': 5, 'actions': [
                        {'sequence': 5, 'kind': 'event', 'id': 'scope:event'}]}), \
                 patch.object(persistent_runtime, 'script_catalog',
                              side_effect=AssertionError('non-source event rescanned catalog')):
                result = runtime.poll(force=True)
            self.assertTrue(result['ok'])
            self.assertEqual([call['op'] for call in runtime.host.calls], ['update'])
            self.assertEqual(runtime.host.calls[0]['timeout_ms'], 35)
        finally:
            runtime.close()

    def test_initial_source_catalog_is_deferred_until_replication_marker(self):
        world = fake_world()
        world.instances = {'scope:players': {}}
        world.top_containers = {'scope:players': {}}
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        calls = []

        def snapshot(*args, **kwargs):
            calls.append(kwargs)
            return {'local_player': 'scope:player',
                    'services': {'Players': 'scope:players'}, 'script_sources': {},
                    'initial_replication_complete': False, 'nodes': []}

        with patch.object(persistent_runtime, 'make_world_snapshot', side_effect=snapshot), \
             patch.object(persistent_runtime, 'PersistentLuauHost',
                          return_value=FakeHost()), \
             patch.object(persistent_runtime, 'script_catalog',
                          side_effect=AssertionError('catalog prepared before marker')):
            try:
                runtime._ensure()
                self.assertEqual(calls[0]['include_script_sources'], False)
                self.assertEqual(runtime.script_catalog, {})
            finally:
                runtime.close()

    def test_catalog_is_loaded_when_initial_replication_marker_arrives(self):
        world = fake_world(revision=7, tags=[])
        runtime = ready_runtime(world, budget=30)
        world.tags.append(13)
        try:
            with patch.object(persistent_runtime, 'script_catalog',
                              return_value={'scope:script': {'source': 'return 42'}}) as catalog:
                result = runtime.poll(force=True)
            self.assertTrue(result['ok'])
            catalog.assert_called_once_with(world)
            self.assertEqual([call['op'] for call in runtime.host.calls], ['update'])
            sent = runtime.host.calls[0]['delta']
            self.assertTrue(sent['initial_replication_complete'])
            self.assertEqual(sent['script_sources'], {'scope:script': {'source': 'return 42'}})
        finally:
            runtime.close()

    def test_catalog_refreshes_for_script_code_updates_only(self):
        world = fake_world(revision=8, tags=[13])
        runtime = ready_runtime(world, budget=30)
        runtime.revision = 7
        try:
            delta = {'version': 1, 'delta': True, 'sequence': 8, 'actions': [
                {'sequence': 8, 'kind': 'set', 'id': 'scope:script',
                 'fields': {'Source': 'return 7'}}]}
            with patch.object(persistent_runtime, 'make_world_delta', return_value=delta), \
                 patch.object(persistent_runtime, 'script_catalog',
                              return_value={'scope:script': {'source': 'return 7'}}) as catalog:
                result = runtime.poll(force=True)
            self.assertTrue(result['ok'])
            catalog.assert_called_once_with(world)
            self.assertEqual(runtime.host.calls[0]['delta']['script_sources'],
                             {'scope:script': {'source': 'return 7'}})
        finally:
            runtime.close()


if __name__ == '__main__':
    unittest.main()
