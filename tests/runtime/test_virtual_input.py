"""Virtual input through real local input state, GUI routing, and callbacks."""
import unittest

import test_motion_math as motion
import test_script_execution as scripts
from persistent_runtime import PersistentLuauHost


class VirtualInputTests(unittest.TestCase):
    execute = motion.MotionMathTests.execute

    def setUp(self):
        data = scripts.snapshot()
        data['display'] = {'size': [640, 480], 'device_insets': [0, 0, 0, 0],
                           'core_insets': [0, 0, 0, 0]}
        self.host = PersistentLuauHost(data)
        self.addCleanup(self.host.close)

    def test_service_identity_keyboard_state_callbacks_and_context_actions(self):
        self.execute('''
local v=game:GetService("VirtualInputManager")
assert(v==game:GetService("VirtualInputManager") and v==game:FindService("VirtualInputManager"))
assert(v.ClassName=="VirtualInputManager" and v.Parent==game)
assert(not pcall(Instance.new,"VirtualInputManager"))
local u=game:GetService("UserInputService")
assert(not u.KeyboardEnabled)
local log,actions={},{};local input
u.InputBegan:Connect(function(i,p)
    assert(not p and i.KeyCode==Enum.KeyCode.W and i.UserInputState==Enum.UserInputState.Begin)
    assert(u:IsKeyDown(Enum.KeyCode.W));input=i;table.insert(log,"begin")
end)
u.InputChanged:Connect(function(i)assert(i==input);table.insert(log,"repeat")end)
u.InputEnded:Connect(function(i,p)
    assert(i==input and not p and i.UserInputState==Enum.UserInputState.End)
    assert(not u:IsKeyDown(Enum.KeyCode.W));table.insert(log,"end")
end)
game:GetService("ContextActionService"):BindAction("virtual-w",function(_,state,i)
    assert(i.KeyCode==Enum.KeyCode.W);table.insert(actions,state)
    return Enum.ContextActionResult.Pass
end,false,Enum.KeyCode.W)
v:SendKeyEvent(true,Enum.KeyCode.W,false,game)
assert(u:IsKeyDown("W") and #u:GetKeysPressed()==1)
assert(select("#",v:WaitForInputEventsProcessed())==0)
assert(log[1]=="begin" and actions[1]==Enum.UserInputState.Begin)
v:SendKeyEvent(true,Enum.KeyCode.W,false,nil)
v:SendKeyEvent(true,Enum.KeyCode.W,true,nil)
v:WaitForInputEventsProcessed()
assert(log[2]=="repeat" and #log==2)
v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
v:WaitForInputEventsProcessed()
assert(table.concat(log,",")=="begin,repeat,end" and #u:GetKeysPressed()==0)
assert(actions[#actions]==Enum.UserInputState.End and not u.KeyboardEnabled)
v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
''')

    def test_mouse_movement_delta_buttons_and_wheel(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
u.MouseBehavior=Enum.MouseBehavior.LockCenter
local movement,wheels={},{}
u.InputChanged:Connect(function(i)
    if i.UserInputType==Enum.UserInputType.MouseMovement then
        table.insert(movement,{i.Position,i.Delta})
    elseif i.UserInputType==Enum.UserInputType.MouseWheel then table.insert(wheels,i.Position.Z) end
end)
v:SendMouseMoveEvent(30,40,game);v:WaitForInputEventsProcessed()
assert(u:GetMouseLocation()==Vector2.new(30,40))
assert(movement[1][1]==Vector3.new(30,40,0) and movement[1][2]==Vector3.new(30,40,0))
v:SendMouseMoveDeltaEvent(5,-7,nil);v:WaitForInputEventsProcessed()
assert(u:GetMouseLocation()==Vector2.new(35,33))
assert(movement[2][2]==Vector3.new(5,-7,0))
for button=0,2 do
    v:SendMouseButtonEvent(35,33,button,true,game,0)
    assert(u:IsMouseButtonPressed(Enum.UserInputType:FromValue(button)))
    v:WaitForInputEventsProcessed()
    v:SendMouseButtonEvent(35,33,button,false,game,0)
    v:WaitForInputEventsProcessed()
    assert(not u:IsMouseButtonPressed(Enum.UserInputType:FromValue(button)))
end
v:SendMouseWheelEvent(35,33,true,nil);v:WaitForInputEventsProcessed()
v:SendMouseWheelEvent(35,33,false,nil);v:WaitForInputEventsProcessed()
assert(wheels[1]==1 and wheels[2]==-1)
assert(#u:GetMouseButtonsPressed()==0 and not u.MouseEnabled)
''')

    def test_input_barrier_inside_an_action_handler_does_not_deadlock(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local completed=false
game:GetService("ContextActionService"):BindAction("wait-inside",function(_,state)
    if state==Enum.UserInputState.Begin then v:WaitForInputEventsProcessed();completed=true end
    return Enum.ContextActionResult.Pass
end,false,Enum.KeyCode.E)
v:SendKeyEvent(true,Enum.KeyCode.E,false,nil)
v:WaitForInputEventsProcessed()
for _=1,100 do if completed then break end;task.wait()end
assert(completed,"input barrier must not wait for a yielding user callback to finish")
v:SendKeyEvent(false,Enum.KeyCode.E,false,nil)
''')

    def test_touch_lifecycle_identity_delta_and_cancel(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local old,ended;local log={}
u.TouchStarted:Connect(function(i,p)assert(not p and i.Position==Vector3.new(5,6,0));old=i;table.insert(log,"begin")end)
u.TouchMoved:Connect(function(i)assert(i==old and i.Delta==Vector3.new(3,4,0));table.insert(log,"move")end)
u.TouchEnded:Connect(function(i)assert(i==old and i.UserInputState==Enum.UserInputState.Cancel);ended=true;table.insert(log,"cancel")end)
v:SendTouchEvent(17,0,5,6);v:WaitForInputEventsProcessed()
old.Position=Vector3.new(999,999,0) -- Editing the userdata cannot move the virtual device.
v:SendTouchEvent(17,1,8,10);v:WaitForInputEventsProcessed()
v:SendTouchEvent(17,3,8,10);v:WaitForInputEventsProcessed()
assert(ended and table.concat(log,",")=="begin,move,cancel" and not u.TouchEnabled)
assert(not pcall(function()v:SendTouchEvent(17,2,8,10)end))
''')

    def test_gui_hit_testing_click_capture_scope_and_disabled_buttons(self):
        self.execute('''
local v=game:GetService("VirtualInputManager")
local gui=game.Players.LocalPlayer.PlayerGui
local screen=Instance.new("ScreenGui",gui);screen.IgnoreGuiInset=true
local other=Instance.new("ScreenGui",gui);other.IgnoreGuiInset=true
local button=Instance.new("TextButton",screen);button.Size=UDim2.fromOffset(100,60);button.Position=UDim2.fromOffset(20,20)
local high=Instance.new("TextButton",screen);high.Size=button.Size;high.Position=button.Position;high.ZIndex=2
local lowClicks,highClicks=0,0
button.MouseButton1Click:Connect(function()lowClicks+=1 end)
high.Activated:Connect(function(input,count)
    assert(input.UserInputType==Enum.UserInputType.MouseButton1 and count==1);highClicks+=1
end)
local function click(layer,x,y)
    v:SendMouseButtonEvent(x,y,0,true,layer,0);v:WaitForInputEventsProcessed()
    v:SendMouseButtonEvent(x,y,0,false,layer,0);v:WaitForInputEventsProcessed()
end
click(game,30,30);assert(highClicks==1 and lowClicks==0)
click(other,30,30);assert(highClicks==1 and lowClicks==0)
high.Visible=false;click(screen,30,30);assert(lowClicks==1)
button.Interactable=false;click(game,30,30);assert(lowClicks==1)
button.Interactable=true
v:SendMouseButtonEvent(30,30,0,true,game,0);v:WaitForInputEventsProcessed()
v:SendMouseMoveEvent(400,300,game);v:WaitForInputEventsProcessed()
v:SendMouseButtonEvent(400,300,0,false,game,0);v:WaitForInputEventsProcessed()
assert(lowClicks==1)
screen:Destroy();other:Destroy()
''')

    def test_gui_wheel_routes_once_and_scrolls(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui);screen.IgnoreGuiInset=true
local frame=Instance.new("ScrollingFrame",screen);frame.Position=UDim2.fromOffset(10,10)
frame.Size=UDim2.fromOffset(150,100);frame.CanvasSize=UDim2.fromOffset(150,500);frame.Active=true
local guiCount,inputCount=0,0
frame.MouseWheelBackward:Connect(function(x,y)assert(x==40 and y==40);guiCount+=1 end)
u.InputChanged:Connect(function(i,processed)
    if i.UserInputType==Enum.UserInputType.MouseWheel then assert(processed);inputCount+=1 end
end)
v:SendMouseWheelEvent(40,40,false,game);v:WaitForInputEventsProcessed()
assert(guiCount==1 and inputCount==1 and frame.CanvasPosition.Y>0)
screen:Destroy()
''')

    def test_text_entry_replaces_utf8_selection_and_honors_focus(self):
        self.execute('''
local v=game:GetService("VirtualInputManager")
local screen=Instance.new("ScreenGui",game.Players.LocalPlayer.PlayerGui)
local box=Instance.new("TextBox",screen);box.Size=UDim2.fromOffset(200,50);box.ClearTextOnFocus=false
box.Text="abCD";box:CaptureFocus();box.CursorPosition=3;box.SelectionStart=5
v:SendTextInputCharacterEvent("🙂",game);v:WaitForInputEventsProcessed()
assert(box.Text=="ab🙂" and box.CursorPosition==7 and box.SelectionStart==-1)
box.TextEditable=false;v:SendTextInputCharacterEvent("ignored",nil);assert(box.Text=="ab🙂")
box.TextEditable=true;box:ReleaseFocus();v:SendTextInputCharacterEvent("ignored",nil)
assert(box.Text=="ab🙂")
assert(not pcall(function()v:SendTextInputCharacterEvent(string.char(255),nil)end))
screen:Destroy()
''')

    def test_invalid_arguments_and_stale_receivers_do_not_mutate_input(self):
        self.execute('''
local v=game:GetService("VirtualInputManager");local u=game:GetService("UserInputService")
local cases={
    function()v:SendKeyEvent(1,Enum.KeyCode.W,false,game)end,
    function()v:SendKeyEvent(true,Enum.UserInputType.Keyboard,false,game)end,
    function()v:SendKeyEvent(true,Enum.KeyCode.W,false,{})end,
    function()v:SendMouseMoveEvent(0/0,10,game)end,
    function()v:SendMouseMoveDeltaEvent(math.huge,10,game)end,
    function()v:SendMouseButtonEvent(1,2,3,true,nil,0)end,
    function()v:SendMouseButtonEvent(1,2,0,true,nil,-1)end,
    function()v:SendMouseWheelEvent(1,2,"forward",nil)end,
    function()v:SendTouchEvent(1,9,1,2)end,
    function()v:SendTouchEvent(.5,0,1,2)end,
    function()v.SendKeyEvent(workspace,true,Enum.KeyCode.W,false,nil)end,
}
for _,fn in cases do assert(not pcall(fn))end
assert(#u:GetKeysPressed()==0 and #u:GetMouseButtonsPressed()==0 and u:GetMouseLocation()==Vector2.zero)
local fn=v.SendMouseMoveEvent;v:Destroy();assert(not pcall(fn,v,10,10,nil))
''')

    def test_synthetic_input_survives_hardware_detach_without_faking_capabilities(self):
        self.execute('game:GetService("VirtualInputManager"):SendKeyEvent(true,Enum.KeyCode.W,false,nil)')
        update = {'version': 1, 'delta': True, 'actions': [],
                  'input_capabilities': {'KeyboardEnabled': False, 'MouseEnabled': False}}
        self.assertTrue(self.host.call({'op': 'update', 'delta': update})['ok'])
        self.execute('local u=game:GetService("UserInputService");assert(u:IsKeyDown("W") and not u.KeyboardEnabled)')
        rejected = self.host.call({'op': 'update', 'delta': {'version': 1, 'delta': True, 'actions': [],
            'input_events': [{'phase': 'begin', 'input_type': 'Keyboard', 'key_code': 'A'}]}})
        self.assertFalse(rejected['ok'])
        self.execute('''local v=game:GetService("VirtualInputManager");v:SendKeyEvent(false,Enum.KeyCode.W,false,nil)
assert(not game:GetService("UserInputService"):IsKeyDown("W"))''')
