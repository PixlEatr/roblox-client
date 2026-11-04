"""Legacy PlayerMouse signals through real host input, without an account."""
import unittest
import test_input_events as inputs
import test_local_compat as compat


class LegacyMouseTests(unittest.TestCase):
    setUp=inputs.InputEventTests.setUp
    execute=inputs.InputEventTests.execute
    deliver=inputs.InputEventTests.deliver

    def listen(self):
        self.execute('''
mouse=game:GetService("Players").LocalPlayer:GetMouse();mouseEvents={}
for _,name in ipairs({"Button1Down","Button1Up","Button2Down","Button2Up","Move","WheelForward","WheelBackward"})do
 mouse[name]:Connect(function(...)
  assert(select("#",...)==0,"legacy Mouse events take no arguments")
  table.insert(mouseEvents,name)
 end)
end
''')

    def names(self):
        return self.execute('return table.concat(mouseEvents,",")')['values'][0]

    def wheel(self,z,**extra):
        return {'input_type':'MouseWheel','phase':'change','position':[12,34,z],**extra}

    def test_buttons_movement_and_wheel_have_no_arguments_and_keep_order(self):
        self.listen()
        self.deliver({'input_type':'MouseButton1','phase':'begin'})
        self.deliver({'input_type':'MouseButton2','phase':'begin'})
        self.deliver({'input_type':'MouseMovement','phase':'change','position':[12,34,0]})
        self.deliver({'input_type':'MouseButton1','phase':'change'})
        self.deliver(self.wheel(3),self.wheel(-2),self.wheel(0))
        self.deliver({'input_type':'MouseButton1','phase':'end'},
                     {'input_type':'MouseButton2','phase':'end'})
        self.assertEqual(self.names(),'Button1Down,Button2Down,Move,WheelForward,WheelBackward,Button1Up,Button2Up')

    def test_legacy_observer_does_not_sink_context_actions_or_use_processed_argument(self):
        self.listen()
        self.execute('''
actionCount=0;globalCount=0
game:GetService("UserInputService").InputChanged:Connect(function()globalCount+=1 end)
game:GetService("ContextActionService"):BindAction("Wheel",function()
 actionCount+=1;return Enum.ContextActionResult.Pass
end,false,Enum.UserInputType.MouseWheel)
mouse.WheelForward:Connect(function()return Enum.ContextActionResult.Sink end)
''')
        self.deliver(self.wheel(1,processed=True))
        self.deliver(self.wheel(1,processed=False))
        self.assertEqual(self.names(),'WheelForward,WheelForward')
        self.assertEqual(self.execute('return globalCount,actionCount')['values'],[2,1])

    def test_mouse_creation_is_lazy_and_destroyed_instance_stays_silent(self):
        self.deliver(self.wheel(1))
        self.execute('for _,v in ipairs(getinstances())do assert(not v:IsA("Mouse"))end')
        self.listen()
        self.execute('oldMouse=mouse;assert(mouse==game:GetService("Players").LocalPlayer:GetMouse());mouse:Destroy()')
        self.deliver(self.wheel(-1))
        self.assertEqual(self.names(),'')
        self.listen()
        self.execute('assert(mouse~=oldMouse)')
        self.deliver(self.wheel(1))
        self.assertEqual(self.names(),'WheelForward')

    def test_disconnected_callback_does_not_receive_more_input(self):
        self.execute('''mouse=game:GetService("Players").LocalPlayer:GetMouse();count=0
connection=mouse.WheelForward:Connect(function()count+=1 end)''')
        self.deliver(self.wheel(1))
        self.execute('connection:Disconnect()')
        self.deliver(self.wheel(1))
        self.assertEqual(self.execute('return count')['values'],[1])

    def test_cancel_and_device_detach_release_pressed_mouse_buttons_once(self):
        self.listen()
        self.deliver({'input_type':'MouseButton1','phase':'begin'},
                     {'input_type':'MouseButton2','phase':'begin'})
        self.deliver({'input_type':'MouseButton1','phase':'cancel'})
        result=self.host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'actions':[], 'input_capabilities':{'MouseEnabled':False}}})
        self.assertTrue(result['ok'],result.get('error'));self.assertFalse(result.get('task_errors'))
        self.assertEqual(self.names(),'Button1Down,Button2Down,Button1Up,Button2Up')
        self.execute('assert(#game:GetService("UserInputService"):GetMouseButtonsPressed()==0)')
        self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                                             'input_capabilities':{'MouseEnabled':False}}})
        self.assertEqual(self.names(),'Button1Down,Button2Down,Button1Up,Button2Up')

    def test_wheel_direction_uses_current_host_data_not_mutated_or_stale_input(self):
        self.listen()
        self.execute('game:GetService("UserInputService").InputChanged:Connect(function(i)lastInput=i end)')
        self.deliver(self.wheel(1))
        self.execute('''firstInput=lastInput;lastInput.Position=Vector3.new(0,0,-99)
lastInput.UserInputType=Enum.UserInputType.Touch;lastInput.KeyCode=Enum.KeyCode.W''')
        self.deliver(self.wheel(1,delta=[0,0,-1]))
        self.execute('''assert(lastInput==firstInput and lastInput.UserInputType==Enum.UserInputType.MouseWheel)
assert(lastInput.KeyCode==Enum.KeyCode.None)''')
        self.deliver({'input_type':'MouseWheel','phase':'change'})
        self.deliver({'input_type':'MouseWheel','phase':'change','delta':[0,0,-1]})
        self.assertEqual(self.names(),'WheelForward,WheelForward,WheelBackward')

    def test_other_devices_do_not_fabricate_mouse_events(self):
        self.listen()
        for kind in ('Keyboard','Touch','MouseButton3'):
            extra={'key_code':'W'} if kind=='Keyboard' else {'input_id':'finger'} if kind=='Touch' else {}
            self.deliver({'input_type':kind,'phase':'begin',**extra},
                         {'input_type':kind,'phase':'end',**extra})
        self.assertEqual(self.names(),'')

    def test_invalid_input_batch_does_not_partially_deliver_legacy_signals(self):
        self.listen()
        self.deliver(self.wheel(1),{'input_type':'MouseWheel','phase':'begin'},ok=False)
        self.assertEqual(self.names(),'')
        self.deliver(self.wheel(-1))
        self.assertEqual(self.names(),'WheelBackward')

    def test_destroyed_local_player_cannot_recreate_or_receive_mouse(self):
        self.listen()
        self.execute('''player=game:GetService("Players").LocalPlayer;player:Destroy()
assert(not pcall(function()player:GetMouse()end))''')
        self.deliver(self.wheel(1))
        self.assertEqual(self.names(),'')


class LegacyMouseSurfaceTests(unittest.TestCase):
    run_script=compat.LocalCompatibilityTests.run_script

    def test_signal_surface_in_both_hosts(self):
        self.run_script('''
local mouse=game:GetService("Players").LocalPlayer:GetMouse()
for _,name in ipairs({"Button1Down","Button1Up","Button2Down","Button2Up","Move","WheelForward","WheelBackward"})do
 assert(typeof(mouse[name])=="RBXScriptSignal")
 assert(mouse[name]==mouse[name])
 local connection=mouse[name]:Connect(function()end)
 assert(connection.Connected);connection:Disconnect();assert(not connection.Connected)
end
''')

if __name__=='__main__':unittest.main()
