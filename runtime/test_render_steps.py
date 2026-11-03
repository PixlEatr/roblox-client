"""Render-step behavior exercised in the real local engine scheduler."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import test_motion_math as motion
import test_script_execution as scripts

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=json.loads((ROOT/'runtime/tests/references/render-step-studio-reference.json').read_text())


@unittest.skipUnless(motion.RUST_HOST.is_file(),'build the worker first')
class RenderStepTests(unittest.TestCase):
    setUp=motion.MotionMathTests.setUp
    execute=motion.MotionMathTests.execute

    def test_render_priority_enum_matches_studio(self):
        result=self.execute('''
local out={}
for _,item in ipairs(Enum.RenderPriority:GetEnumItems())do
 assert(typeof(item)=="EnumItem" and item.EnumType==Enum.RenderPriority)
 table.insert(out,{item.Name,item.Value})
end
return game:GetService("HttpService"):JSONEncode(out)
''')
        self.assertEqual(json.loads(result['values'][0]),REFERENCE['priorities'])

    def test_client_context_methods_include_edit_and_run_mode(self):
        result=self.execute('''
local r=game:GetService("RunService")
return r:IsRunning(),r:IsClient(),r:IsStudio(),r:IsServer(),r:IsEdit(),r:IsRunMode()
''')
        self.assertEqual(result['values'],[True,True,False,False,False,False])

    def test_callbacks_and_signals_follow_native_priority_order(self):
        self.execute('''
local r=game:GetService("RunService")
local log={};local frames=0;local dt
r:BindToRenderStep("late",300,function(value)assert(value==dt);table.insert(log,"late")end)
r:BindToRenderStep("early",100,function(value)assert(value>0);dt=value;table.insert(log,"early")end)
local old=r.RenderStepped:Connect(function(value)assert(value==dt);table.insert(log,"RenderStepped")end)
local pre=r.PreRender:Connect(function(value)
 assert(value==dt);table.insert(log,"PreRender");frames+=1
end)
while frames<3 do r.PreRender:Wait()end
r:UnbindFromRenderStep("early");r:UnbindFromRenderStep("late")
old:Disconnect();pre:Disconnect()
for i=1,#log,4 do assert(table.concat(log,",",i,i+3)=="early,late,RenderStepped,PreRender")end
''')

    def test_duplicate_names_at_both_same_and_different_priorities_are_all_unbound(self):
        self.execute('''
local r=game:GetService("RunService");local count=0
local function add()count+=1 end
assert(select("#",r:BindToRenderStep("duplicate",100,add))==0)
r:BindToRenderStep("duplicate",100,add);r:BindToRenderStep("duplicate",200,add)
r.PreRender:Wait();assert(count==3)
assert(select("#",r:UnbindFromRenderStep("duplicate"))==0)
r:UnbindFromRenderStep("not bound")
r.PreRender:Wait();r.PreRender:Wait();assert(count==3)
''')

    def test_binding_during_frame_observes_native_priority_group_boundaries(self):
        self.execute('''
local r=game:GetService("RunService");local log={};local first=true
r:BindToRenderStep("removed",400,function()table.insert(log,"removed")end)
r:BindToRenderStep("main",200,function()
 table.insert(log,"main")
 if first then first=false
  r:UnbindFromRenderStep("removed")
  r:BindToRenderStep("low",100,function()table.insert(log,"low")end)
  r:BindToRenderStep("same",200,function()table.insert(log,"same")end)
  r:BindToRenderStep("high",300,function()table.insert(log,"high")end)
 end
end)
r.PreRender:Wait();assert(table.concat(log,",")=="main,high")
table.clear(log);r.PreRender:Wait();assert(table.concat(log,",")=="low,main,same,high")
for _,name in ipairs({"removed","main","low","same","high"})do r:UnbindFromRenderStep(name)end
''')

    def test_yielding_callback_does_not_block_later_priorities_or_future_frames(self):
        self.execute('''
local r=game:GetService("RunService");local started,later=0,0;local finished=false
r:BindToRenderStep("yielding",100,function()
 started+=1
 if started==1 then task.wait(.09);finished=true end
end)
r:BindToRenderStep("later",200,function()later+=1 end)
task.wait(.05);assert(started>0 and later==started and not finished)
task.wait(.08);assert(started>1 and finished and later==started)
r:UnbindFromRenderStep("yielding");r:UnbindFromRenderStep("later")
''')

    def test_unbinding_keeps_already_yielded_invocation_alive(self):
        self.execute('''
local r=game:GetService("RunService");local began=false;local finished=false
r:BindToRenderStep("once",100,function()
 r:UnbindFromRenderStep("once");began=true;task.wait(.04);finished=true
end)
for _=1,100 do if began then break end;task.wait(.005)end
assert(began and not finished)
task.wait(.06);assert(finished)
''')

    def test_unbind_releases_capacity_without_allocating_signal_connections(self):
        self.execute('''
local r=game:GetService("RunService")
for i=1,1000 do r:BindToRenderStep("temporary",100,function()end);r:UnbindFromRenderStep("temporary")end
for i=1,256 do r:BindToRenderStep("cap"..i,100,function()end)end
assert(not pcall(function()r:BindToRenderStep("over cap",100,function()end)end))
for i=1,256 do r:UnbindFromRenderStep("cap"..i)end
r:BindToRenderStep("recovered",100,function()end);r:UnbindFromRenderStep("recovered")
''')

    def test_argument_errors_are_atomic_and_engine_wait_is_pinned(self):
        self.execute('''
local r=game:GetService("RunService")
for _,priority in ipairs({0/0,math.huge,-math.huge,2147483648,-2147483649})do
 assert(not pcall(function()r:BindToRenderStep("invalid",priority,function()error("invalid binding ran")end)end))
end
assert(not pcall(function()r:BindToRenderStep("invalid",100,{})end))
assert(not pcall(function()r:BindToRenderStep(string.rep("x",257),100,function()end)end))
assert(not pcall(function()r.BindToRenderStep(Instance.new("Part"),"invalid",100,function()end)end))
local count=0
task=nil
r:BindToRenderStep("pinned",100,function()count+=1 end)
r.PreRender:Wait();assert(count==1)
r:UnbindFromRenderStep("pinned")
''')

    def test_error_in_callback_is_reported_without_stopping_other_bindings(self):
        result=self.host.call({'op':'execute','source':'''healthyRenderCalls=0
local r=game:GetService("RunService")
r:BindToRenderStep("bad",100,function()error("expected render callback failure")end)
r:BindToRenderStep("healthy",200,function()healthyRenderCalls+=1 end)''','timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        errors=list(result.get('task_errors',[]))
        for _ in range(3):
            time.sleep(.025)
            batch=self.host.call({'op':'poll','timeout_ms':100})
            self.assertTrue(batch['ok'],batch.get('error'));errors.extend(batch.get('task_errors',[]))
        result=self.host.call({'op':'execute','source':'local r=game:GetService("RunService");r:UnbindFromRenderStep("bad");r:UnbindFromRenderStep("healthy");return healthyRenderCalls','timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertGreater(result['values'][0],0)
        self.assertTrue(any('expected render callback failure' in e for e in errors),errors)

    def test_cpu_slice_does_not_advance_priority_before_callback_yields_or_finishes(self):
        result=self.host.call({'op':'execute','source':'''renderTrace={};renderReady=false
local r=game:GetService("RunService")
r:BindToRenderStep("slow",100,function()
 table.insert(renderTrace,"start");local sum=0
 for i=1,3000000 do sum+=i%7 end
 assert(sum>0);table.insert(renderTrace,"finish")
end)
r:BindToRenderStep("after",200,function()
 table.insert(renderTrace,"after");renderReady=true
 r:UnbindFromRenderStep("slow");r:UnbindFromRenderStep("after")
end)''','timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        deadline=time.monotonic()+5;polls=0
        while time.monotonic()<deadline:
            time.sleep(.003);batch=self.host.call({'op':'poll','timeout_ms':3});polls+=1
            self.assertTrue(batch['ok'],batch.get('error'));self.assertFalse(batch.get('task_errors'),batch.get('task_errors'))
            result=self.host.call({'op':'execute','source':'return renderReady,table.concat(renderTrace,",")','timeout_ms':3})
            self.assertTrue(result['ok'],result.get('error'))
            if result.get('completed') and result['values'][0]:break
        else:self.fail('CPU-sliced render callback did not finish')
        self.assertGreater(polls,1)
        self.assertEqual(result['values'],[True,'start,finish,after'])

    def test_yielding_callback_backpressure_reserves_foreground_capacity(self):
        self.execute('''
renderGate=Instance.new("BindableEvent");renderStarted=0
local r=game:GetService("RunService")
for i=1,64 do r:BindToRenderStep("waiting"..i,i,function()renderStarted+=1;renderGate.Event:Wait()end)end
''')
        deadline=time.monotonic()+5;peak=0
        while time.monotonic()<deadline and peak<1900:
            time.sleep(.018);batch=self.host.call({'op':'poll','timeout_ms':20})
            self.assertTrue(batch['ok'],batch.get('error'));self.assertFalse(batch.get('task_errors'),batch.get('task_errors'))
            peak=max(peak,batch.get('pending_tasks',0))
        self.assertGreaterEqual(peak,1900)
        result=self.execute('''
local r=game:GetService("RunService")
for i=1,64 do r:UnbindFromRenderStep("waiting"..i)end
renderGate:Fire();return "foreground responsive"
''')
        self.assertEqual(result['values'],['foreground responsive'])

    def test_local_script_shutdown_removes_owned_render_bindings(self):
        data=scripts.snapshot();scripts.add_script(data,'RenderLifecycle','''
shared.renderCount=0
game:GetService("RunService"):BindToRenderStep("owned",100,function()shared.renderCount+=1 end)
''',kind='LocalScript',parent='gui')
        self.host.close();self.host=motion.PersistentLuauHost(data);self.addCleanup(self.host.close)
        self.execute('''
local r=game:GetService("RunService")
r.PreRender:Wait();assert(shared.renderCount>0)
game:GetService("Players").LocalPlayer.PlayerGui.RenderLifecycle.Disabled=true
task.wait(.01);local held=shared.renderCount
task.wait(.05);assert(shared.renderCount==held)
''')

    def test_authored_camera_demo_updates_actual_camera_state(self):
        result=self.execute((ROOT/'runtime/scripts/render_step_demo.luau').read_text())
        self.assertEqual(result['values'][0],'Render-step camera updated')
        self.assertGreaterEqual(result['values'][1],5)
        self.assertGreater(result['values'][2],0)

    @unittest.skipUnless(motion.STANDALONE.is_file(),'build the standalone host first')
    def test_standalone_camera_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'source.luau').write_text((ROOT/'runtime/scripts/render_step_demo.luau').read_text())
            (path/'world.json').write_text(json.dumps(motion.SNAPSHOT))
            result=subprocess.run([str(motion.STANDALONE),'--source',str(path/'source.luau'),'--world-snapshot',str(path/'world.json'),'--timeout-ms','2000'],capture_output=True,timeout=10)
            body=json.loads(result.stdout)
            self.assertEqual(result.returncode,0,body.get('error'));self.assertTrue(body['ok'],body.get('error'))
            self.assertFalse(body.get('task_errors'),body.get('task_errors'))
            self.assertEqual(body['values'][0],'Render-step camera updated')


if __name__=='__main__':unittest.main()
