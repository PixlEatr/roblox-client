"""Virtual devices and recording execute through the persistent native VM."""
import json
from pathlib import Path
import unittest

import test_virtual_input as base
import test_cframe_transforms as native


class VirtualInputExtendedTests(unittest.TestCase):
    setUp=base.VirtualInputTests.setUp
    execute=base.VirtualInputTests.execute

    def test_both_native_hosts_deliver_devices_and_recording(self):
        native.CFrameTransformTests.run_script(self,'''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local recording,done,begins
begins=0;u.InputBegan:Connect(function(i)if i.KeyCode==Enum.KeyCode.W then begins+=1 end end)
v.RecordingCompleted:Connect(function(s)recording=s end)
v.PlaybackCompleted:Connect(function()done=true end)
v:StartRecording();v:SendKeyEvent(true,Enum.KeyCode.W,false,nil)
v:WaitForInputEventsProcessed();v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
v:StopRecording();v:WaitForInputEventsProcessed();v:StartPlayingJSON(recording)
for _=1,100 do if done then break end;task.wait(.01)end
assert(done and begins==2 and not u:IsKeyDown("W"))
v:HandleGamepadConnect(10);v:HandleGamepadAxisInput(10,Enum.KeyCode.Thumbstick1,.5,0,0)
assert(u:GetLastInputType()==Enum.UserInputType.Gamepad1)
v:HandleGamepadDisconnect(10);assert(not u.GamepadEnabled)
v:SendGravityEvent(0,-1,0);vec(u:GetDeviceGravity().Position,Vector3.new(0,-1,0))
''')

    def test_pinned_api_surface_is_callable(self):
        root=Path(__file__).resolve().parents[2]
        api=json.loads((root/'vendor/roblox-player-scripts-745/API-Dump.json').read_text())
        cls=next(c for c in api['Classes'] if c['Name']=='VirtualInputManager')
        methods=[m['Name'] for m in cls['Members'] if m['MemberType']=='Function']
        self.execute('local v=game:GetService("VirtualInputManager")\n'+
                     '\n'.join('assert(type(v['+json.dumps(name)+'])=="function",'+json.dumps(name)+')' for name in methods))

    def test_gamepads_queries_events_axes_and_disconnect_release(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local p=Enum.UserInputType.Gamepad1
local connected,disconnected,ended=0,0,0
u.GamepadConnected:Connect(function(g) assert(g==p);connected+=1 end)
u.GamepadDisconnected:Connect(function(g)assert(g==p);disconnected+=1 end)
u.InputEnded:Connect(function(i)if i.UserInputType==p then ended+=1 end end)
assert(not u.GamepadEnabled and #u:GetConnectedGamepads()==0)
v:HandleGamepadConnect(42);v:WaitForInputEventsProcessed()
assert(connected==1 and u.GamepadEnabled and u:GetGamepadConnected(p))
assert(#u:GetGamepadState(p)==18 and #u:GetSupportedGamepadKeyCodes(p)==18)
assert(u:GamepadSupports(p,Enum.KeyCode.ButtonA) and not u:GamepadSupports(p,Enum.KeyCode.W))
assert(u:GetConnectedGamepads()[1]==p and u:IsNavigationGamepad(p))
u:SetNavigationGamepad(p,false);assert(#u:GetNavigationGamepads()==0)
u:SetNavigationGamepad(p,true)
local action
game:GetService("ContextActionService"):BindAction("pad",function(_,state,i)
 action={state,i};return Enum.ContextActionResult.Pass
end,false,Enum.KeyCode.ButtonA)
v:HandleGamepadButtonInput(42,Enum.KeyCode.ButtonA,0);v:WaitForInputEventsProcessed()
assert(u:IsGamepadButtonDown(p,Enum.KeyCode.ButtonA))
assert(action[1]==Enum.UserInputState.Begin and action[2].UserInputType==p)
v:HandleGamepadAxisInput(42,Enum.KeyCode.Thumbstick1,.5,-.75,0);v:WaitForInputEventsProcessed()
local thumb
for _,i in u:GetGamepadState(p) do if i.KeyCode==Enum.KeyCode.Thumbstick1 then thumb=i end end
assert(thumb.Position==Vector3.new(.5,-.75,0) and thumb.UserInputState==Enum.UserInputState.Change)
thumb.Position=Vector3.new(99,99,99)
v:HandleGamepadAxisInput(42,Enum.KeyCode.Thumbstick1,.25,-.5,0)
assert(thumb.Delta==Vector3.new(-.25,.25,0))
v:HandleGamepadDisconnect(42);v:WaitForInputEventsProcessed()
assert(disconnected==1 and ended==2 and not u.GamepadEnabled and #u:GetGamepadState(p)==0)
assert(not u:IsGamepadButtonDown(p,Enum.KeyCode.ButtonA))
''')

    def test_sensor_state_and_quaternion_rotation(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local acceleration,gravity,rotation,cf
u.DeviceAccelerationChanged:Connect(function(i)acceleration=i end)
u.DeviceGravityChanged:Connect(function(i)gravity=i end)
u.DeviceRotationChanged:Connect(function(i,c)rotation=i;cf=c end)
v:SendAccelerometerEvent(1,2,3);v:SendGravityEvent(0,-1,0)
v:SendGyroscopeEvent(0,math.sin(math.pi/4),0,math.cos(math.pi/4));v:WaitForInputEventsProcessed()
assert(acceleration==u:GetDeviceAcceleration() and acceleration.Position==Vector3.new(1,2,3))
assert(gravity==u:GetDeviceGravity() and gravity.Position==Vector3.new(0,-1,0))
local r,c=u:GetDeviceRotation();assert(r==rotation)
assert((c.LookVector-Vector3.new(-1,0,0)).Magnitude<1e-5 and c==cf)
assert(not u.AccelerometerEnabled and not u.GyroscopeEnabled)
assert(not pcall(function()v:SendGyroscopeEvent(0,0,0,0)end))
assert(not pcall(function()v:SendGravityEvent(0/0,0,0)end))
''')

    def test_filter_allows_releases_and_restores_new_input(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
v:SendKeyEvent(true,Enum.KeyCode.W,false,nil)
v:SetInputTypesToIgnore({Enum.UserInputType.Keyboard,Enum.UserInputType.MouseMovement})
v:SendKeyEvent(true,Enum.KeyCode.A,false,nil);assert(not u:IsKeyDown("A"))
v:SendKeyEvent(false,Enum.KeyCode.W,false,nil);assert(not u:IsKeyDown("W"))
v:SendMouseMoveEvent(50,60,nil);assert(u:GetMouseLocation()==Vector2.zero)
assert(not pcall(function()v:SetInputTypesToIgnore({Enum.KeyCode.W})end))
v:SetInputTypesToIgnore({});v:SendKeyEvent(true,Enum.KeyCode.A,false,nil)
assert(u:IsKeyDown("A"));v:SendKeyEvent(false,Enum.KeyCode.A,false,nil)
''')

    def test_scroll_updates_canvas_and_input_callbacks(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui);screen.IgnoreGuiInset=true
local box=Instance.new("ScrollingFrame",screen);box.Size=UDim2.fromOffset(200,200)
box.CanvasSize=UDim2.fromOffset(600,600);box.ScrollBarThickness=0
local event,processed
u.InputChanged:Connect(function(i,p)if i.UserInputType==Enum.UserInputType.MouseWheel then event=i;processed=p end end)
v:SendScroll(50,60,30,80,{},screen);v:WaitForInputEventsProcessed()
assert(box.CanvasPosition==Vector2.new(30,80))
assert(event.Delta==Vector3.new(30,80,-2) and processed)
assert(u:GetMouseLocation()==Vector2.new(50,60))
''')

    def test_recording_replay_and_workspace_file_preserve_callback_order(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local recording,completed;local log={}
v.AdditionalLuaState="checkpoint ✓"
v.RecordingCompleted:Connect(function(s)recording=s end)
v.PlaybackCompleted:Connect(function(s)completed=s end)
u.InputBegan:Connect(function(i)if i.KeyCode==Enum.KeyCode.W then table.insert(log,"down")end end)
u.InputEnded:Connect(function(i)if i.KeyCode==Enum.KeyCode.W then table.insert(log,"up")end end)
v:StartRecording()
v:SendKeyEvent(true,Enum.KeyCode.W,false,nil);v:WaitForInputEventsProcessed()
v:SendKeyEvent(false,Enum.KeyCode.W,false,nil);v:WaitForInputEventsProcessed()
v:sendRobloxEvent("test","marker","local");v:sendThemeChangeEvent("Dark")
v:StopRecording();v:WaitForInputEventsProcessed()
assert(type(recording)=="string")
local parsed=game:GetService("HttpService"):JSONDecode(recording)
assert(parsed.format=="rove.virtual-input" and parsed.version==1 and #parsed.events==4)
assert(parsed.additionalLuaState=="checkpoint ✓")
log={};v:StartPlayingJSON(recording)
for _=1,100 do if completed then break end;task.wait(.01)end
assert(completed=="checkpoint ✓" and table.concat(log,",")=="down,up" and not u:IsKeyDown("W"))
writefile("recordings/input.json",recording);completed=nil;log={}
v:StartPlaying("recordings/input.json")
for _=1,100 do if completed then break end;task.wait(.01)end
assert(completed and table.concat(log,",")=="down,up")
assert(select("#",v:Dump())==0)
''')

    def test_stop_playback_cancels_its_keys_but_preserves_existing_input(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local recording
v.RecordingCompleted:Connect(function(s)recording=s end)
v:StartRecording();v:SendKeyEvent(true,Enum.KeyCode.W,false,nil);v:StopRecording()
v:WaitForInputEventsProcessed();v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
local doc=game:GetService("HttpService"):JSONDecode(recording);doc.duration=10
v:SendKeyEvent(true,Enum.KeyCode.A,false,nil)
v:StartPlayingJSON(game:GetService("HttpService"):JSONEncode(doc))
for _=1,100 do if u:IsKeyDown("W")then break end;task.wait(.01)end
assert(u:IsKeyDown("W"));v:StopPlaying();v:WaitForInputEventsProcessed()
assert(not u:IsKeyDown("W") and u:IsKeyDown("A"))
v:SendKeyEvent(false,Enum.KeyCode.A,false,nil)
''')

    def test_invalid_recordings_and_devices_leave_state_unchanged(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
assert(not pcall(function()v:StartPlayingJSON("{}")end))
assert(not pcall(function()v:StartPlayingJSON("not json")end))
assert(not pcall(function()v:StartPlaying("missing.json")end))
assert(not pcall(function()v:HandleGamepadConnect(-1)end))
assert(not pcall(function()v:HandleGamepadAxisInput(123,Enum.KeyCode.Thumbstick1,1,0,0)end))
assert(#u:GetConnectedGamepads()==0 and #u:GetKeysPressed()==0)
v:StartRecording();assert(not pcall(function()v:StartRecording()end));v:StopRecording()
for i=1,8 do v:HandleGamepadConnect(i)end
assert(not pcall(function()v:HandleGamepadConnect(9)end))
assert(#u:GetConnectedGamepads()==8)
for i=1,8 do v:HandleGamepadDisconnect(i)end
assert(#u:GetConnectedGamepads()==0)
''')

    def test_textbox_editing_keys_use_utf8_boundaries_and_submission(self):
        self.execute('''
local v=game:GetService("VirtualInputManager")
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui)
local text=Instance.new("TextBox",screen);text.Size=UDim2.fromOffset(200,50)
text:CaptureFocus();v:SendTextInputCharacterEvent("aé🙂z",nil)
local function key(k)
 v:SendKeyEvent(true,k,false,nil);v:SendKeyEvent(false,k,false,nil)
end
key(Enum.KeyCode.Left);key(Enum.KeyCode.Backspace);assert(text.Text=="aéz")
key(Enum.KeyCode.Home);key(Enum.KeyCode.Delete);assert(text.Text=="éz")
v:SendKeyEvent(true,Enum.KeyCode.LeftControl,false,nil)
key(Enum.KeyCode.A);v:SendKeyEvent(false,Enum.KeyCode.LeftControl,false,nil)
v:SendTextInputCharacterEvent("replacement",nil);assert(text.Text=="replacement")
local submitted
text.FocusLost:Connect(function(enter,input)submitted=enter;assert(input.KeyCode==Enum.KeyCode.Return)end)
key(Enum.KeyCode.Return);v:WaitForInputEventsProcessed()
assert(submitted and not text:IsFocused())
''')

    def test_entire_recording_is_validated_before_start(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local doc={format="rove.virtual-input",version=1,duration=0,additionalLuaState="",events={
 {time=0,method="SendKeyEvent",args={true,"W",false}},
 {time=0,method="SendMouseMoveEvent",args={"bad",0}}
}}
assert(not pcall(function()v:StartPlayingJSON(game:GetService("HttpService"):JSONEncode(doc))end))
v:WaitForInputEventsProcessed();assert(not u:IsKeyDown("W"))
assert(not pcall(function()v:SetInputTypesToIgnore({Keyboard=true})end))
''')

    def test_playback_does_not_release_a_key_that_was_already_held(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local finished=false;v.PlaybackCompleted:Connect(function()finished=true end)
local doc={format="rove.virtual-input",version=1,duration=0,additionalLuaState="",events={
 {time=0,method="SendKeyEvent",args={true,"W",false}},
 {time=0,method="SendKeyEvent",args={false,"W",false}}
}}
v:SendKeyEvent(true,Enum.KeyCode.W,false,nil)
v:StartPlayingJSON(game:GetService("HttpService"):JSONEncode(doc))
for _=1,100 do if finished then break end;task.wait(.01)end
assert(finished and u:IsKeyDown("W"));v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
''')

    def test_real_host_input_is_recorded_and_can_be_replayed_without_hardware(self):
        self.execute('''
getgenv().v=game:GetService("VirtualInputManager")
v.RecordingCompleted:Connect(function(value)getgenv().hostRecording=value end)
v:StartRecording()
''')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_capabilities':{'KeyboardEnabled':True},
            'input_events':[{'phase':'begin','input_type':'Keyboard','key_code':'Q'},
                            {'phase':'end','input_type':'Keyboard','key_code':'Q'}]}})
        self.assertTrue(result['ok'],result)
        self.execute('v:StopRecording();v:WaitForInputEventsProcessed();assert(type(hostRecording)=="string")')
        self.assertTrue(self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_capabilities':{'KeyboardEnabled':False}}})['ok'])
        self.execute('''
local count=0;local complete=false
game:GetService("UserInputService").InputBegan:Connect(function(i)if i.KeyCode==Enum.KeyCode.Q then count+=1 end end)
v.PlaybackCompleted:Connect(function()complete=true end);v:StartPlayingJSON(hostRecording)
for _=1,100 do if complete then break end;task.wait(.01)end
assert(complete and count==1 and not game:GetService("UserInputService").KeyboardEnabled)
''')

    def test_scroll_gesture_momentum_keeps_target_and_rejects_unknown_flags(self):
        self.execute('''
local v=game:GetService("VirtualInputManager")
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui);screen.IgnoreGuiInset=true
local box=Instance.new("ScrollingFrame",screen);box.Size=UDim2.fromOffset(100,100)
box.CanvasSize=UDim2.fromOffset(500,500);box.ScrollBarThickness=0
v:SendScroll(20,20,0,40,{StartOfScroll=true,IsPartOfGesture=true},screen)
v:SendScroll(400,400,0,30,{IsResultOfMomentum=true},screen)
assert(box.CanvasPosition.Y==70)
assert(not pcall(function()v:SendScroll(20,20,0,10,{UnknownFlag=true},screen)end))
assert(not pcall(function()v:SendScroll(20,20,0,10,{StartOfScroll="yes"},screen)end))
assert(box.CanvasPosition.Y==70)
v:SendScroll(400,400,0,30,{StartOfScroll=true},screen)
assert(box.CanvasPosition.Y==70)
''')

    def test_gamepad_recording_replays_and_disconnects_its_device(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local recording,completed
v.RecordingCompleted:Connect(function(s)recording=s end)
v.PlaybackCompleted:Connect(function()completed=true end)
v:StartRecording();v:HandleGamepadConnect(80)
v:HandleGamepadButtonInput(80,Enum.KeyCode.ButtonA,0);v:WaitForInputEventsProcessed()
v:HandleGamepadDisconnect(80);v:WaitForInputEventsProcessed()
v:SendGravityEvent(0,-1,0);v:SetInputTypesToIgnore({});v:StopRecording();v:WaitForInputEventsProcessed()
local beginCount=0
u.InputBegan:Connect(function(i)if i.KeyCode==Enum.KeyCode.ButtonA then beginCount+=1 end end)
v:StartPlayingJSON(recording)
for _=1,100 do if completed then break end;task.wait(.01)end
assert(completed and beginCount==1 and #u:GetConnectedGamepads()==0)
''')

    def test_ignored_host_press_and_release_are_both_dropped(self):
        self.execute('game:GetService("VirtualInputManager"):SetInputTypesToIgnore({Enum.UserInputType.Keyboard})')
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_capabilities':{'KeyboardEnabled':True},
            'input_events':[{'phase':'begin','input_type':'Keyboard','key_code':'Q'},
                            {'phase':'end','input_type':'Keyboard','key_code':'Q'}]}})
        self.assertTrue(result['ok'],result)
        self.execute('assert(#game:GetService("UserInputService"):GetKeysPressed()==0)')

    def test_relative_mouse_recording_restores_its_starting_position(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local recording,done
v.RecordingCompleted:Connect(function(s)recording=s end)
v.PlaybackCompleted:Connect(function()done=true end)
v:SendMouseMoveEvent(20,30,nil);v:StartRecording()
v:SendMouseMoveDeltaEvent(5,-7,nil);v:StopRecording();v:WaitForInputEventsProcessed()
v:SendMouseMoveEvent(500,400,nil);v:StartPlayingJSON(recording)
for _=1,100 do if done then break end;task.wait(.01)end
assert(done and u:GetMouseLocation()==Vector2.new(25,23))
''')

    def test_browser_text_edit_recording_rebases_focus_revisions(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui)
getgenv().recordBox=Instance.new("TextBox",screen);recordBox.Name="RecordBox";recordBox.Size=UDim2.fromOffset(200,40)
getgenv().v=game:GetService("VirtualInputManager")
v.RecordingCompleted:Connect(function(s)getgenv().recordedText=s end)
v:StartRecording()
''')
        frame=self.host.call({'op':'render'})['frame']
        target=next(n['id'] for n in frame['gui'] if n['name']=='RecordBox')
        result=self.host.call({'op':'gui_action','action':{'kind':'text_focus','id':target}})
        self.assertTrue(result['ok'],result)
        frame=self.host.call({'op':'render'})['frame']
        result=self.host.call({'op':'gui_action','action':{'kind':'text_edit','id':target,
            'text':'captured text','revision':frame['textFocus']['revision']}})
        self.assertTrue(result['ok'],result)
        self.execute('''
v:StopRecording();v:WaitForInputEventsProcessed()
recordBox:ReleaseFocus();recordBox.Text="reset"
local completed=false;v.PlaybackCompleted:Connect(function()completed=true end)
v:StartPlayingJSON(recordedText)
for _=1,100 do if completed then break end;task.wait(.01)end
assert(completed and recordBox:IsFocused() and recordBox.Text=="captured text")
''')


if __name__=='__main__':unittest.main()
