"""Host-driven input through the real persistent Luau scheduler."""
import copy
import json
from pathlib import Path
import time
import subprocess
import tempfile
import unittest
import test_motion_math as motion
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_world_snapshot import fixture

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((ROOT/'tests/fixtures/references/input-studio-reference.json').read_text())


class InputEventTests(unittest.TestCase):
    execute=motion.MotionMathTests.execute

    def setUp(self):
        snapshot=copy.deepcopy(motion.SNAPSHOT)
        snapshot['input_capabilities']={name:True for name in ('KeyboardEnabled','MouseEnabled','TouchEnabled')}
        self.host=PersistentLuauHost(snapshot)
        self.addCleanup(self.host.close)

    def deliver(self,*events,ok=True):
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                                                    'input_events':list(events)},'timeout_ms':100})
        self.assertEqual(result['ok'],ok,result.get('error'))
        self.assertFalse(result.get('task_errors'),result.get('task_errors'))
        return result

    def key(self,phase='begin',key='W',**extra):
        return {'phase':phase,'input_type':'Keyboard','key_code':key,**extra}

    def test_complete_enum_catalog_matches_native_studio_including_alias(self):
        result=self.execute('''local out={}
for _,name in ipairs({"MouseBehavior","UserInputState","UserInputType","KeyCode"})do
 local items={}
 for _,item in ipairs(Enum[name]:GetEnumItems())do
  assert(typeof(item)=="EnumItem" and item.EnumType==Enum[name])
  assert(Enum[name]:FromName(item.Name)==item and Enum[name]:FromValue(item.Value)==item)
  table.insert(items,{name=item.Name,value=item.Value})
 end
 out[name]=items
end
assert(Enum.KeyCode.Unknown==Enum.KeyCode.None and Enum.KeyCode.Unknown.Name=="None")
assert(Enum.KeyCode:FromName("Unknown")==Enum.KeyCode.None)
assert(Enum.KeyCode:FromName("NotAKey")==nil)
assert(not pcall(function()Enum.KeyCode.W.Value=1 end))
return game:GetService("HttpService"):JSONEncode(out)''')
        self.assertEqual(json.loads(result['values'][0]),REFERENCE['enums'])

    def test_headless_defaults_and_casting_are_truthful(self):
        self.execute('''local u=game:GetService("UserInputService")
assert(u:GetLastInputType()==Enum.UserInputType.None)
assert(#u:GetKeysPressed()==0 and #u:GetMouseButtonsPressed()==0)
assert(u:GetMouseLocation()==Vector2.zero and u:GetMouseDelta()==Vector2.zero)
assert(u.MouseBehavior==Enum.MouseBehavior.Default and u.MouseIconEnabled)
assert(not u:IsKeyDown("W") and not u:IsKeyDown(119))
for _,value in ipairs({"NotAKey",{},true,Enum.UserInputType.Keyboard,0/0,math.huge})do
 assert(not pcall(function()u:IsKeyDown(value)end))
end
assert(not pcall(function()u.IsKeyDown(Instance.new("Part"),Enum.KeyCode.W)end))
assert(not pcall(function()Instance.new("InputObject")end))
''')

    def test_keyboard_lifecycle_identity_signals_processed_and_fresh_press(self):
        self.execute('''local u=game:GetService("UserInputService");inputLog={};inputTypes={}
u.LastInputTypeChanged:Connect(function(t)table.insert(inputTypes,t)end)
u.InputBegan:Connect(function(i,p)
 assert(typeof(i)=="Instance" and i:IsA("InputObject") and i.Parent==nil)
 assert(i.KeyCode==Enum.KeyCode.W and i.UserInputState==Enum.UserInputState.Begin)
 assert(i.UserInputType==Enum.UserInputType.Keyboard and p)
 if savedInput then assert(savedInput~=i)end
 savedInput=i;table.insert(inputLog,"begin")
end)
u.InputChanged:Connect(function(i,p)assert(i==savedInput and not p);table.insert(inputLog,"change")end)
u.InputEnded:Connect(function(i,p)
 assert(i==savedInput and i.UserInputState==Enum.UserInputState.End and not p)
 table.insert(inputLog,"end")
end)''')
        self.deliver(self.key(processed=True))
        self.execute('''local u=game:GetService("UserInputService")
assert(u:IsKeyDown("W") and u:IsKeyDown(119) and u:IsKeyDown(Enum.KeyCode.W))
assert(u:GetKeysPressed()[1]==savedInput and #u:GetKeysPressed()==1)
assert(#inputTypes==1 and inputTypes[1]==Enum.UserInputType.Keyboard)
local list=u:GetKeysPressed();table.clear(list);assert(#u:GetKeysPressed()==1)
''')
        self.deliver(self.key('change'))
        self.deliver(self.key('end'))
        self.execute('''local u=game:GetService("UserInputService")
assert(not u:IsKeyDown("W") and #u:GetKeysPressed()==0)
assert(savedInput.UserInputState==Enum.UserInputState.End)
assert(table.concat(inputLog,",")=="begin,change,end")''')
        self.deliver(self.key(processed=True))
        self.execute('assert(#inputTypes==1 and #inputLog==4)')

    def test_mutable_input_object_cannot_rewrite_backend_pressed_keys(self):
        self.deliver(self.key())
        self.execute('''local u=game:GetService("UserInputService");savedInput=u:GetKeysPressed()[1]
local changed=0;savedInput:GetPropertyChangedSignal("KeyCode"):Connect(function()changed+=1 end)
savedInput.KeyCode="A";savedInput.UserInputType=Enum.UserInputType.Touch
savedInput.Position=Vector3.new(1,2,3);savedInput.Delta=Vector3.new(4,5,6)
assert(savedInput.KeyCode==Enum.KeyCode.A and savedInput.Position==Vector3.new(1,2,3))
assert(client.propertyOrigin(savedInput,"KeyCode")=="local_input")
assert(u:IsKeyDown("W") and not u:IsKeyDown("A"))
assert(not pcall(function()savedInput.KeyCode=Enum.UserInputType.Keyboard end))
assert(not pcall(function()savedInput.Position=Vector2.zero end))
task.wait(.005);assert(changed==1)''')
        self.deliver(self.key('end'))
        self.execute('assert(#game:GetService("UserInputService"):GetKeysPressed()==0 and savedInput.UserInputState==Enum.UserInputState.End)')

    def test_mouse_buttons_location_coordinates_and_continuous_identity(self):
        self.execute('''mouseInputs={};local u=game:GetService("UserInputService")
u.InputChanged:Connect(function(i,p)
 assert(not p);table.insert(mouseInputs,i)
end)''')
        self.deliver({'phase':'begin','input_type':'MouseButton1','position':[10,20,0],
                      'mouse_location':[10,56]})
        self.execute('''local u=game:GetService("UserInputService");mouseButton=u:GetMouseButtonsPressed()[1]
assert(u:IsMouseButtonPressed("MouseButton1") and not u:IsMouseButtonPressed(Enum.UserInputType.MouseButton2))
assert(mouseButton.KeyCode==Enum.KeyCode.None and mouseButton.Position==Vector3.new(10,20,0))
assert(u:GetMouseLocation()==Vector2.new(10,56))''')
        self.deliver({'phase':'change','input_type':'MouseMovement','position':[30,40,0],
                      'delta':[20,20,0],'mouse_location':[30,76]})
        self.deliver({'phase':'change','input_type':'MouseMovement','position':[35,40,0],'delta':[5,0,0]})
        self.execute('''assert(#mouseInputs==2 and mouseInputs[1]==mouseInputs[2])
assert(mouseButton.Position==Vector3.new(10,20,0))
assert(mouseInputs[1].Position==Vector3.new(35,40,0))''')
        self.deliver({'phase':'end','input_type':'MouseButton1','position':[35,40,0]})
        self.execute('''local u=game:GetService("UserInputService")
assert(not u:IsMouseButtonPressed(Enum.UserInputType.MouseButton1) and #u:GetMouseButtonsPressed()==0)
assert(mouseButton.Position==Vector3.new(35,40,0))''')

    def test_frame_mouse_delta_resets_and_is_zero_when_unlocked(self):
        self.execute('''local u=game:GetService("UserInputService");u.MouseBehavior="LockCenter"
mouseDeltas={};local r=game:GetService("RunService")
r:BindToRenderStep("input-test",Enum.RenderPriority.Input.Value,function()
 local a=u:GetMouseDelta();local b=u:GetMouseDelta();assert(a==b)
 table.insert(mouseDeltas,{a.X,a.Y})
end)''')
        self.deliver({'phase':'change','input_type':'MouseMovement','delta':[7,-3,0]})
        time.sleep(.025)
        self.host.call({'op':'poll','timeout_ms':100})
        self.execute('''game:GetService("RunService").PreRender:Wait()
local found=false
for _,d in ipairs(mouseDeltas)do if d[1]==7 and d[2]==-3 then found=true end end
assert(found,"missing mouse frame delta")
local u=game:GetService("UserInputService")
u.MouseBehavior=Enum.MouseBehavior.Default;assert(u:GetMouseDelta()==Vector2.zero)
u.MouseIconEnabled=false;assert(not u.MouseIconEnabled)
assert(not pcall(function()u.MouseBehavior=Enum.KeyCode.W end))
assert(not pcall(function()u.MouseIconEnabled=1 end))
game:GetService("RunService"):UnbindFromRenderStep("input-test")
assert(mouseDeltas[#mouseDeltas][1]==0 and mouseDeltas[#mouseDeltas][2]==0)''')

    def test_input_handler_precedes_next_render_control_callback(self):
        self.execute('''local u=game:GetService("UserInputService");inputReady=false;renderSawInput=false
u.InputBegan:Connect(function(i)if i.KeyCode==Enum.KeyCode.W then inputReady=true end end)
game:GetService("RunService"):BindToRenderStep("input-order",100,function()
 if u:IsKeyDown("W")then assert(inputReady,"render ran before InputBegan");renderSawInput=true end
end)''')
        time.sleep(.025)
        self.deliver(self.key())
        self.execute('''game:GetService("RunService").PreRender:Wait();assert(renderSawInput)
game:GetService("RunService"):UnbindFromRenderStep("input-order")''')

    def test_multi_touch_identity_changes_cancel_and_touch_signals(self):
        self.execute('''touches={};touchMoves=0;touchEnds=0;local u=game:GetService("UserInputService")
u.TouchStarted:Connect(function(i,p)assert(not p);table.insert(touches,i)end)
u.TouchMoved:Connect(function(i)assert(i==touches[1]);touchMoves+=1 end)
u.TouchEnded:Connect(function(i)assert(i==touches[1] or i==touches[2]);touchEnds+=1 end)''')
        self.deliver(*[{'phase':'begin','input_type':'Touch','input_id':name,'position':[x,2,0]}
                       for name,x in [('a',1),('b',9)]])
        self.deliver({'phase':'change','input_type':'Touch','input_id':'a','position':[3,4,0],'delta':[2,2,0]})
        self.deliver({'phase':'cancel','input_type':'Touch','input_id':'a'},
                     {'phase':'end','input_type':'Touch','input_id':'b'})
        self.execute('''assert(#touches==2 and touches[1]~=touches[2] and touchMoves==1 and touchEnds==2)
assert(touches[1].Position==Vector3.new(3,4,0) and touches[1].UserInputState==Enum.UserInputState.Cancel)
assert(touches[2].UserInputState==Enum.UserInputState.End)''')

    def test_invalid_batches_are_atomic_and_recoverable(self):
        invalid=[self.key('end'),self.key(key='bogus'),self.key(position=[0,0]),
                 self.key(position=[0,1e39,0]),self.key(processed=1),
                 self.key(input_id='keyboard-not-touch'),self.key(unknown=True),
                 {'phase':'begin','input_type':'Touch'},
                 {'phase':'begin','input_type':'MouseMovement'},
                 {'phase':'change','input_type':'Gamepad1'},
                 {'phase':'begin','input_type':'MouseButton1','mouse_location':[0,'no']}]
        for event in invalid:
            self.deliver(self.key(key='A'),event,ok=False)
            self.execute('assert(#game:GetService("UserInputService"):GetKeysPressed()==0)')
        self.deliver(*[self.key(key='W')]*129,ok=False)
        self.deliver(self.key(),self.key(),ok=False)
        self.deliver(self.key())
        self.deliver(self.key(),ok=False)
        self.deliver(self.key('cancel'))
        self.execute('assert(#game:GetService("UserInputService"):GetKeysPressed()==0)')

    def test_active_contact_limit_and_reclaim(self):
        self.deliver(*[{'phase':'begin','input_type':'Touch','input_id':str(i)} for i in range(128)])
        self.deliver(self.key(),ok=False)
        self.deliver(*[{'phase':'end','input_type':'Touch','input_id':str(i)} for i in range(128)])
        self.deliver(self.key())
        self.execute('assert(game:GetService("UserInputService"):IsKeyDown("W"))')

    def test_disabled_device_rejects_input_without_fabricating_capabilities(self):
        self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                                             'input_capabilities':{'KeyboardEnabled':False}}})
        self.deliver(self.key(),ok=False)
        self.execute('''local u=game:GetService("UserInputService")
assert(not u.KeyboardEnabled and not u:IsKeyDown("W") and u:GetLastInputType()==Enum.UserInputType.None)''')

    def test_disconnecting_device_cancels_pressed_objects_and_clears_state(self):
        self.execute('''local u=game:GetService("UserInputService");cancelCount=0
u.InputEnded:Connect(function(i,p)
 assert(i.UserInputState==Enum.UserInputState.Cancel and not p);cancelCount+=1
end)''')
        self.deliver(self.key(),{'phase':'begin','input_type':'MouseButton1'})
        result=self.host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'actions':[],
            'input_capabilities':{'KeyboardEnabled':False,'MouseEnabled':False}}})
        self.assertTrue(result['ok'],result.get('error'))
        self.execute('''local u=game:GetService("UserInputService")
assert(cancelCount==2 and #u:GetKeysPressed()==0 and #u:GetMouseButtonsPressed()==0)
assert(not u:IsKeyDown("W") and not u:IsMouseButtonPressed("MouseButton1"))''')

    def test_authored_keyboard_script_moves_a_real_part_and_cleans_up(self):
        source=(ROOT/'runtime/scripts/keyboard_control_demo.luau').read_text()
        started=self.host.call({'op':'execute','source':source,'timeout_ms':10})
        self.assertTrue(started['ok'],started.get('error'))
        self.assertFalse(started.get('completed'))
        job=started['job_id'];results=[]
        response=self.deliver(self.key())
        results.extend(response.get('completed_jobs',[]))
        deadline=time.monotonic()+3
        while not any(r['job_id']==job for r in results) and time.monotonic()<deadline:
            time.sleep(.02)
            response=self.host.call({'op':'poll','timeout_ms':100})
            self.assertTrue(response['ok'],response.get('error'))
            self.assertFalse(response.get('task_errors'))
            results.extend(response.get('completed_jobs',[]))
        done=next((r for r in results if r['job_id']==job),None)
        self.assertIsNotNone(done,'keyboard demo did not finish')
        self.assertTrue(done['ok'],done.get('error'))
        self.assertEqual(done['values'][:2],['Keyboard moved a local part',4])
        self.assertGreater(done['values'][2],0)
        self.execute('assert(workspace:FindFirstChild("KeyboardControlDemo")==nil)')

    @unittest.skipUnless(motion.STANDALONE.is_file(),'build standalone host first')
    def test_standalone_input_apis_and_enum_serialization(self):
        source='''local u=game:GetService("UserInputService")
assert(u:GetLastInputType()==Enum.UserInputType.None and #u:GetKeysPressed()==0)
assert(u:GetMouseLocation()==Vector2.zero and u:GetMouseDelta()==Vector2.zero)
assert(client.propertyOrigin(u,"MouseBehavior")=="local_input")
local changes=0
u:GetPropertyChangedSignal("MouseBehavior"):Connect(function()changes+=1 end)
u.MouseBehavior=Enum.MouseBehavior.LockCurrentPosition
assert(u.MouseBehavior==Enum.MouseBehavior.LockCurrentPosition)
task.wait(.005);assert(changes==1)
return Enum.KeyCode.W,Enum.UserInputType.MouseButton1,Enum.UserInputState.Begin
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'source.luau').write_text(source)
            (path/'world.json').write_text(json.dumps(motion.SNAPSHOT))
            process=subprocess.run([str(motion.STANDALONE),'--source',str(path/'source.luau'),
                '--world-snapshot',str(path/'world.json'),'--timeout-ms','1000'],capture_output=True,timeout=10)
            result=json.loads(process.stdout)
        self.assertEqual(process.returncode,0,result.get('error'))
        self.assertTrue(result['ok'],result.get('error'))
        self.assertFalse(result.get('task_errors'))
        self.assertEqual(result['values'],[
            {'$enum':{'type':'KeyCode','name':'W','value':119}},
            {'$enum':{'type':'UserInputType','name':'MouseButton1','value':0}},
            {'$enum':{'type':'UserInputState','name':'Begin','value':0}}])

    def test_session_owner_adapter_delivers_real_events(self):
        world,_=fixture()
        runtime=PersistentClientScriptRuntime(lambda:world,123,input_capabilities={'KeyboardEnabled':True})
        self.addCleanup(runtime.close)
        result=runtime.deliver_input([self.key()])
        self.assertTrue(result['ok'],result.get('error'))
        result=runtime.host.call({'op':'execute','source':'return game:GetService("UserInputService"):IsKeyDown("W")','timeout_ms':100})
        self.assertEqual(result['values'],[True])

if __name__=='__main__':unittest.main()
