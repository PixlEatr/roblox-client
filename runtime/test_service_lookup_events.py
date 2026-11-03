"""Offline Luau coverage for FindService and Players membership events."""
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import local_runtime
import persistent_runtime

if os.environ.get('LUAU_TEST_HOST'):
    local_runtime.RUST_HOST = Path(os.environ['LUAU_TEST_HOST'])
    if not local_runtime.RUST_HOST.is_file():
        raise RuntimeError('LUAU_TEST_HOST must point to a completed build')
    persistent_runtime.RUST_HOST = local_runtime.RUST_HOST

SNAPSHOT = ROOT / 'runtime/tests/world_snapshot.json'


@unittest.skipUnless(local_runtime.RUST_HOST.is_file(), 'build the persistent Luau host first')
class ServiceLookupEventsTests(unittest.TestCase):
    def setUp(self):
        import json
        self.host = persistent_runtime.PersistentLuauHost(json.loads(SNAPSHOT.read_text()))
        self.addCleanup(self.host.close)

    def execute(self, source, timeout_ms=1000):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': timeout_ms})
        mutations = list(result.get('mutations', []))
        if result.get('completed'):
            result['mutations'] = mutations
            return result
        job_id = result.get('job_id')
        if type(job_id) is not int:
            result['mutations'] = mutations
            return result
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            poll = self.host.call({'op': 'poll', 'timeout_ms': 100})
            mutations.extend(poll.get('mutations', []))
            for completed in poll.get('completed_jobs', []):
                if completed.get('job_id') == job_id:
                    mutations.extend(completed.get('mutations', []))
                    completed['mutations'] = mutations
                    return completed
        result['mutations'] = mutations
        return result

    def test_find_service_returns_only_existing_services_without_mutation(self):
        result = self.execute('''
local players = game:FindService("Players")
local workspaceService = game:FindService("Workspace")
local missing = game:FindService("HttpService")
return players == game:GetService("Players"),
       workspaceService == workspace,
       missing == nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True])
        self.assertEqual(result['mutations'], [])

    def test_player_membership_events_fire_once_for_direct_parent_transitions(self):
        result = self.execute('''
local players = game:GetService("Players")
local player = players.LocalPlayer
local added, removed = {}, {}
local addedSignal = players.PlayerAdded
local sameSignal = players.PlayerAdded
assert(addedSignal == sameSignal)
addedSignal:Connect(function(value) table.insert(added, value) end)
players.PlayerRemoving:Connect(function(value) table.insert(removed, value) end)
local oldParent = player.Parent
player.Parent = oldParent
player.Parent = workspace
player.Parent = players
task.wait(0.01)
local afterTransitions = #added == 1 and added[1] == player and
                         #removed == 1 and removed[1] == player
player:Destroy()
task.wait(0.01)
return afterTransitions, #added, #removed,
       added[1] == player, removed[1] == player
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, 1, 2, True, True])

    def test_non_player_child_and_unobserved_service_do_not_emit_player_events(self):
        result = self.execute('''
local players = game:GetService("Players")
local calls = 0
players.PlayerAdded:Connect(function() calls += 1 end)
players.PlayerRemoving:Connect(function() calls += 1 end)
local child = Instance.new("Folder")
child.Parent = players
child.Parent = workspace
task.wait(0.01)
return child.Parent == workspace, calls
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, 0])


if __name__ == '__main__':
    unittest.main()
