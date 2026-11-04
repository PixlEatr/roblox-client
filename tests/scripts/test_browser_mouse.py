"""Pointer backend and real HTTP/socket/Luau mouse events."""
import copy
import math
from pathlib import Path
import unittest
from browser_input import validate_keyboard_request
import test_browser_input as keyboard


def mouse(kind='MouseMovement',phase='change',x=20,y=30,z=0,delta=None):
    return {'input_type':kind,'phase':phase,'position':[x,y,z],
            'delta':delta if delta is not None else [0,0,0],'mouse_location':[x,y]}


class BrowserMouseTests(unittest.TestCase):
    setUp=keyboard.BrowserKeyboardTests.setUp
    send=keyboard.BrowserKeyboardTests.send
    events=keyboard.BrowserKeyboardTests.events

    def test_buttons_motion_wheel_and_keyboard_share_ordered_backend(self):
        self.send('attach');self.assertTrue(self.runtime.input_capabilities['MouseEnabled'])
        self.events({'phase':'begin','key_code':'W'},mouse('MouseButton2','begin'))
        self.events(mouse(x=35,y=50,delta=[15,20,0]),mouse('MouseWheel',z=1,delta=[0,0,1]))
        self.assertEqual(self.keyboard.held,{'W'});self.assertEqual(self.keyboard.mouse_held,{'MouseButton2'})
        self.assertEqual(self.runtime.calls[-1][0]['delta'],[15,20,0])
        self.events(mouse('MouseButton2','end',x=35,y=50))
        self.assertEqual(self.keyboard.mouse_held,set())

    def test_release_and_expiry_cancel_all_buttons_and_restore_devices(self):
        self.runtime.input_capabilities['MouseEnabled']=True
        self.send('attach')
        self.events(*[mouse(n,'begin') for n in ['MouseButton1','MouseButton2','MouseButton3']])
        self.events(mouse(x=150,y=200,delta=[130,170,0]))
        self.now+=2.1;self.keyboard.poll()
        cancelled=self.runtime.calls[-1]
        self.assertEqual({e['input_type'] for e in cancelled},{'MouseButton1','MouseButton2','MouseButton3'})
        self.assertTrue(all(e['phase']=='cancel' and e['position']==[150,200,0] for e in cancelled))
        self.assertFalse(self.runtime.input_capabilities['KeyboardEnabled'])
        self.assertTrue(self.runtime.input_capabilities['MouseEnabled'])

    def test_invalid_pointer_batches_are_atomic_and_mouse_replay_is_idempotent(self):
        self.send('attach')
        cases=[mouse('MouseMovement','begin'),mouse('MouseButton1','change'),
               dict(mouse(),position=[1,2]),dict(mouse(),delta=[0,math.inf,0]),dict(mouse(),mouse_location=[True,2]),
               dict(mouse(),position=[1e20,2,0]),dict(mouse(),input_type='Touch'),dict(mouse(),source='return 1')]
        for bad in cases:
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.events(mouse('MouseButton1','begin'),bad)
            self.assertEqual(self.runtime.calls,[]);self.assertEqual(self.keyboard.mouse_held,set())
        with self.assertRaises(ValueError):self.events(mouse('MouseButton1','end'))
        self.events(mouse('MouseButton1','begin'))
        count=len(self.runtime.calls);self.keyboard.handle(copy.deepcopy(self.keyboard.last_request))
        self.assertEqual(len(self.runtime.calls),count)
        with self.assertRaises(ValueError):self.events(mouse('MouseButton1','begin'))
        self.assertEqual(self.keyboard.mouse_held,{'MouseButton1'})


class BrowserMouseHTTPTests(unittest.TestCase):
    setUp=keyboard.BrowserKeyboardHTTPTests.setUp
    stop_worker=keyboard.BrowserKeyboardHTTPTests.stop_worker
    request=keyboard.BrowserKeyboardHTTPTests.request
    source=keyboard.BrowserKeyboardHTTPTests.source
    send=keyboard.BrowserKeyboardHTTPTests.send

    def test_real_mouse_input_object_lifecycle_and_context_action(self):
        self.source('''mouseBegins=0;mouseEnds=0;mouseChanges=0;wheelSteps=0;mouseActionCount=0
local u=game:GetService("UserInputService")
u.InputBegan:Connect(function(i,p)
 assert(not p);mouseBegins+=1;savedMouse=i
 assert(i.KeyCode==Enum.KeyCode.None and i.UserInputType==Enum.UserInputType.MouseButton2)
end)
u.InputEnded:Connect(function(i)assert(i==savedMouse);mouseEnds+=1 end)
u.InputChanged:Connect(function(i)
 if i.UserInputType==Enum.UserInputType.MouseMovement then mouseChanges+=1;mouseDelta=i.Delta
 elseif i.UserInputType==Enum.UserInputType.MouseWheel then wheelSteps+=i.Position.Z end
end)
game:GetService("ContextActionService"):BindAction("right-action",function(_,s)
 if s==Enum.UserInputState.Begin then mouseActionCount+=1 end
end,false,Enum.UserInputType.MouseButton2)''')
        self.send('attach');self.send('events',events=[mouse('MouseButton2','begin',x=100,y=150)])
        self.assertEqual(self.source('''local u=game:GetService("UserInputService")
return mouseBegins,mouseActionCount,u:IsMouseButtonPressed(Enum.UserInputType.MouseButton2),u:GetMouseButtonsPressed()[1]==savedMouse''')['values'],[1,1,True,True])
        self.send('events',events=[mouse(x=125,y=170,delta=[25,20,0]),mouse('MouseWheel',x=125,y=170,z=-1,delta=[0,0,-1])])
        self.source('''assert(mouseChanges==1 and wheelSteps==-1 and mouseDelta==Vector3.new(25,20,0))
assert(savedMouse.Position==Vector3.new(100,150,0))
assert(game:GetService("UserInputService"):GetMouseLocation()==Vector2.new(125,170))''')
        self.send('events',events=[mouse('MouseButton2','end',x=125,y=170)])
        self.source('''assert(mouseEnds==1 and savedMouse.Position==Vector3.new(125,170,0))
assert(not game:GetService("UserInputService"):IsMouseButtonPressed("MouseButton2"))''')

    def test_sensitivity_scales_luau_motion_but_keeps_absolute_position_and_wheel(self):
        self.source('''local u=game:GetService("UserInputService");u.MouseDeltaSensitivity=.5
u.InputChanged:Connect(function(i)
 if i.UserInputType==Enum.UserInputType.MouseMovement then scaledDelta=i.Delta;scaledPosition=i.Position
 else scrollDelta=i.Position.Z end
end)''')
        self.send('attach');self.send('events',events=[mouse(x=90,y=80,delta=[8,-4,0]),mouse('MouseWheel',x=90,y=80,z=1,delta=[0,0,1])])
        self.source('assert(scaledDelta==Vector3.new(4,-2,0) and scaledPosition==Vector3.new(90,80,0) and scrollDelta==1)')

    def test_mouse_camera_demo_updates_real_camera_with_smoothing_and_restores_it(self):
        source=(Path(__file__).resolve().parents[2]/'runtime/scripts/browser_mouse_camera.luau').read_text()
        self.source('savedMouseCameraFrame=workspace.CurrentCamera.CFrame')
        self.source(source);self.send('attach')
        self.send('events',events=[mouse('MouseButton2','begin'),mouse(x=60,y=50,delta=[40,20,0])])
        self.source('''local target=shared.mouseCameraControls.target()
assert(not target:FuzzyEq(savedMouseCameraFrame))
shared.mouseTargetBeforeWheel=target
game:GetService("RunService").PreRender:Wait();game:GetService("RunService").PreRender:Wait()
assert(not workspace.CurrentCamera.CFrame:FuzzyEq(savedMouseCameraFrame))''')
        self.send('events',events=[mouse('MouseWheel',z=1,delta=[0,0,1])])
        self.source('assert(math.abs((shared.mouseCameraControls.target().Position-shared.mouseTargetBeforeWheel.Position).Magnitude-2)<1e-5)')
        self.send('release')
        self.source('''shared.mouseCameraControls.stop();shared.mouseCameraControls.stop()
assert(workspace.CurrentCamera.CFrame:FuzzyEq(savedMouseCameraFrame))
assert(next(game:GetService("ContextActionService"):GetAllBoundActionInfo())==nil)''')

    def test_lease_expiry_cancels_native_mouse_and_keyboard_contacts(self):
        clock=[10.];self.bridge.clock=lambda:clock[0]
        self.source('''local u=game:GetService("UserInputService");pointerCancelled=0
u.InputEnded:Connect(function(i)if i.UserInputState==Enum.UserInputState.Cancel then pointerCancelled+=1 end end)''')
        self.send('attach');self.send('events',events=[{'phase':'begin','key_code':'W'},mouse('MouseButton1','begin'),mouse('MouseButton3','begin')])
        clock[0]+=2.1
        self.source('''local u=game:GetService("UserInputService")
assert(#u:GetKeysPressed()==0 and #u:GetMouseButtonsPressed()==0 and pointerCancelled==3)
assert(not u.KeyboardEnabled and not u.MouseEnabled)''')

if __name__=='__main__':unittest.main()
