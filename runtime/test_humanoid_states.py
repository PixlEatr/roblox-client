"""Local Humanoid state API integration tests; no live client is used."""
import json
import os
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import local_runtime
HOST = Path(os.environ.get('ROBLOX_LUAU_HOST', local_runtime.HOST))
RUST_HOST = Path(os.environ.get('ROBLOX_LUAU_RUST_HOST', local_runtime.RUST_HOST))
local_runtime.HOST = HOST
local_runtime.RUST_HOST = RUST_HOST
from local_runtime import run_source
import persistent_runtime
persistent_runtime.RUST_HOST = RUST_HOST
from persistent_runtime import PersistentLuauHost

RUNTIME = Path(__file__).resolve().parent
SNAPSHOT = json.loads((RUNTIME / 'tests/world_snapshot.json').read_text())


@unittest.skipUnless(HOST.is_file(), 'build the local Luau host first')
class HumanoidStateRuntimeTests(unittest.TestCase):
    def test_change_state_queues_request_and_getstate_does_not_guess(self):
        result = run_source('''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
local before=pcall(function() return h:GetState() end)
h:ChangeState(Enum.HumanoidStateType.Jumping)
return before,h:GetStateEnabled(Enum.HumanoidStateType.Jumping),#Enum.HumanoidStateType:GetEnumItems(),Enum.HumanoidStateType.None.Value
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, True, 17, 18])
        requests = [item for item in result['movement_intents'] if item['kind'] == 'ChangeState']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['state'], 'Jumping')
        self.assertEqual(requests[0]['state_value'], 3)

    def test_enum_excludes_unknown_placeholder_values(self):
        result = run_source('''
local e=Enum.HumanoidStateType
return e.Jumping.Value,e.Swimming.Value,e.Freefall.Value,e.Climbing.Value,e.Seated.Value,e.None.Value
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [3, 4, 5, 12, 13, 18])

    def test_disabled_state_does_not_enqueue(self):
        result = run_source('''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
h:SetStateEnabled(Enum.HumanoidStateType.Swimming,false)
h:ChangeState(Enum.HumanoidStateType.Swimming)
return h:GetStateEnabled(Enum.HumanoidStateType.Swimming)
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False])
        self.assertFalse(any(item['kind'] == 'ChangeState' for item in result['movement_intents']))

    def test_disabling_jumping_blocks_jump_property_intent(self):
        result = run_source('''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
h:SetStateEnabled(Enum.HumanoidStateType.Jumping,false)
h.Jump=true
local before_boundary=h.Jump
task.wait()
return before_boundary,h.Jump,h:GetStateEnabled(Enum.HumanoidStateType.Jumping)
''', world_snapshot=SNAPSHOT)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, False, False])
        self.assertEqual([item['kind'] for item in result['movement_intents']], ['SetStateEnabled'])
        self.assertFalse(result['movement_intents'][0]['enabled'])


@unittest.skipUnless(RUST_HOST.is_file(), 'build the persistent Rust linked worker first')
class PersistentHumanoidFeedbackTests(unittest.TestCase):
    def test_controller_feedback_is_a_typed_enum_visible_to_luau(self):
        with PersistentLuauHost(SNAPSHOT) as host:
            feedback = host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                                  'feedback': {
                                      'humanoid_id': 'scope:humanoid-1',
                                      'state': {'name': 'Running', 'value': 8},
                                      'transitions': [], 'signals': []}})
            self.assertTrue(feedback['ok'], feedback)
            result = host.call({'op': 'execute', 'timeout_ms': 200,
                                'source': '''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
return h:GetState()==Enum.HumanoidStateType.Running,h:GetState().Name
'''})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['values'], [True, 'Running'])

    def test_state_changed_uses_enum_items_and_jump_signal_uses_bool(self):
        with PersistentLuauHost(SNAPSHOT) as host:
            host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                       'feedback': {'humanoid_id': 'scope:humanoid-1',
                                    'state': {'name': 'Running', 'value': 8},
                                    'transitions': [], 'signals': []}})
            connected = host.call({'op': 'execute', 'timeout_ms': 200, 'source': '''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
h.StateChanged:Connect(function(old,new)
    print(old==Enum.HumanoidStateType.Running,new==Enum.HumanoidStateType.Jumping)
end)
h.Jumping:Connect(function(active) print(active==true) end)
return true
'''})
            self.assertTrue(connected['ok'], connected)
            host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                       'feedback': {'humanoid_id': 'scope:humanoid-1',
                                    'state': {'name': 'Jumping', 'value': 3},
                                    'transitions': [{'name': 'Jumping', 'value': 3}],
                                    'signals': [{'name': 'Jumping', 'active': True}]}})
            result = host.call({'op': 'poll', 'timeout_ms': 200})
            self.assertTrue(result['ok'], result)
            self.assertIn('true\ttrue', result['output'])
            self.assertIn('true', result['output'])

    def test_running_event_reports_changed_speed_once_and_includes_stop(self):
        with PersistentLuauHost(SNAPSHOT) as host:
            connected = host.call({'op': 'execute', 'timeout_ms': 200, 'source': '''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
getgenv().reported_speeds={}
h.Running:Connect(function(speed) table.insert(reported_speeds,speed) end)
return true
'''})
            self.assertTrue(connected['ok'], connected)
            for speed in (7.25, 8.5, 8.5, 0):
                feedback = host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                                      'feedback': {'humanoid_id': 'scope:humanoid-1',
                                                   'state': {'name': 'Running', 'value': 8},
                                                   'transitions': [], 'signals': [],
                                                   'running_speed': speed}})
                self.assertTrue(feedback['ok'], feedback)
            poll = host.call({'op': 'poll', 'timeout_ms': 200})
            self.assertTrue(poll['ok'], poll)
            result = host.call({'op': 'execute', 'timeout_ms': 200, 'source': '''
return #reported_speeds,reported_speeds[1],reported_speeds[2],reported_speeds[3]
'''})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['values'], [3, 7.25, 8.5, 0])

    def test_running_feedback_requires_finite_nonnegative_speed_and_owned_humanoid(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['nodes'] += [
            {'id': 'scope:other-character', 'class_name': 'Model', 'name': 'OtherCharacter',
             'parent': 'scope:workspace', 'properties': {}},
            {'id': 'scope:other-humanoid', 'class_name': 'Humanoid', 'name': 'Humanoid',
             'parent': 'scope:other-character', 'properties': {'Health': 100}},
        ]
        with PersistentLuauHost(snapshot) as host:
            connected = host.call({'op': 'execute', 'timeout_ms': 200, 'source': '''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
getgenv().reported_speeds={}
h.Running:Connect(function(speed) table.insert(reported_speeds,speed) end)
return true
'''})
            self.assertTrue(connected['ok'], connected)
            for invalid in ('fast', -1):
                result = host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                                    'feedback': {'humanoid_id': 'scope:humanoid-1',
                                                 'running_speed': invalid}})
                self.assertFalse(result['ok'], result)
            with self.assertRaises(ValueError):
                host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                           'feedback': {'humanoid_id': 'scope:humanoid-1',
                                        'running_speed': float('nan')}})
            foreign = host.call({'op': 'physics_feedback', 'timeout_ms': 50,
                                 'feedback': {'humanoid_id': 'scope:other-humanoid',
                                              'running_speed': 12}})
            self.assertFalse(foreign['ok'], foreign)
            poll = host.call({'op': 'poll', 'timeout_ms': 200})
            self.assertTrue(poll['ok'], poll)
            observed = host.call({'op': 'execute', 'timeout_ms': 200, 'source': '''
return #reported_speeds
'''})
            self.assertTrue(observed['ok'], observed)
            self.assertEqual(observed['values'], [0])


if __name__ == '__main__':
    unittest.main()
