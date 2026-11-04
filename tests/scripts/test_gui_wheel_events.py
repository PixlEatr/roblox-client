"""Native GUI wheel signals and actual input coordinates; offline only."""
import unittest
from persistent_runtime import PersistentLuauHost
from test_display_layout import layout_snapshot
import test_scrolling_layout as layout

class GuiWheelEventsTests(unittest.TestCase):
    execute=layout.ScrollingLayoutTests.execute

    def setUp(self):
        self.host=PersistentLuauHost(layout_snapshot());self.addCleanup(self.host.close)
        self.execute(layout.SETUP+'''
scroll.Name="WheelScroll";child.Name="WheelChild"
getgenv().events={};getgenv().parentEvents=0
getgenv().connection=child.InputChanged:Connect(function(input)
 table.insert(events,{input.UserInputType.Name,input.UserInputState.Name,input.KeyCode.Name,
 input.Position.X,input.Position.Y,input.Position.Z,input.Delta.Z})
 getgenv().lastInput=input
end)
scroll.InputChanged:Connect(function()parentEvents+=1 end)
getgenv().forward=0;getgenv().backward=0
getgenv().fc=child.MouseWheelForward:Connect(function(x,y)forward+=1;getgenv().mouseX=x;getgenv().mouseY=y end)
getgenv().bc=child.MouseWheelBackward:Connect(function(x,y)backward+=1;getgenv().mouseX=x;getgenv().mouseY=y end)
''')
        self.id=self.frame()['id']

    def frame(self):
        return next(g for g in self.host.call({'op':'render'})['frame']['gui'] if g['name']=='WheelChild')

    def wheel(self,steps=-1,delta=None,position=None):
        action={'id':self.id,'kind':'wheel','delta':delta if delta is not None else [0,20],
                'position':position if position is not None else [260,160],'steps':steps}
        result=self.host.call({'op':'gui_action','action':action})
        self.assertTrue(result['ok'],result.get('error'));self.assertEqual(result.get('task_errors',[]),[])

    def test_typed_input_coordinates_parent_signal_and_canvas_movement(self):
        self.wheel()
        self.assertEqual(self.execute('return table.unpack(events[1])'),['MouseWheel','Change','None',260,160,-1,-1])
        self.assertEqual(self.execute('return forward,backward,mouseX,mouseY,parentEvents,scroll.CanvasPosition.Y'),[0,1,260,160,1,20])

    def test_forward_backward_and_coalesced_steps(self):
        self.wheel(-3,[0,60]);self.wheel(1,[0,-20])
        self.assertEqual(self.execute('return #events,events[1][6],events[2][6],forward,backward,scroll.CanvasPosition.Y'),[2,-3,1,1,1,40])

    def test_horizontal_input_has_zero_wheel_z_and_no_direction_signal(self):
        self.wheel(0,[20,0])
        self.assertEqual(self.execute('return #events,events[1][6],forward,backward,scroll.CanvasPosition.X'),[1,0,0,0,20])

    def test_plain_gui_receives_wheel_without_scroll_container(self):
        self.execute('child.Parent=scroll.Parent;child.Position=UDim2.fromOffset(200,100)')
        self.assertTrue(self.frame()['wheelTarget']);self.assertNotIn('scrollTarget',self.frame())
        self.wheel()
        self.assertEqual(self.execute('return #events,backward,parentEvents,scroll.CanvasPosition.Y'),[1,1,0,0])

    def test_disabled_scroller_still_delivers_gui_events(self):
        self.execute('scroll.ScrollingEnabled=false')
        self.wheel()
        self.assertEqual(self.execute('return #events,parentEvents,scroll.CanvasPosition.Y'),[1,1,0])

    def test_outside_parent_does_not_scroll_or_emit_parent_events(self):
        self.execute('scroll.ClipsDescendants=false')
        self.wheel(position=[450,170])
        self.assertEqual(self.execute('return #events,parentEvents,scroll.CanvasPosition.Y'),[1,0,0])

    def test_clipped_and_stale_coordinates_fail_before_any_mutation(self):
        for position in ([450,170],[20,20]):
            result=self.host.call({'op':'gui_action','action':{'id':self.id,'kind':'wheel','delta':[0,30],'position':position,'steps':-1}})
            self.assertFalse(result['ok'])
        self.assertEqual(self.execute('return #events,scroll.CanvasPosition.Y'),[0,0])

    def test_reused_input_object_is_refreshed_after_script_write(self):
        self.wheel()
        self.execute('getgenv().first=lastInput;lastInput.Position=Vector3.new(999,999,999);lastInput.UserInputType=Enum.UserInputType.Touch')
        self.wheel(1,[0,-10],position=[270,170])
        self.assertEqual(self.execute('return first==lastInput,lastInput.Position.X,lastInput.Position.Z,lastInput.UserInputType.Name'),[True,270,1,'MouseWheel'])

    def test_disconnect_removes_plain_frame_wheel_routing(self):
        self.execute('child.Parent=scroll.Parent;connection:Disconnect();fc:Disconnect();bc:Disconnect()')
        self.assertNotIn('wheelTarget',self.frame());self.assertNotIn('scrollTarget',self.frame())

    def test_invalid_metadata_rejected(self):
        action={'id':self.id,'kind':'wheel','delta':[0,20],'position':[260,160],'steps':-1}
        for patch in ({'steps':True},{'steps':129},{'steps':1.5},{'position':[True,10]},{'position':[1]},{'position':[40000,10]}):
            result=self.host.call({'op':'gui_action','action':{**action,**patch}})
            self.assertFalse(result['ok'])
        self.assertEqual(self.execute('return #events,scroll.CanvasPosition.Y'),[0,0])

    def test_fast_wheel_burst_survives_child_moving_before_next_render(self):
        self.execute('child.Position=UDim2.fromOffset(0,0);child.Size=UDim2.fromOffset(300,40)')
        self.wheel(delta=[0,100],position=[120,60])
        self.wheel(delta=[0,100],position=[120,60])
        self.assertEqual(self.execute('return #events,parentEvents,scroll.CanvasPosition.Y'),[1,2,200])

if __name__=='__main__':unittest.main()
