import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from persistent_runtime import PersistentClientScriptRuntime, PersistentLuauHost, RUST_HOST


class FakeHost:
    def __init__(self):
        self.commands = []

    def call(self, command):
        self.commands.append(command)
        return {'ok': True, 'values': []}


class FakeController:
    humanoid_id = 'humanoid-1'


class JournalController(FakeController):
    def __init__(self, transitions=(), signals=()):
        self.transitions = list(transitions)
        self.signals = list(signals)
        self.through_sequence = len(self.transitions) + len(self.signals)
        self.acked = []

    def feedback_events(self):
        return {'through_sequence': self.through_sequence,
                'transitions': list(self.transitions),
                'signals': list(self.signals)}

    def ack_feedback(self, through_sequence):
        self.acked.append(through_sequence)
        self.transitions.clear()
        self.signals.clear()


class FakePreview:
    bridge = type('Bridge', (), {'controller': FakeController()})()

    def report(self):
        return {'controller': {'simulated_seconds': 1.0, 'predicted_state': {
            'humanoid_state': {'name': 'Running', 'value': 8},
            'humanoid_state_transitions': [{'name': 'Landed', 'value': 7},
                                           {'name': 'Running', 'value': 8}],
            'humanoid_state_signals': [{'name': 'FreeFalling', 'active': False}],
        }}}


class PhysicsFeedbackTests(unittest.TestCase):
    def test_publishes_accumulated_transitions_from_intervening_physics_ticks(self):
        controller=JournalController(
            transitions=[{'name':'Jumping','value':3},
                         {'name':'Freefall','value':5},
                         {'name':'Landed','value':7},
                         {'name':'Running','value':8}],
            signals=[{'name':'FreeFalling','active':True},
                     {'name':'FreeFalling','active':False}])
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=FakeHost()
        runtime.movement_preview=SimpleNamespace(
            bridge=SimpleNamespace(controller=controller),
            report=lambda:{'controller':{'simulated_seconds':1.,'predicted_state':{
                # Simulate report() observing the previous step while a live
                # physics tick advances before feedback_events() is read.
                'humanoid_state':{'name':'Jumping','value':3},
                # This last-step report is empty because earlier physics ticks
                # have already overwritten their transient records.
                'humanoid_state_transitions':[], 'humanoid_state_signals':[],
                'linear_velocity':[0.,0.,0.]}}})
        runtime.background_budget_ms=20
        runtime._physics_feedback_token=None
        runtime._publish_physics_feedback()
        feedback=runtime.host.commands[-1]['feedback']
        self.assertEqual(feedback['state'], {'name':'Running','value':8})
        self.assertEqual(feedback['transitions'],[
            {'name':'Jumping','value':3}, {'name':'Freefall','value':5},
            {'name':'Landed','value':7}, {'name':'Running','value':8}])
        self.assertEqual(feedback['signals'],[
            {'name':'FreeFalling','active':True},
            {'name':'FreeFalling','active':False}])
        self.assertEqual(controller.acked,[6])

    def test_stale_character_deferral_keeps_journal_events_for_retry(self):
        controller=JournalController(
            transitions=[{'name':'Jumping','value':3}],
            signals=[{'name':'Jumping','active':True}])
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=Mock()
        runtime.host.call.side_effect=[
            {'ok':False,'values':[],
             'error':'physics feedback Humanoid is not the current local character'},
            {'ok':True,'values':[]}]
        runtime.movement_preview=SimpleNamespace(
            bridge=SimpleNamespace(controller=controller),
            report=lambda:{'controller':{'simulated_seconds':1.,'predicted_state':{
                'humanoid_state':{'name':'Jumping','value':3},
                'linear_velocity':[0.,0.,0.]}}})
        runtime.background_budget_ms=20
        runtime._physics_feedback_token=None
        runtime.stats={}
        runtime._publish_physics_feedback()
        self.assertEqual(controller.acked,[])
        self.assertEqual(runtime.host.call.call_args_list[0].args[0]['feedback']['transitions'],
                         [{'name':'Jumping','value':3}])
        runtime._publish_physics_feedback()
        self.assertEqual(controller.acked,[2])
        self.assertEqual(runtime.host.call.call_args_list[1].args[0]['feedback']['transitions'],
                         [{'name':'Jumping','value':3}])

    def test_speed_changes_publish_without_a_humanoid_state_change(self):
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=FakeHost()
        predicted={'humanoid_state':{'name':'Running','value':8},
                   'linear_velocity':[3.,2.,4.]}
        runtime.movement_preview=SimpleNamespace(
            bridge=SimpleNamespace(controller=FakeController()),
            report=lambda:{'controller':{'simulated_seconds':1.,'predicted_state':predicted}})
        runtime.background_budget_ms=20
        runtime._physics_feedback_token=None
        runtime._publish_physics_feedback()
        self.assertEqual(runtime.host.commands[-1]['feedback']['running_speed'],5.)
        predicted['linear_velocity']=[0.,0.,0.]
        runtime._publish_physics_feedback()
        self.assertEqual(runtime.host.commands[-1]['feedback']['running_speed'],0.)
        self.assertEqual(len(runtime.host.commands),2)
        runtime._publish_physics_feedback()
        self.assertEqual(len(runtime.host.commands),2)

    @unittest.skipUnless(RUST_HOST.is_file(), 'build the persistent worker first')
    def test_native_feedback_recovers_when_humanoid_arrives_in_a_later_chunk(self):
        snapshot=json.loads((Path(__file__).resolve().parents[2] /
                             'tests/fixtures/world_snapshot.json').read_text())
        humanoid=next(node for node in snapshot['nodes'] if node['class_name']=='Humanoid')
        snapshot['nodes'].remove(humanoid)
        host=PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=host
        runtime.movement_preview=SimpleNamespace(
            bridge=SimpleNamespace(controller=SimpleNamespace(humanoid_id=humanoid['id'])),
            report=FakePreview().report)
        runtime.background_budget_ms=50
        runtime._physics_feedback_token=None
        runtime.stats={}
        runtime._publish_physics_feedback()
        self.assertIsNone(runtime._physics_feedback_token)
        self.assertEqual(runtime.stats['physics_feedback_deferred'],1)
        received=host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'actions':[{
                'sequence':snapshot['sequence']+1,'kind':'create','node':humanoid}]}})
        self.assertTrue(received['ok'],received.get('error'))
        runtime._publish_physics_feedback()
        self.assertIsNotNone(runtime._physics_feedback_token)
        result=host.call({'op':'execute','timeout_ms':100,'source': '''
local h=game:GetService("Players").LocalPlayer.Character:FindFirstChildOfClass("Humanoid")
return h:GetState()==Enum.HumanoidStateType.Running
'''})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertEqual(result['values'],[True])

    def test_character_receipt_ahead_of_vm_defers_without_acknowledging_then_retries(self):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host = Mock()
        runtime.host.call.side_effect = [
            {'ok':False,'values':[],
             'error':'physics feedback Humanoid is not the current local character'},
            {'ok':True,'values':[]}]
        runtime.movement_preview = FakePreview()
        runtime.background_budget_ms = 20
        runtime._physics_feedback_token = None
        runtime.stats = {}
        runtime._publish_physics_feedback()
        self.assertIsNone(runtime._physics_feedback_token)
        self.assertEqual(runtime.stats['physics_feedback_deferred'],1)
        runtime._publish_physics_feedback()
        self.assertIsNotNone(runtime._physics_feedback_token)
        self.assertEqual(runtime.host.call.call_count,2)
        runtime._publish_physics_feedback()
        self.assertEqual(runtime.host.call.call_count,2)

    def test_publishes_local_prediction_and_deduplicates(self):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host = FakeHost()
        runtime.movement_preview = FakePreview()
        runtime.background_budget_ms = 20
        runtime._physics_feedback_token = None
        runtime._publish_physics_feedback()
        runtime._publish_physics_feedback()
        self.assertEqual(len(runtime.host.commands), 1)
        command = runtime.host.commands[0]
        self.assertEqual(command['op'], 'physics_feedback')
        self.assertEqual(command['feedback'], {
            'humanoid_id': 'humanoid-1',
            'state': {'name': 'Running', 'value': 8},
            'transitions': [{'name': 'Landed', 'value': 7},
                            {'name': 'Running', 'value': 8}],
            'signals': [{'name': 'FreeFalling', 'active': False}],
        })

    def test_maps_freefall_signal_name(self):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host = FakeHost()
        runtime.movement_preview = FakePreview()
        runtime.background_budget_ms = 20
        runtime._physics_feedback_token = None
        runtime.movement_preview.report = lambda: {'controller': {
            'simulated_seconds': 2.0, 'predicted_state': {
                'humanoid_state': {'name': 'Freefall', 'value': 5},
                'humanoid_state_transitions': [{'name': 'Freefall', 'value': 5}],
                'humanoid_state_signals': [{'name': 'FreeFalling', 'active': True}],
            }}}
        runtime._publish_physics_feedback()
        self.assertEqual(runtime.host.commands[0]['feedback']['signals'], [
            {'name': 'FreeFalling', 'active': True}])

    def test_unresolved_prediction_clears_previous_state_feedback(self):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host = FakeHost()
        runtime.movement_preview = FakePreview()
        runtime.background_budget_ms = 20
        runtime._physics_feedback_token = ('humanoid-1', 'Running', 8, 1., (), ())
        runtime.movement_preview.report = lambda: {'controller': {
            'simulated_seconds': 1.1, 'predicted_state': None}}
        runtime._publish_physics_feedback()
        self.assertEqual(runtime.host.commands[0]['feedback'], {
            'humanoid_id': 'humanoid-1', 'state': None,
            'transitions': [], 'signals': [],
        })


if __name__ == '__main__':
    unittest.main()
