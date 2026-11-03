"""Authored animation timelines exercised by real persistent Luau bytecode."""
import copy
import time
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT

REFERENCE = 'rbxassetid://123456'
CLIP = {'version': 1, 'kind': 'KeyframeSequence', 'length': .4,
        'looped': False, 'priority': 1000, 'keyframes': [
            {'time': 0, 'name': 'Start', 'markers': []},
            {'time': .04, 'name': 'Footstep', 'markers': [{'name': 'Step', 'value': 'left'}]},
            {'time': .4, 'name': 'End', 'markers': []}]}


class AnimationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 50})
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result.get('task_errors', []), [])
        return result

    def setup_track(self):
        return self.execute('''
controller=Instance.new("AnimationController",workspace)
animator=Instance.new("Animator",controller)
animation=Instance.new("Animation");animation.Name="TestClip"
animation.AnimationId="rbxassetid://123456"
track=animator:LoadAnimation(animation)
return track.Length,track.IsPlaying,#animator:GetPlayingAnimationTracks()
''')

    def setup_local_character_track(self):
        return self.execute('''
localCharacter=game:GetService("Players").LocalPlayer.Character
localAnimator=Instance.new("Animator",localCharacter.Humanoid)
localAnimation=Instance.new("Animation");localAnimation.AnimationId=REFERENCE or "rbxassetid://123456"
localTrack=localAnimator:LoadAnimation(localAnimation)
return localTrack~=nil
''')

    def complete(self, clip=None, error=None):
        response = {'reference': REFERENCE}
        response.update({'error': error} if error else {'clip': copy.deepcopy(clip or CLIP)})
        return self.host.call({'op': 'update', 'delta': {
            'version': 1, 'delta': True, 'actions': [], 'animation_responses': [response]},
            'timeout_ms': 50})

    def test_pending_real_metadata_and_distinct_canonical_tracks(self):
        result = self.setup_track()
        self.assertEqual(result['values'], [0, False, 0])
        self.assertEqual(result['animation_requests'], [{'reference': REFERENCE}])
        self.assertTrue(self.complete()['ok'])
        result = self.execute('''
other=animator:LoadAnimation(animation)
return track.Length,track.Animation==animation,track.Name,
    other~=track,other.Length,animator:GetTrackByAnimationId(animation.AnimationId)==track,
    animator:GetTrackByAnimationId("rbxassetid://999")==nil,
    track.Priority==Enum.AnimationPriority.Core,track.Priority.Value,
    #Enum.AnimationPriority:GetEnumItems(),typeof(track.Priority)
''')
        self.assertEqual(result['values'], [.4, True, 'TestClip', True, .4, True, True, True, 1000, 7, 'EnumItem'])
        self.assertEqual(result['animation_requests'], [])

    def test_play_pause_seek_speed_weight_and_animator_event(self):
        self.setup_track(); self.complete()
        self.execute('''
played=nil;animator.AnimationPlayed:Connect(function(t)played=t end)
track:Play(0,.7,0);track.TimePosition=.2
''')
        result = self.execute('''
return played==track,track.IsPlaying,track.Speed,track.TimePosition,
    track.WeightCurrent,track.WeightTarget,animator:GetPlayingAnimationTracks()[1]==track
''')
        self.assertEqual(result['values'], [True, True, 0, .2, .7, .7, True])
        self.execute('track:AdjustWeight(.4,0);track:AdjustSpeed(1)')
        time.sleep(.03)
        values = self.execute('track:AdjustSpeed(0);return track.TimePosition,track.WeightCurrent')['values']
        self.assertGreater(values[0], .22)
        self.assertLess(values[0], .4)
        self.assertAlmostEqual(values[1], .4)

    def test_stopped_track_remains_active_until_fade_ends(self):
        self.setup_track(); self.complete()
        result = self.execute('''
stopped=0;ended=0
track.Stopped:Connect(function()stopped+=1 end)
track.Ended:Connect(function()ended+=1 end)
track:Play(0,1,0);track:Stop(.15)
return track.IsPlaying,#animator:GetPlayingAnimationTracks(),track.WeightTarget
''')
        self.assertEqual(result['values'], [False, 1, 0])
        self.assertEqual(self.execute('return stopped,ended')['values'], [1, 0])
        time.sleep(.18)
        self.host.call({'op': 'poll', 'timeout_ms': 50})
        self.assertEqual(self.execute('return stopped,ended,#animator:GetPlayingAnimationTracks(),track.WeightCurrent')['values'], [1, 1, 0, 0])

    def test_keyframe_marker_events_and_natural_completion(self):
        self.setup_track(); self.complete()
        self.execute('''
names={};markers={};stopped=0;ended=0
track.KeyframeReached:Connect(function(name)table.insert(names,name)end)
track:GetMarkerReachedSignal("Step"):Connect(function(value)table.insert(markers,value)end)
track.Stopped:Connect(function()stopped+=1 end)
track.Ended:Connect(function()ended+=1 end)
track:Play(0)
''')
        time.sleep(.45)
        self.host.call({'op': 'poll', 'timeout_ms': 50})
        self.assertEqual(self.execute('return table.concat(names,","),table.concat(markers,","),stopped,ended,track.IsPlaying')['values'],
                         ['Start,Footstep,End', 'left', 1, 1, False])

    def test_loop_forward_reverse_and_no_marker_on_seek(self):
        self.setup_track()
        clip=copy.deepcopy(CLIP);clip['length']=4;clip['keyframes'][-1]['time']=4
        self.complete(clip)
        self.execute('''
loops=0;steps=0;track.Looped=true
track.DidLoop:Connect(function()loops+=1 end)
track:GetMarkerReachedSignal("Step"):Connect(function()steps+=1 end)
track:Play(0,1,0);track.TimePosition=3.99;track:AdjustSpeed(1)
''')
        time.sleep(.08)
        self.host.call({'op': 'poll', 'timeout_ms': 50})
        values = self.execute('track:AdjustSpeed(0);return loops,steps,track.IsPlaying')['values']
        self.assertEqual(values, [1, 1, True])
        self.execute('track.TimePosition=.02;track:AdjustSpeed(-1)')
        time.sleep(.05)
        self.host.call({'op': 'poll', 'timeout_ms': 50})
        values = self.execute('track:AdjustSpeed(0);return loops,steps,track.TimePosition')['values']
        self.assertEqual(values[:2], [2, 1])
        self.assertGreater(values[2], 0)
        self.assertLess(values[2], 4)

    def test_asset_failure_stops_pending_track_and_rejects_replay(self):
        self.setup_track()
        self.execute('track:Play()')
        self.assertTrue(self.complete(error='animation_asset_unavailable')['ok'])
        values = self.execute('return track.Length,track.IsPlaying,#animator:GetPlayingAnimationTracks(),pcall(function()track:Play()end)')['values']
        self.assertEqual(values[:4], [0, False, 0, False])

    def test_overrides_survive_asset_completion_and_keyframe_lookup(self):
        self.setup_track()
        self.execute('track.Looped=true;track.Priority=Enum.AnimationPriority.Action4')
        self.complete()
        values = self.execute('return track.Looped,track.Priority.Value,track:GetTimeOfKeyframe("Footstep"),pcall(function()track:GetTimeOfKeyframe("Missing")end)')['values']
        self.assertEqual(values[:4], [True, 5, .04, False])

    def test_proxy_animator_and_destroy_cleanup(self):
        result = self.execute('''
controller=Instance.new("AnimationController",workspace)
animation=Instance.new("Animation");animation.AnimationId="rbxassetid://123456"
track=controller:LoadAnimation(animation)
animator=controller:FindFirstChildOfClass("Animator")
track:Play(0,1,0)
local before=controller:GetPlayingAnimationTracks()[1]==track
track:Destroy()
return animator~=nil,before,#controller:GetPlayingAnimationTracks()
''')
        self.assertEqual(result['values'], [True, True, 0])

    def test_received_content_alias_is_readable_and_can_be_overridden(self):
        self.setup_track()
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':100,'kind':'create','node':{'id':'test:received-animation',
             'class_name':'Animation','name':'ReceivedAnimation','parent':'scope:workspace',
             'properties':{'AnimationContent':REFERENCE},'origins':{'AnimationContent':'received'}}}
        ]},'timeout_ms':50})
        self.assertTrue(result['ok'])
        result=self.execute('''
local received=workspace:FindFirstChild("ReceivedAnimation")
local original=received.AnimationId
received.AnimationId="rbxassetid://456"
local loaded=animator:LoadAnimation(received)
return original,received.AnimationId,loaded.Animation==received
''')
        self.assertEqual(result['values'],[REFERENCE,'rbxassetid://456',True])
        self.assertEqual(result['animation_requests'],[{'reference':'rbxassetid://456'}])

    def test_destroyed_animator_stops_tracks_and_prevents_restart(self):
        self.setup_track();self.complete()
        values=self.execute('''
track:Play(0,1,0);animator:Destroy()
return track.IsPlaying,pcall(function()track:Play()end)
''')['values']
        self.assertEqual(values[:2],[False,False])

    def test_destroyed_tracks_release_registry_capacity(self):
        self.setup_track()
        # Keep all 256 tracks alive simultaneously, but spread the work across
        # requests so this registry test does not depend on completing 512
        # native create/destroy operations inside one 50 ms script step.
        self.execute('capacityTracks={track}')
        for start in range(0,255,32):
            self.execute('for i=1,%d do capacityTracks[#capacityTracks+1]=animator:LoadAnimation(animation) end'
                         % min(32,255-start))
        self.assertEqual(self.execute('return pcall(function()animator:LoadAnimation(animation)end)')['values'][0],False)
        for start in range(1,257,32):
            self.execute('for i=%d,%d do capacityTracks[i]:Destroy() end' % (start,min(start+31,256)))
        values=self.execute('''
replacement=animator:LoadAnimation(animation)
return replacement~=track,
    animator:GetTrackByAnimationId(animation.AnimationId)==replacement,
    #animator:GetPlayingAnimationTracks()
''')['values']
        self.assertEqual(values,[True,True,0])

    def test_owned_character_track_intents_are_ordered_and_drained_once(self):
        self.setup_local_character_track()
        played=self.execute('localTrack.Looped=true;localTrack.Priority=Enum.AnimationPriority.Action2;localTrack:Play(.2,.6,1.5)')
        intents=played['animation_intents']
        self.assertEqual([item['kind'] for item in intents],['Looped','Priority','Play'])
        self.assertEqual([item['sequence'] for item in intents],sorted(item['sequence'] for item in intents))
        for item in intents:
            self.assertEqual(item['animator_id'],intents[0]['animator_id'])
            self.assertEqual(item['track_id'],intents[0]['track_id'])
            self.assertEqual(item['animation_reference'],REFERENCE)
        play=intents[-1]
        self.assertEqual(play['parameters'],{
            'fade_time':.2,'weight':.6,'speed':1.5,
            'effective_state':{'playing':True,'priority':3,'looped':True,
                               'time_position':0.,'speed':1.5,
                               'weight_current':0.,'weight_target':.6}})
        adjusted=self.execute('localTrack:AdjustSpeed(2);localTrack:AdjustWeight(.4,0);localTrack.TimePosition=.1')
        self.assertEqual([item['kind'] for item in adjusted['animation_intents']],
                         ['AdjustSpeed','AdjustWeight','TimePosition'])
        self.assertEqual(self.execute('return localTrack.IsPlaying')['animation_intents'],[])
        stopped=self.execute('localTrack:Stop(.3)')
        self.assertEqual([item['kind'] for item in stopped['animation_intents']],['Stop'])
        self.assertFalse(stopped['animation_intents'][0]['parameters']['effective_state']['playing'])

    def test_destroying_playing_owned_track_emits_one_final_stop(self):
        self.setup_local_character_track()
        play=self.execute('localTrack:Play(0,1,1)')
        result=self.execute('localTrack:Destroy()')
        self.assertEqual([item['kind'] for item in result['animation_intents']],['Stop'])
        self.assertEqual(result['animation_intents'][0]['track_id'],play['animation_intents'][0]['track_id'])
        self.assertFalse(result['animation_intents'][0]['parameters']['effective_state']['playing'])
        self.assertEqual(self.execute('localTrack:Destroy()')['animation_intents'],[])

    def test_destroying_owned_animator_emits_stop_before_invalidation(self):
        self.setup_local_character_track()
        play=self.execute('localTrack:Play(0,1,1)')
        result=self.execute('localAnimator:Destroy()')
        self.assertEqual([item['kind'] for item in result['animation_intents']],['Stop'])
        self.assertEqual(result['animation_intents'][0]['track_id'],play['animation_intents'][0]['track_id'])
        self.assertFalse(result['animation_intents'][0]['parameters']['effective_state']['playing'])

    def test_natural_completion_exports_one_stop_and_no_replay(self):
        self.setup_local_character_track();self.complete()
        self.execute('localTrack:Play(0,1,1)')
        time.sleep(.45)
        result=self.execute('return localTrack.IsPlaying')
        self.assertEqual(result['values'],[False])
        self.assertEqual([item['kind'] for item in result['animation_intents']],['Stop'])
        self.assertEqual(self.execute('return localTrack.IsPlaying')['animation_intents'],[])

    def test_non_character_animation_tracks_do_not_export_owned_intents(self):
        self.setup_track()
        result=self.execute('track:Play();track:AdjustSpeed(2);track:Stop()')
        self.assertEqual(result['animation_intents'],[])

    def test_animation_intent_overflow_rejects_mutation_before_changing_state(self):
        self.setup_local_character_track()
        result=self.host.call({'op':'execute','source':'''for i=1,1025 do localTrack:AdjustSpeed(i) end''',
                               'timeout_ms':1000})
        self.assertFalse(result['ok'])
        self.assertIn('animation intent limit exceeded',result['error'])
        self.assertEqual(len(result['animation_intents']),1024)
        self.assertEqual(self.execute('return localTrack.Speed')['values'],[1024])

    def test_invalid_receivers_readonly_fields_and_finite_arguments(self):
        self.setup_track(); self.complete()
        values = self.execute('''
local checks={
    pcall(function()track.Length=2 end),
    pcall(function()track.Priority=3 end),
    pcall(function()track:Play(-1)end),
    pcall(function()track:AdjustSpeed(0/0)end),
    pcall(function()track.TimePosition=.1 end),
    pcall(function()animator.LoadAnimation(workspace,animation)end),
    pcall(function()Instance.new("Animator"):LoadAnimation(animation)end),
    pcall(function()Instance.new("AnimationTrack")end)
}
return table.unpack(checks)
''')['values']
        # The last pcall also returns its diagnostic in the table.
        self.assertEqual(values[:8], [False]*8)

    def test_malformed_clip_is_rejected_without_installing_length(self):
        self.setup_track()
        clip=copy.deepcopy(CLIP);clip['keyframes'][1]['time']=-1
        self.assertFalse(self.complete(clip)['ok'])


if __name__ == '__main__':
    unittest.main()
