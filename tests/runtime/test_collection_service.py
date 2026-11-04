"""Authored-Luau checks for the bounded local CollectionService surface."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import local_runtime
import persistent_runtime
if os.environ.get('LUAU_TEST_HOST'):
    local_runtime.RUST_HOST = Path(os.environ['LUAU_TEST_HOST'])
    if not local_runtime.RUST_HOST.is_file():
        raise RuntimeError('LUAU_TEST_HOST must point to a completed build')
    persistent_runtime.RUST_HOST = local_runtime.RUST_HOST

BASE = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())


def snapshot_with_collection_service():
    snapshot = copy.deepcopy(BASE)
    snapshot['class_parents']['CollectionService'] = 'Instance'
    snapshot['nodes'].append({
        'id': 'scope:collection-service', 'class_name': 'CollectionService',
        'name': 'CollectionService', 'parent': None, 'properties': {},
    })
    snapshot['services']['CollectionService'] = 'scope:collection-service'
    return snapshot


@unittest.skipUnless(local_runtime.RUST_HOST.is_file(), 'build the persistent Luau host first')
class CollectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.host = persistent_runtime.PersistentLuauHost(snapshot_with_collection_service())
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        job_id = result.get('job_id')
        deadline = time.monotonic() + 3.0
        while not result.get('completed', True) and time.monotonic() < deadline:
            time.sleep(.01)
            polled = self.host.call({'op': 'poll', 'timeout_ms': 50})
            completed = next((job for job in polled.get('completed_jobs', [])
                              if job.get('job_id') == job_id), None)
            if completed is not None:
                result.update(polled)
                result['completed'] = completed.get('completed', True)
                result['ok'] = completed.get('ok', polled.get('ok', True))
                result['values'] = completed.get('values', [])
                if completed.get('error'):
                    result['error'] = completed['error']
            else:
                result.update({key: value for key, value in polled.items()
                               if key not in ('values', 'completed_jobs')})
        if not result.get('completed', True):
            self.fail(f"Luau source job {job_id} did not finish while polling: {result}")
        return result

    def test_instance_and_service_tag_queries_and_idempotence(self):
        result = self.execute('''
local service=game:GetService("CollectionService")
local folder=Instance.new("Folder",workspace)
local part=Instance.new("Part",folder)
part:AddTag("target")
part:AddTag("target")
assert(part:HasTag("target"))
local tags=part:GetTags()
local listed=service:GetTagged("target")
local all=service:GetAllTags()
return #tags,tags[1],#listed,listed[1]==part,#all,all[1]
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [1, 'target', 1, True, 1, 'target'])

    def test_service_and_instance_signatures_share_state_and_record_mutations(self):
        result = self.execute('''
local service=game:GetService("CollectionService")
local part=Instance.new("Part",workspace)
service:AddTag(part,"via-service")
assert(part:HasTag("via-service"))
assert(service:HasTag(part,"via-service"))
local serviceTags=service:GetTags(part)
assert(#serviceTags==1 and serviceTags[1]=="via-service")
part:AddTag("via-instance")
assert(service:HasTag(part,"via-instance"))
service:RemoveTag(part,"via-service")
assert(not part:HasTag("via-service"))
part:RemoveTag("via-instance")
return #part:GetTags(),#service:GetTagged("via-service"),#service:GetAllTags()
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [0, 0, 0])
        self.assertEqual([mutation['kind'] for mutation in result['mutations'] if mutation['kind'] == 'set_tags'],
                         ['set_tags'] * 4)
        self.assertEqual([mutation['tags'] for mutation in result['mutations'] if mutation['kind'] == 'set_tags'],
                         [['via-service'], ['via-instance', 'via-service'], ['via-instance'], []])

    def test_embedded_nul_tag_is_rejected_without_truncation(self):
        result = self.execute('''
local part=Instance.new("Part",workspace)
local ok=pcall(function()part:AddTag("safe\\0hidden")end)
return ok,#part:GetTags()
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, 0])

    def test_cached_signals_fire_for_datamodel_membership_only(self):
        result = self.execute('''
local service=game:GetService("CollectionService")
local added=service:GetInstanceAddedSignal("marked")
local removed=service:GetInstanceRemovedSignal("marked")
assert(added==service:GetInstanceAddedSignal("marked"))
local seen={a=0,r=0}
added:Connect(function(instance) seen.a+=1;seen.last=instance end)
removed:Connect(function(instance) seen.r+=1;seen.last=instance end)
local part=Instance.new("Part")
part:AddTag("marked")
task.wait(.001)
local before=seen.a
part.Parent=workspace
task.wait(.005)
part.Parent=nil
task.wait(.005)
part.Parent=workspace
task.wait(.005)
part:RemoveTag("marked")
task.wait(.005)
return before,seen.a,seen.r,seen.last==part
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [0, 2, 2, True])

    def test_unknown_server_tag_state_is_not_guessed(self):
        result = self.execute('''
local character=workspace:FindFirstChild("FixtureCharacter")
local part=character:FindFirstChild("HumanoidRootPart")
local readOk=pcall(function()return part:GetTags()end)
local writeOk=pcall(function()part:AddTag("unsafe")end)
return readOk,writeOk
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, False])

    def test_local_clone_copies_tag_membership(self):
        result = self.execute('''
local source=Instance.new("Folder",workspace)
source:AddTag("copy-me")
local cloned=source:Clone()
local service=game:GetService("CollectionService")
local detachedCount=#service:GetTagged("copy-me")
cloned.Parent=workspace
return cloned:HasTag("copy-me"),detachedCount,#service:GetTagged("copy-me")
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, 1, 2])


if __name__ == '__main__':
    unittest.main()
