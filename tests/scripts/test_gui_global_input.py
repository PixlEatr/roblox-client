"""GUI wheel events share global input state and context-action dispatch."""
import unittest
import test_gui_wheel_events as gui

class GuiGlobalInputTests(unittest.TestCase):
    execute=gui.GuiWheelEventsTests.execute
    wheel=gui.GuiWheelEventsTests.wheel
    frame=gui.GuiWheelEventsTests.frame

    def setUp(self):
        gui.GuiWheelEventsTests.setUp(self)
        self.execute('''
getgenv().globalEvents={};getgenv().types={}
local u=game:GetService("UserInputService")
getgenv().globalConnection=u.InputChanged:Connect(function(input,processed)
 getgenv().globalInput=input
 table.insert(globalEvents,{input.UserInputType.Name,input.UserInputState.Name,
 input.Position.X,input.Position.Y,input.Position.Z,processed})
end)
getgenv().typeConnection=u.LastInputTypeChanged:Connect(function(kind)table.insert(types,kind.Name)end)
''')

    def deliver(self,event,**extra):
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
                                                    'input_events':[event],**extra},'timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'));self.assertEqual(result.get('task_errors',[]),[])

    def test_gui_and_global_callbacks_share_input_and_mouse_queries(self):
        self.wheel()
        self.assertEqual(self.execute('return table.unpack(globalEvents[1])'),['MouseWheel','Change',260,160,-1,True])
        self.assertEqual(self.execute('''
local u=game:GetService("UserInputService")
return lastInput==globalInput,u:GetMouseLocation().X,u:GetMouseLocation().Y,
u:GetLastInputType().Name,#types,types[1],#u:GetKeysPressed(),#u:GetMouseButtonsPressed(),
u:GetMouseDelta().X,u.MouseEnabled,u.KeyboardEnabled
'''),[True,260,160,'MouseWheel',1,'MouseWheel',0,0,0,False,False])

    def test_handled_edges_remain_processed_without_duplicate_global_event(self):
        self.execute('scroll.CanvasPosition=Vector2.new(0,310)')
        self.wheel(delta=[0,40],position=[260,100])
        self.assertEqual(self.execute('return #globalEvents,globalEvents[1][6],scroll.CanvasPosition.Y'),[1,True,310])

    def plain(self):
        self.execute('child.Parent=scroll.Parent;child.Position=UDim2.fromOffset(200,100);child.Active=false')

    def test_plain_frame_is_unprocessed_until_active(self):
        self.plain();self.wheel()
        self.execute('child.Active=true');self.wheel()
        self.assertEqual(self.execute('return globalEvents[1][6],globalEvents[2][6],#types'),[False,True,1])

    def test_unprocessed_wheel_reaches_context_actions_and_processed_does_not(self):
        self.execute('''
getgenv().actions={}
game:GetService("ContextActionService"):BindAction("WheelAction",function(name,state,input)
 table.insert(actions,{name,state.Name,input.UserInputType.Name,input.Position.Z})
 return Enum.ContextActionResult.Pass
end,false,Enum.UserInputType.MouseWheel)
''')
        self.wheel();self.assertEqual(self.execute('return #actions'),[0])
        self.plain();self.wheel(1,[0,-20])
        self.assertEqual(self.execute('return table.unpack(actions[1])'),['WheelAction','Change','MouseWheel',1])
        self.execute('child.Active=true');self.wheel()
        self.assertEqual(self.execute('return #actions'),[1])

    def test_focused_and_gui_wheel_paths_reuse_the_continuous_input(self):
        self.wheel();self.execute('getgenv().first=globalInput')
        self.deliver({'input_type':'MouseWheel','phase':'change','position':[40,50,1],
                      'delta':[0,0,1],'mouse_location':[40,50],'processed':False},
                     input_capabilities={'MouseEnabled':True})
        self.assertEqual(self.execute('return first==globalInput,#globalEvents,globalEvents[2][6],#types'),[True,2,False,1])
        self.wheel()
        self.assertEqual(self.execute('return first==globalInput,#globalEvents,#types'),[True,3,1])

    def test_focused_keyboard_then_gui_wheel_changes_last_input_type(self):
        self.wheel()
        self.deliver({'input_type':'Keyboard','key_code':'W','phase':'begin'},input_capabilities={'KeyboardEnabled':True})
        self.deliver({'input_type':'Keyboard','key_code':'W','phase':'end'})
        self.wheel()
        self.assertEqual(self.execute('return table.concat(types,","),game:GetService("UserInputService"):GetLastInputType().Name'),
                         ['MouseWheel,Keyboard,MouseWheel','MouseWheel'])

    def test_only_global_listener_or_context_binding_routes_plain_frame(self):
        self.plain();self.execute('connection:Disconnect();fc:Disconnect();bc:Disconnect()')
        self.assertTrue(self.frame()['wheelTarget'])
        self.execute('globalConnection:Disconnect();typeConnection:Disconnect()')
        self.assertNotIn('wheelTarget',self.frame())
        self.execute('game:GetService("ContextActionService"):BindAction("WheelOnly",function()end,false,Enum.UserInputType.MouseWheel)')
        self.assertTrue(self.frame()['wheelTarget'])
        self.execute('game:GetService("ContextActionService"):UnbindAction("WheelOnly")')
        self.assertNotIn('wheelTarget',self.frame())

    def test_invalid_gui_input_does_not_change_global_state(self):
        result=self.host.call({'op':'gui_action','action':{'id':self.id,'kind':'wheel','delta':[0,40],'position':[1,1],'steps':-1}})
        self.assertFalse(result['ok'])
        self.assertEqual(self.execute('return #globalEvents,#types,game:GetService("UserInputService"):GetLastInputType().Name'),[0,0,'None'])

if __name__=='__main__':unittest.main()
