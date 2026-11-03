"""Ordinary authored Luau exercising real property updates and tween callbacks."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from persistent_runtime import PersistentLuauHost, RUST_HOST

SNAPSHOT=json.loads((ROOT/'runtime/tests/world_snapshot.json').read_text())


@unittest.skipUnless(RUST_HOST.is_file(),'build the persistent worker first')
class TweenPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.host=PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self,source):
        result=self.host.call({'op':'execute','source':source,'timeout_ms':1000})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertFalse(result.get('task_errors'),result.get('task_errors'))
        if not result.get('completed',False):
            import time
            deadline=time.monotonic()+3
            job=result['job_id']
            while time.monotonic()<deadline:
                result=self.host.call({'op':'poll','timeout_ms':100})
                self.assertTrue(result['ok'],result.get('error'))
                self.assertFalse(result.get('task_errors'),result.get('task_errors'))
                done=next((r for r in result.get('completed_jobs',[]) if r['job_id']==job),None)
                if done:
                    self.assertTrue(done['ok'],done)
                    return done
                time.sleep(.005)
            self.fail('tween job did not finish')
        return result

    def test_tween_is_an_instance_with_immutable_info_and_typed_state(self):
        result=self.execute('''
local v=Instance.new("NumberValue")
local info=TweenInfo.new(.1)
local t=game:GetService("TweenService"):Create(v,info,{Value=10})
assert(typeof(t)=="Instance" and t:IsA("Tween") and t:IsA("TweenBase"))
assert(t.Instance==v and t.TweenInfo==info and t.PlaybackState==Enum.PlaybackState.Begin)
t:Cancel();t:Pause();assert(t.PlaybackState==Enum.PlaybackState.Begin)
for _,k in ipairs({"PlaybackState","TweenInfo","Instance"}) do
 assert(not pcall(function()t[k]=0 end))
end
return #Enum.PlaybackState:GetEnumItems(),t.PlaybackState.Name
''')
        self.assertEqual(result['values'],[6,'Begin'])

    def test_new_part_has_measured_color_and_rejects_incorrect_type(self):
        result=self.execute('''
local p=Instance.new("Part")
assert(typeof(p.Color)=="Color3")
assert(not pcall(function()p.Color=Vector3.new(1,0,0)end))
return p.Color.R,p.Color.G,p.Color.B
''')
        for actual,expected in zip(result['values'],[.6392157077789307,.6352941393852234,.6470588445663452]):
            self.assertAlmostEqual(actual,expected,places=7)

    def test_changes_properties_and_wakes_completed_wait_with_enum(self):
        result=self.execute('''
local v=Instance.new("NumberValue")
local t=game:GetService("TweenService"):Create(v,TweenInfo.new(.09,Enum.EasingStyle.Linear),{Value=10})
local changes=0
v:GetPropertyChangedSignal("Value"):Connect(function()changes+=1 end)
t:Play();task.wait(.025)
assert(v.Value>0 and v.Value<10)
local ended=t.Completed:Wait()
assert(ended==Enum.PlaybackState.Completed and t.PlaybackState==ended)
assert(v.Value==10 and changes>1)
return v.Value,ended.Name
''')
        self.assertEqual(result['values'],[10,'Completed'])

    def test_pause_freezes_then_play_resumes(self):
        self.execute('''
local v=Instance.new("NumberValue")
local t=game:GetService("TweenService"):Create(v,TweenInfo.new(.15,Enum.EasingStyle.Linear),{Value=10})
t:Play();task.wait(.025);t:Pause()
local held=v.Value
assert(t.PlaybackState==Enum.PlaybackState.Paused and held>0 and held<10)
task.wait(.04);assert(v.Value==held)
t:Play();assert(v.Value==held)
assert(t.Completed:Wait()==Enum.PlaybackState.Completed and v.Value==10)
''')

    def test_cancel_event_holds_value_and_restart_finishes(self):
        self.execute('''
local v=Instance.new("NumberValue")
local t=game:GetService("TweenService"):Create(v,TweenInfo.new(.1,Enum.EasingStyle.Linear),{Value=10})
local events={}
t.Completed:Connect(function(state)table.insert(events,state)end)
t:Play();task.wait(.02);t:Cancel();local held=v.Value
task.wait(.02)
assert(t.PlaybackState==Enum.PlaybackState.Cancelled and v.Value==held)
assert(#events==1 and events[1]==Enum.PlaybackState.Cancelled)
t:Cancel();task.wait();assert(#events==1)
t:Play();assert(v.Value==held)
t.Completed:Wait();task.wait();assert(v.Value==10 and #events==2)
t:Cancel();task.wait();assert(t.PlaybackState==Enum.PlaybackState.Cancelled and #events==3)
''')

    def test_delay_repeats_reverse_and_start_is_captured_at_play(self):
        self.execute('''
local v=Instance.new("NumberValue");v.Value=2
local t=game:GetService("TweenService"):Create(v,
 TweenInfo.new(.02,Enum.EasingStyle.Linear,Enum.EasingDirection.Out,1,true,.02),{Value=10})
v.Value=7;t:Play();assert(t.PlaybackState==Enum.PlaybackState.Delayed)
t:Pause();assert(t.PlaybackState==Enum.PlaybackState.Delayed)
task.wait(.005);assert(v.Value==7)
assert(t.Completed:Wait()==Enum.PlaybackState.Completed and v.Value==7)
''')

    def test_competing_property_cancels_previous_but_independent_tween_survives(self):
        self.execute('''
local ts=game:GetService("TweenService")
local p=Instance.new("Part")
local a=ts:Create(p,TweenInfo.new(.2),{Transparency=1})
local b=ts:Create(p,TweenInfo.new(.04),{Position=Vector3.new(5,2,3)})
local c=ts:Create(p,TweenInfo.new(.04),{Transparency=.4})
a:Play();b:Play();assert(a.PlaybackState==Enum.PlaybackState.Playing)
c:Play();assert(a.PlaybackState==Enum.PlaybackState.Cancelled)
assert(b.PlaybackState==Enum.PlaybackState.Playing)
c.Completed:Wait();task.wait(.02)
assert(math.abs(p.Transparency-.4)<1e-5 and p.Position==Vector3.new(5,2,3))
''')

    def test_cframe_color_and_gui_datatypes_reach_exact_goals(self):
        # The constructor registry does not promise a default Color. Supply an
        # explicit received value so this tests interpolation of known state.
        snapshot=copy.deepcopy(SNAPSHOT)
        part=next(n for n in snapshot['nodes'] if n['id']=='scope:root-1')
        part['properties']['Color']={'$color3':[1,1,1]}
        self.host.close()
        self.host=PersistentLuauHost(snapshot)
        self.addCleanup(self.host.close)
        self.execute('''
local ts=game:GetService("TweenService")
local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
local target=CFrame.new(3,4,5)*CFrame.Angles(0,math.pi/2,0)
local t=ts:Create(p,TweenInfo.new(.05),{CFrame=target,Color=Color3.new(.2,.3,.4)})
t:Play();t.Completed:Wait()
assert((p.Position-target.Position).Magnitude<1e-5)
assert((p.CFrame.LookVector-target.LookVector).Magnitude<1e-5)
assert(math.abs(p.Color.R-.2)<1e-5)
local ui=Instance.new("Frame")
ui.Size=UDim2.fromOffset(100,100)
ui.AnchorPoint=Vector2.zero
local size=UDim2.new(.4,30,.7,40)
local a=ts:Create(ui,TweenInfo.new(.03),{Size=size,AnchorPoint=Vector2.new(.5,.5)})
a:Play();a.Completed:Wait();assert(ui.Size==size and ui.AnchorPoint==Vector2.new(.5,.5))
''')

    def test_invalid_goal_batch_does_not_write_any_properties(self):
        self.execute('''
local ts=game:GetService("TweenService")
local p=Instance.new("Part")
local old=p.Transparency
for _,goals in ipairs({{Transparency=.5,Missing=4},{Transparency="bad"},
 {Transparency=0/0},{Position={['$vector3']={1,2,3}}},{[1]=4}}) do
 assert(not pcall(function()ts:Create(p,TweenInfo.new(.1),goals)end))
 assert(p.Transparency==old)
end
''')

    def test_destroyed_tweens_release_registry_capacity(self):
        self.execute('''
local ts=game:GetService("TweenService")
local v=Instance.new("NumberValue")
for i=1,300 do
 local t=ts:Create(v,TweenInfo.new(),{Value=i});t:Destroy()
 assert(not pcall(function()t:Play()end))
end
local t=ts:Create(v,TweenInfo.new(0),{Value=99})
t:Play();assert(t.Completed:Wait()==Enum.PlaybackState.Completed and v.Value==99)
''')

    def test_retained_target_reference_keeps_tweening_after_destroy(self):
        self.execute('''
local v=Instance.new("NumberValue")
local t=game:GetService("TweenService"):Create(v,TweenInfo.new(.03),{Value=10})
t:Play();v:Destroy()
assert(t.Completed:Wait()==Enum.PlaybackState.Completed)
assert(v.Value==10 and v.Parent==nil and t.Instance==v)
''')

    def test_demo_finishes_with_bounded_frame_updates(self):
        result=self.execute((ROOT/'runtime/scripts/tween_demo.luau').read_text())
        self.assertEqual(result['values'][0],'Completed')
        self.assertGreater(result['values'][1],1)
        self.assertLess(result['values'][1],100)


if __name__=='__main__':unittest.main()
