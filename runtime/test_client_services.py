"""Offline Luau integration tests for local Debris and Players queries."""
import copy
import json
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

SNAPSHOT = json.loads((ROOT / 'runtime/tests/world_snapshot.json').read_text())
DEBRIS_ID = 'scope:debris-fixture'


def with_debris_service():
    snapshot = copy.deepcopy(SNAPSHOT)
    snapshot['class_parents']['Debris'] = 'Instance'
    snapshot['nodes'].append({
        'id': DEBRIS_ID, 'class_name': 'Debris', 'name': 'Debris',
        'parent': None, 'properties': {},
    })
    snapshot['services']['Debris'] = DEBRIS_ID
    return snapshot


def with_negative_id_player():
    snapshot = with_debris_service()
    snapshot['nodes'].extend([
        {
            'id': 'scope:character-negative', 'class_name': 'Model',
            'name': 'NegativeIdCharacter', 'parent': 'scope:workspace',
            'properties': {},
        },
        {
            'id': 'scope:player-negative', 'class_name': 'Player',
            'name': 'NegativeIdPlayer', 'parent': 'scope:players',
            'properties': {
                'UserId': -42,
                'Character': {'$ref': 'scope:character-negative'},
            },
        },
    ])
    return snapshot


@unittest.skipUnless(local_runtime.RUST_HOST.is_file(), 'build the persistent Luau host first')
class ClientServiceTests(unittest.TestCase):
    def setUp(self):
        self.host = persistent_runtime.PersistentLuauHost(with_debris_service())
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

    def test_stats_service_is_local_and_preserves_identity(self):
        result = self.execute('''
local before=game:FindService("Stats")
local stats=game:GetService("Stats")
local hasUnmeasuredValue=pcall(function()return stats.DataReceiveKbps end)
return before==nil,stats.ClassName,stats.Parent==game,
       stats==game:GetService("Stats"),stats==game:FindService("Stats"),
       hasUnmeasuredValue
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, 'Stats', True, True, True, False])

    def test_players_queries_return_replicated_service_members_and_identity(self):
        result = self.execute('''
local players = game:GetService("Players")
local current = players.LocalPlayer
local character = current.Character
local listed = players:GetPlayers()
local legacy = players:getPlayers()
return #listed, listed[1] == current, #legacy, legacy[1] == current,
       players:GetPlayerByUserId(current.UserId) == current,
       players:GetPlayerByUserId(987654321) == nil,
       players:GetPlayerFromCharacter(character) == current,
       players:GetPlayerFromCharacter(workspace) == nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [1, True, 1, True, True, True, True, True])
        self.assertEqual(result['mutations'], [])

    def test_players_queries_support_negative_studio_user_ids(self):
        host = persistent_runtime.PersistentLuauHost(with_negative_id_player())
        self.addCleanup(host.close)
        result = host.call({'op': 'execute', 'source': '''
local players = game:GetService("Players")
local found = players:GetPlayerByUserId(-42)
return #players:GetPlayers(), found and found.Name,
       players:GetPlayerFromCharacter(found.Character) == found,
       players:GetPlayerByUserId(-43) == nil
''', 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [2, 'NegativeIdPlayer', True, True])

    def test_players_queries_follow_current_parent_and_character_references(self):
        result = self.execute('''
local players = game:GetService("Players")
local current = players.LocalPlayer
local character = current.Character
local before = #players:GetPlayers()
local by_id = players:GetPlayerByUserId(current.UserId)
current.Character = nil
return before, by_id == current, players:GetPlayerFromCharacter(character) == nil,
       players:GetPlayerFromCharacter(workspace.FixtureCharacter) == nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [1, True, True, True])

    def test_debris_add_item_is_non_yielding_uses_default_and_destroys_normally(self):
        result = self.execute('''
local debris = game:GetService("Debris")
local deferred = Instance.new("Part")
debris:AddItem(deferred)
local default_did_not_yield = deferred.Parent == nil
local part = Instance.new("Part")
part.Parent = workspace
local destroying = false
part.Destroying:Connect(function() destroying = true end)
local returned = debris:AddItem(part, 0.015)
local stayed = part.Parent == workspace

task.wait(0.06)
return returned == nil, default_did_not_yield, stayed, part.Parent == nil, destroying
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True, True, True])
        self.assertEqual(result['mutations'][-1]['kind'], 'destroy')

    def test_services_interoperate_across_tags_tweens_players_and_debris(self):
        result = self.execute('''
local players = game:GetService("Players")
local localPlayer = players.LocalPlayer
local collection = game:GetService("CollectionService")
local debris = game:GetService("Debris")
local tweenService = game:GetService("TweenService")
local removedCount = 0
local removedInstance
collection:GetInstanceRemovedSignal("cleanup-target"):Connect(function(instance)
    removedCount += 1
    removedInstance = instance
end)
local part = Instance.new("Part")
part.Parent = workspace
part:AddTag("cleanup-target")
assert(collection:GetTagged("cleanup-target")[1] == part)
local info = TweenInfo.new(2.5, Enum.EasingStyle.Quad, Enum.EasingDirection.Out, 3, true, .25)
assert(info.Time == 2.5 and info.RepeatCount == 3 and info.Reverses)
assert(info.EasingStyle == Enum.EasingStyle.Quad and info.EasingDirection == Enum.EasingDirection.Out)
local eased = tweenService:GetValue(.25, info.EasingStyle, info.EasingDirection)
assert(math.abs(eased - .4375) < 1e-6)
debris:AddItem(part, .03)
task.wait(.09)
return players:GetPlayerByUserId(localPlayer.UserId) == localPlayer,
       players:GetPlayerFromCharacter(localPlayer.Character) == localPlayer,
       part.Parent == nil, #collection:GetTagged("cleanup-target"),
       removedCount, removedInstance == part, eased
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True, 0, 1, True, 0.4375])
        self.assertEqual(result['mutations'][-1]['kind'], 'destroy')

    def test_debris_queue_survives_the_source_job_that_scheduled_it(self):
        first = self.execute('''
item = Instance.new("Part")
item.Parent = workspace
game:GetService("Debris"):AddItem(item, 0.03)
return item.Parent == workspace
''')
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['values'], [True])
        second = self.execute('task.wait(0.09); return item.Parent == nil')
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [True])

    def test_debris_cleanup_survives_task_cancellation(self):
        result = self.execute('''
local item = Instance.new("Part")
item.Parent = workspace
game:GetService("Debris"):AddItem(item, 0.03)
local unrelated = task.spawn(function() task.wait(10) end)
task.cancel(unrelated)
task.wait(0.09)
return item.Parent == nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True])

    def test_debris_cap_destroys_oldest_queued_item(self):
        result = self.execute('''
local debris = game:GetService("Debris")
local item = Instance.new("Folder")
item.Parent = workspace
for _ = 1, 1001 do debris:AddItem(item, 1000) end
return item.Parent == nil
''', timeout_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True])

    def test_invalid_debris_lifetimes_fail_without_an_unbounded_timer(self):
        result = self.execute('''
local debris = game:GetService("Debris")
local item = Instance.new("Folder")
local infinite = pcall(function() debris:AddItem(item, math.huge) end)
local not_number = pcall(function() debris:AddItem(item, "soon") end)
return infinite, not_number, item.Parent == nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, False, True])


if __name__ == '__main__':
    unittest.main()
