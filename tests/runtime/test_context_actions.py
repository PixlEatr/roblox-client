"""Context actions: native metadata/cancellation and real host-input control flow."""
import copy
import json
from pathlib import Path
import time
import subprocess
import tempfile
import unittest
import test_motion_math as motion
import test_input_events as inputs
import test_script_execution as scripts

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((ROOT/'tests/fixtures/references/context-actions-studio-reference.json').read_text())
LIFECYCLE=json.loads((ROOT/'tests/fixtures/references/context-actions-lifecycle-studio-reference.json').read_text())


class ContextActionTests(unittest.TestCase):
    execute=motion.MotionMathTests.execute
    deliver=inputs.InputEventTests.deliver
    key=inputs.InputEventTests.key

    def setUp(self):
        data=copy.deepcopy(motion.SNAPSHOT)
        data['input_capabilities']={'KeyboardEnabled':True,'MouseEnabled':True}
        self.host=motion.PersistentLuauHost(data);self.addCleanup(self.host.close)

    def test_action_enums_match_studio(self):
        result=self.execute('''local out={}
for _,name in ipairs({"ContextActionResult","ContextActionPriority","PlayerActions"})do
 local items={}
 for _,v in ipairs(Enum[name]:GetEnumItems())do
  assert(typeof(v)=="EnumItem" and v.EnumType==Enum[name] and Enum[name]:FromValue(v.Value)==v)
  table.insert(items,{name=v.Name,value=v.Value})
 end
 out[name]=items
end
return game:GetService("HttpService"):JSONEncode(out)''')
        self.assertEqual(json.loads(result['values'][0]),REFERENCE['enums'])

    def test_native_binding_metadata_and_copy_isolation(self):
        result=self.execute('''local c=game:GetService("ContextActionService");local out={}
local function info(name)
 local result=c:GetBoundActionInfo(name)
 if result.inputTypes then for i,x in ipairs(result.inputTypes)do result.inputTypes[i]={kind=typeof(x),name=tostring(x)}end end
 return result
end
c:BindAction("a",function()end,false,Enum.KeyCode.W,Enum.UserInputType.MouseButton1)
out.first=info("a")
c:BindActionAtPriority("b",function()end,false,3000,Enum.KeyCode.W)
out.second=info("b")
c:SetTitle("a","Probe title");c:SetDescription("a","Probe description");c:SetImage("a","rbxassetid://123")
out.labeled=info("a")
c:BindAction("a",function()end,false,Enum.KeyCode.A);out.rebound=info("a")
local copied=c:GetBoundActionInfo("a");copied.inputTypes[1]=Enum.KeyCode.Z;copied.priorityLevel=-1
assert(c:GetBoundActionInfo("a").inputTypes[1]==Enum.KeyCode.A and c:GetBoundActionInfo("a").priorityLevel==2000)
assert(next(c:GetBoundActionInfo("missing"))==nil)
local all=c:GetAllBoundActionInfo();assert(all.a and all.b);table.clear(all)
assert(c:GetAllBoundActionInfo().a)
c:UnbindAllActions();assert(next(c:GetAllBoundActionInfo())==nil)
return game:GetService("HttpService"):JSONEncode(out)''')
        self.assertEqual(json.loads(result['values'][0]),{k:REFERENCE[k] for k in ['first','second','labeled','rebound']})

    def test_native_cancel_callbacks_and_stack_positions_match_studio(self):
        source=(ROOT/'tests/fixtures/context_actions_lifecycle_studio_probe.luau').read_text()
        result=json.loads(self.execute(source)['values'][0])
        # Native JSONEncode renders empty anonymous tables as []; normalize only
        # those empty metadata rows, not actual action records or call ordering.
        def normalized(value):
            if value==[]:return {}
            if isinstance(value,list):return [normalized(v) for v in value]
            if isinstance(value,dict):return {k:normalized(v) for k,v in value.items()}
            return value
        self.assertEqual(normalized(result),normalized(LIFECYCLE))

    def test_cancel_is_inline_fresh_neutral_and_sees_native_binding_lifecycle(self):
        self.execute('''local c=game:GetService("ContextActionService");local calls={};local last
local function cancel(name,state,input)
 assert(state==Enum.UserInputState.Cancel and input.UserInputState==state)
 assert(input.KeyCode==Enum.KeyCode.None and input.UserInputType==Enum.UserInputType.None)
 assert(input.Position==Vector3.zero and input.Delta==Vector3.zero)
 assert(input~=last);last=input
 table.insert(calls,{next(c:GetBoundActionInfo("a"))~=nil,next(c:GetBoundActionInfo("b"))~=nil})
end
c:BindAction("a",cancel,false,Enum.KeyCode.W)
c:BindAction("b",function()end,false,Enum.KeyCode.W)
assert(#calls==1 and calls[1][1] and not calls[1][2])
c:UnbindAction("a");assert(#calls==2 and not calls[2][1] and calls[2][2])
c:UnbindAction("b")''')

    def test_priority_stack_and_pass_use_original_input_object(self):
        self.execute('''actionLog={};local c=game:GetService("ContextActionService")
local function handler(name,state,input)
 if state==Enum.UserInputState.Begin then
  assert(input==game:GetService("UserInputService"):GetKeysPressed()[1])
  assert(input.KeyCode==Enum.KeyCode.W and input.UserInputType==Enum.UserInputType.Keyboard)
  table.insert(actionLog,name)
 end
 return Enum.ContextActionResult.Pass
end
c:BindActionAtPriority("low",handler,false,1000,Enum.KeyCode.W)
c:BindActionAtPriority("high-old",handler,false,3000,Enum.KeyCode.W)
c:BindActionAtPriority("medium",handler,false,2000,Enum.KeyCode.W)
c:BindActionAtPriority("high-new",handler,false,3000,Enum.KeyCode.W)''')
        self.deliver(self.key())
        self.execute('assert(table.concat(actionLog,",")=="high-new,high-old,medium,low")')

    def test_only_typed_pass_propagates_nil_numbers_booleans_and_other_enums_sink(self):
        for value,passes in [('nil',False),('false',False),('true',False),('0',False),('1',False),
                             ('"Pass"',False),('Enum.UserInputState.Change',False),
                             ('Enum.ContextActionResult.Sink',False),('Enum.ContextActionResult.Pass',True)]:
            self.execute('''local c=game:GetService("ContextActionService");c:UnbindAllActions();lowCalls=0
c:BindAction("low",function(_,state)if state==Enum.UserInputState.Begin then lowCalls+=1 end end,false,Enum.KeyCode.W)
c:BindAction("top",function(_,state)if state==Enum.UserInputState.Begin then return '''+value+''' end end,false,Enum.KeyCode.W)''')
            self.deliver(self.key())
            self.assertEqual(self.execute('return lowCalls')['values'],[1 if passes else 0],value)
            self.deliver(self.key('end'))

    def test_user_yield_sinks_without_blocking_other_inputs_or_frame_callbacks(self):
        self.execute('''local c=game:GetService("ContextActionService");low=0;finished=false;other=0
c:BindAction("low",function(_,s)if s==Enum.UserInputState.Begin then low+=1 end end,false,Enum.KeyCode.W)
c:BindAction("yield",function(_,s)
 if s==Enum.UserInputState.Begin then task.wait(.06);finished=true;return Enum.ContextActionResult.Pass end
end,false,Enum.KeyCode.W)
c:BindAction("other",function(_,s)if s==Enum.UserInputState.Begin then other+=1 end end,false,Enum.KeyCode.A)''')
        self.deliver(self.key())
        self.deliver(self.key(key='A'))
        self.execute('''game:GetService("RunService").PreRender:Wait()
assert(other==1 and low==0);task.wait(.08);assert(finished and low==0)''')

    def test_cpu_slice_retains_pass_and_control_phase_before_render(self):
        self.execute('''local c=game:GetService("ContextActionService");heavyFinished=false;lowRan=false
c:BindAction("low",function(_,s)if s==Enum.UserInputState.Begin then assert(heavyFinished);lowRan=true end end,false,Enum.KeyCode.W)
c:BindAction("heavy",function(_,s)
 if s==Enum.UserInputState.Begin then
  local sum=0;for i=1,3000000 do sum+=i%7 end;heavyFinished=true
  return Enum.ContextActionResult.Pass
 end
end,false,Enum.KeyCode.W)
game:GetService("RunService"):BindToRenderStep("after-actions",100,function()
 if game:GetService("UserInputService"):IsKeyDown("W")then assert(lowRan,"render preceded action phase")end
end)''')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_events':[self.key()]},'timeout_ms':3})
        self.assertTrue(result['ok'],result.get('error'));self.assertFalse(result.get('task_errors'))
        self.execute('''game:GetService("RunService").PreRender:Wait();assert(heavyFinished and lowRan)
game:GetService("RunService"):UnbindFromRenderStep("after-actions")''')

    def test_key_user_input_type_string_and_player_actions(self):
        self.execute('''local c=game:GetService("ContextActionService");actionNames={}
local function handler(n,s)if s==Enum.UserInputState.Begin then actionNames[n]=(actionNames[n] or 0)+1 end;return Enum.ContextActionResult.Pass end
c:BindAction("key",handler,false,Enum.KeyCode.W)
c:BindAction("keyboard",handler,false,Enum.UserInputType.Keyboard)
c:BindAction("string",handler,false,"w")
c:BindAction("forward",handler,false,Enum.PlayerActions.CharacterForward)
c:BindAction("mouse",handler,false,Enum.UserInputType.MouseButton1)
c:BindActionToInputTypes("jump",handler,false,Enum.PlayerActions.CharacterJump)''')
        self.deliver(self.key())
        self.deliver(self.key(key='Up'))
        self.deliver(self.key(key='Space'))
        self.deliver({'phase':'begin','input_type':'MouseButton1'})
        self.execute('''assert(actionNames.key==1 and actionNames.keyboard==3 and actionNames.string==1)
assert(actionNames.forward==2 and actionNames.mouse==1 and actionNames.jump==1)''')

    def test_processed_ui_input_does_not_begin_game_actions_and_release_clears_existing(self):
        self.execute('''local c=game:GetService("ContextActionService");beginCount=0;endCount=0;held=false
c:BindAction("hold",function(_,s)
 if s==Enum.UserInputState.Begin then beginCount+=1;held=true
 elseif s==Enum.UserInputState.End or s==Enum.UserInputState.Cancel then held=false;endCount+=1 end
end,false,Enum.KeyCode.W)''')
        self.deliver(self.key(processed=True));self.deliver(self.key('end',processed=True))
        self.execute('assert(beginCount==0 and not held)')
        self.deliver(self.key());self.execute('assert(held and beginCount==1)')
        self.deliver(self.key('end',processed=True));self.execute('assert(not held and endCount==2)')

    def test_device_disconnect_cancels_action_with_neutral_input(self):
        self.execute('''local c=game:GetService("ContextActionService");began=false;cancelled=false
c:BindAction("held",function(_,s,i)
 if s==Enum.UserInputState.Begin then began=true
 elseif s==Enum.UserInputState.Cancel then assert(i.KeyCode==Enum.KeyCode.None);cancelled=true end
end,false,Enum.KeyCode.W)''')
        self.deliver(self.key())
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
             'input_capabilities':{'KeyboardEnabled':False}},'timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        self.execute('assert(began and cancelled)')

    def test_invalid_rebinding_preserves_existing_action_and_resource_slots_reclaim(self):
        self.execute('''local c=game:GetService("ContextActionService")
c:BindAction("preserve",function()end,false,Enum.KeyCode.W)
for _,bad in ipairs({{},true,119,Enum.ContextActionResult.Pass})do
 assert(not pcall(function()c:BindAction("preserve",function()end,false,bad)end))
 assert(c:GetBoundActionInfo("preserve").inputTypes[1]==Enum.KeyCode.W)
end
for _,priority in ipairs({0/0,math.huge,-math.huge,2147483648,-2147483649,Enum.ContextActionPriority.High})do
 assert(not pcall(function()c:BindActionAtPriority("preserve",function()end,false,priority,Enum.KeyCode.A)end))
end
assert(not pcall(function()c:BindAction("bad",{},false,Enum.KeyCode.W)end))
assert(not pcall(function()c:BindAction("bad",function()end,1,Enum.KeyCode.W)end))
assert(not pcall(function()c:BindAction(string.rep("x",257),function()end,false)end))
assert(not pcall(function()c.BindAction(Instance.new("Part"),"bad",function()end,false)end))
c:UnbindAllActions()
for i=1,1000 do c:BindAction("temporary",function()end,false);c:UnbindAction("temporary")end
for i=1,256 do c:BindAction("cap"..i,function()end,false)end
assert(not pcall(function()c:BindAction("over",function()end,false)end))
c:UnbindAllActions();c:BindAction("recovered",function()end,false)
assert(c:GetAllBoundActionInfo().recovered)''')

    def test_desktop_touch_request_metadata_and_explicit_mobile_ui_boundary(self):
        self.execute('''local c=game:GetService("ContextActionService")
c:BindAction("touch-request",function()end,true,Enum.KeyCode.W)
assert(c:GetBoundActionInfo("touch-request").createTouchButton and c:GetButton("touch-request")==nil)
c:SetPosition("touch-request",UDim2.fromScale(.5,.5));c:SetTitle("missing","ignored")
c:SetImage("missing","ignored");c:SetDescription("missing","ignored")
assert(c:GetButton("missing")==nil)
c:UnbindAction("touch-request")''')
        self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                                             'input_capabilities':{'TouchEnabled':True}}})
        self.execute('''local c=game:GetService("ContextActionService")
local ok,message=pcall(function()c:BindAction("mobile",function()end,true,Enum.KeyCode.W)end)
assert(not ok and string.find(message,"touch%-button UI is not implemented"))
assert(next(c:GetBoundActionInfo("mobile"))==nil)
c:BindAction("direct-touch",function(_,s)if s==Enum.UserInputState.Begin then touchActionRan=true end end,false,Enum.UserInputType.Touch)''')
        self.deliver({'phase':'begin','input_type':'Touch','input_id':'finger'})
        self.execute('assert(touchActionRan)')

    def test_local_script_shutdown_releases_owned_bindings(self):
        data=scripts.snapshot();scripts.add_script(data,'ActionLifecycle','''
game:GetService("ContextActionService"):BindAction("owned",function()shared.actionCallbackRan=true end,false,Enum.KeyCode.W)
''',kind='LocalScript',parent='gui')
        data['input_capabilities']={'KeyboardEnabled':True}
        self.host.close();self.host=motion.PersistentLuauHost(data);self.addCleanup(self.host.close)
        self.execute('''local c=game:GetService("ContextActionService")
assert(c:GetAllBoundActionInfo().owned)
game:GetService("Players").LocalPlayer.PlayerGui.ActionLifecycle.Disabled=true
task.wait(.01);assert(c:GetAllBoundActionInfo().owned==nil)''')
        self.deliver(self.key())
        self.execute('assert(not shared.actionCallbackRan)')

    def test_reentrant_unbinding_cancels_each_action_once(self):
        self.execute('''local c=game:GetService("ContextActionService");local a,b=0,0
c:BindAction("a",function(_,state)
 if state==Enum.UserInputState.Cancel then a+=1;c:UnbindAllActions()end
end,false)
c:BindAction("b",function(_,state)if state==Enum.UserInputState.Cancel then b+=1 end end,false)
c:UnbindAllActions()
assert(a==1 and b==1,"an action was cancelled twice during reentrant unbinding")
assert(next(c:GetAllBoundActionInfo())==nil)''')

    def test_movement_demo_drives_real_humanoid_intents_and_stops(self):
        started=self.host.call({'op':'execute','source':(ROOT/'runtime/scripts/context_action_movement_demo.luau').read_text(),'timeout_ms':10})
        self.assertTrue(started['ok'],started.get('error'));self.assertFalse(started.get('completed'))
        job=started['job_id'];results=[];intents=list(started.get('movement_intents',[]))
        response=self.deliver(self.key());results.extend(response.get('completed_jobs',[]));intents.extend(response.get('movement_intents',[]))
        deadline=time.monotonic()+3
        while not any(r['job_id']==job for r in results) and time.monotonic()<deadline:
            time.sleep(.02);response=self.host.call({'op':'poll','timeout_ms':100})
            self.assertTrue(response['ok'],response.get('error'));self.assertFalse(response.get('task_errors'))
            results.extend(response.get('completed_jobs',[]));intents.extend(response.get('movement_intents',[]))
        done=next((r for r in results if r['job_id']==job),None)
        self.assertIsNotNone(done,'movement demo did not finish');self.assertTrue(done['ok'],done.get('error'))
        self.assertEqual(done['values'],['Context action drove Humanoid movement',4,True])
        moves=[i for i in intents if i['kind']=='Move']
        self.assertTrue(any(i['vector']==[0,0,-1] and not i['relative_to_camera'] for i in moves),moves)
        self.assertEqual(moves[-1]['vector'],[0,0,0])
        self.execute('assert(next(game:GetService("ContextActionService"):GetAllBoundActionInfo())==nil)')

    def test_cancellation_backpressure_keeps_foreground_responsive_and_callback_alive(self):
        self.execute('''actionGate=Instance.new("BindableEvent");actionCancelled=false
local c=game:GetService("ContextActionService")
c:BindAction("held-reference",function(_,s)if s==Enum.UserInputState.Cancel then actionCancelled=true end end,false,Enum.KeyCode.W)
local r=game:GetService("RunService")
for i=1,64 do r:BindToRenderStep("action-waiting"..i,i,function()actionGate.Event:Wait()end)end
''')
        deadline=time.monotonic()+5;peak=0
        while time.monotonic()<deadline and peak<1919:
            time.sleep(.018);batch=self.host.call({'op':'poll','timeout_ms':20})
            self.assertTrue(batch['ok'],batch.get('error'));self.assertFalse(batch.get('task_errors'))
            peak=max(peak,batch.get('pending_tasks',0))
        self.assertGreaterEqual(peak,1919)
        self.execute('''local c=game:GetService("ContextActionService");c:UnbindAction("held-reference")
assert(next(c:GetBoundActionInfo("held-reference"))==nil)
local r=game:GetService("RunService")
for i=1,64 do r:UnbindFromRenderStep("action-waiting"..i)end
actionGate:Fire()
for _=1,100 do if actionCancelled then break end;task.wait(.005)end
assert(actionCancelled,"deferred cancellation lost its callback reference")''')

    @unittest.skipUnless(motion.STANDALONE.is_file(),'build standalone worker first')
    def test_standalone_native_cancellation_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'source.luau').write_text((ROOT/'tests/fixtures/context_actions_lifecycle_studio_probe.luau').read_text())
            (path/'world.json').write_text(json.dumps(motion.SNAPSHOT))
            process=subprocess.run([str(motion.STANDALONE),'--source',str(path/'source.luau'),
                '--world-snapshot',str(path/'world.json'),'--timeout-ms','1000'],capture_output=True,timeout=10)
            result=json.loads(process.stdout)
        self.assertEqual(process.returncode,0,result.get('error'));self.assertTrue(result['ok'],result.get('error'))
        self.assertFalse(result.get('task_errors'))
        observed=json.loads(result['values'][0])
        self.assertEqual(observed['calls'],LIFECYCLE['calls'])
        for left,right in zip(observed['steps'],LIFECYCLE['steps']):
            self.assertEqual(left['name'],right['name'])
            for a,b in zip(left['rows'],right['rows']):self.assertEqual(a or {},b or {})

    def test_error_sinks_only_its_event_and_other_input_still_runs(self):
        self.execute('''local c=game:GetService("ContextActionService");lowerRan=false;otherRan=false
c:BindAction("lower",function(_,s)if s==Enum.UserInputState.Begin then lowerRan=true end end,false,Enum.KeyCode.W)
c:BindAction("bad",function(_,s)if s==Enum.UserInputState.Begin then error("expected action failure")end end,false,Enum.KeyCode.W)
c:BindAction("other",function(_,s)if s==Enum.UserInputState.Begin then otherRan=true end end,false,Enum.KeyCode.A)''')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_events':[self.key(),self.key(key='A')]},'timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertTrue(any('expected action failure' in e for e in result.get('task_errors',[])),result)
        self.execute('assert(otherRan and not lowerRan)')

if __name__=='__main__':unittest.main()
