"""Initial replication completion exposed through the actual persistent VM."""
import copy
import unittest

from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust host first')
class GameLoadingTests(unittest.TestCase):
    def host(self, ready=None):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot.pop('initial_replication_complete', None)
        if ready is not None:
            snapshot['initial_replication_complete'] = ready
        host = PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 100})
        self.assertTrue(result['ok'], result)
        return result

    def update(self, host, ready):
        result = host.call({'op': 'update', 'delta': {
            'version': 1, 'delta': True, 'actions': [],
            'initial_replication_complete': ready}, 'timeout_ms': 100})
        self.assertTrue(result['ok'], result)
        return result

    def test_loaded_wait_and_callback_resolve_once_after_initial_marker(self):
        host = self.host(False)
        first = self.execute(host, '''
shared.loadedCount=0
game.Loaded:Connect(function()
    assert(game:IsLoaded())
    shared.loadedCount+=1
end)
assert(not game:IsLoaded())
game.Loaded:Wait()
return game:IsLoaded()
''')
        self.assertEqual(first['state'], 'running')
        response = self.update(host, True)
        completed = next(r for r in response['completed_jobs'] if r['job_id'] == first['job_id'])
        self.assertTrue(completed['ok'], completed)
        self.assertEqual(completed['values'], [True])
        self.update(host, True)
        self.update(host, False)  # A later streaming update cannot unload the game.
        self.assertEqual(self.execute(host, 'return game:IsLoaded(),shared.loadedCount')['values'], [True, 1])

    def test_initially_loaded_snapshot_does_not_replay_loaded_to_new_listener(self):
        host = self.host(True)
        self.execute(host, 'shared.loadedCount=0;game.Loaded:Connect(function()shared.loadedCount+=1 end)')
        self.update(host, True)
        self.assertEqual(self.execute(host, 'return game:IsLoaded(),shared.loadedCount')['values'], [True, 0])

    def test_missing_marker_stays_false_and_scripts_cannot_set_loaded(self):
        host = self.host()
        result = self.execute(host, '''
local writable=pcall(function()game.IsLoaded=true end)
local other=pcall(function()return game.IsLoaded(workspace)end)
return game:IsLoaded(),writable,other
''')
        self.assertEqual(result['values'], [False, False, False])

    def test_client_server_storage_is_empty_and_has_stable_identity(self):
        host = self.host()
        result = self.execute(host, '''
local storage=game:GetService("ServerStorage")
return storage.ClassName,#storage:GetChildren(),storage==game:GetService("ServerStorage"),storage.Parent==game
''')
        self.assertEqual(result['values'], ['ServerStorage', 0, True, True])


if __name__ == '__main__':
    unittest.main()
