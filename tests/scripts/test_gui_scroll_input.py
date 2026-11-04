"""Wheel transport -> native canvas behavior, without a Roblox connection."""
import unittest
from persistent_runtime import PersistentLuauHost
from test_display_layout import layout_snapshot
import test_scrolling_layout as layouts

class GuiScrollInputTests(unittest.TestCase):
    execute=layouts.ScrollingLayoutTests.execute

    def setUp(self):
        self.host=PersistentLuauHost(layout_snapshot());self.addCleanup(self.host.close)
        self.execute(layouts.SETUP+'\nscroll.Name="ScrollInput";child.Name="ScrollChild"')
        self.id=self.frames()['ScrollInput']['id']

    def frames(self):
        result=self.host.call({'op':'render'})
        self.assertTrue(result['ok'],result.get('error'))
        return {item['name']:item for item in result['frame']['gui']}

    def wheel(self,x,y,id=None):
        result=self.host.call({'op':'gui_action','action':{'id':id or self.id,'kind':'scroll','delta':[x,y]}})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertEqual(result.get('task_errors',[]),[])
        return result

    def position(self):
        return self.execute('return scroll.CanvasPosition.X,scroll.CanvasPosition.Y')

    def test_render_routes_children_and_container_to_scroll_target(self):
        for name in ('ScrollInput','ScrollChild'):
            self.assertEqual(self.frames()[name]['scrollTarget'],self.id)
            self.assertEqual(self.frames()[name]['scrollViewport'],[300,200])
        self.execute('scroll.ScrollingEnabled=false')
        self.assertNotIn('scrollTarget',self.frames()['ScrollInput'])
        self.assertNotIn('scrollTarget',self.frames()['ScrollChild'])

    def test_pixels_move_canvas_children_and_fire_changed_once(self):
        self.execute('''
getgenv().offsets=0;getgenv().positions=0
scroll:GetPropertyChangedSignal("CanvasPosition"):Connect(function()offsets+=1 end)
child:GetPropertyChangedSignal("AbsolutePosition"):Connect(function()positions+=1 end)
''')
        before=self.frames()['ScrollChild']['box']
        self.wheel(25,60)
        self.assertEqual(self.position(),[25,60])
        self.assertEqual(self.execute('return offsets,positions'),[1,1])
        after=self.frames()['ScrollChild']['box']
        self.assertEqual(after[:2],[before[0]-25,before[1]-60])
        self.wheel(0,0)
        self.assertEqual(self.execute('return offsets,positions'),[1,1])

    def test_clamp_then_reverse_does_not_lose_return_movement(self):
        self.wheel(4096,4096);self.assertEqual(self.position(),[310,310])
        self.wheel(-20,-40);self.assertEqual(self.position(),[290,270])
        self.wheel(-4096,-4096);self.assertEqual(self.position(),[0,0])

    def test_direction_and_disabled_scroll_are_respected(self):
        self.execute('scroll.ScrollingDirection=Enum.ScrollingDirection.Y')
        self.wheel(50,60);self.assertEqual(self.position(),[0,60])
        self.execute('scroll.ScrollingDirection=Enum.ScrollingDirection.X')
        self.wheel(20,60);self.assertEqual(self.position(),[20,0])
        self.execute('scroll.ScrollingEnabled=false')
        self.wheel(50,60);self.assertEqual(self.position(),[0,0])

    def test_physical_pixels_convert_through_ui_scale(self):
        self.execute('Instance.new("UIScale",scroll).Scale=2')
        before=self.frames()['ScrollChild']['box']
        self.wheel(20,60);self.assertEqual(self.position(),[10,30])
        after=self.frames()['ScrollChild']['box']
        self.assertEqual(after[:2],[before[0]-20,before[1]-60])

    def nested(self):
        self.execute('''
scroll.ScrollBarThickness=0
getgenv().inner=Instance.new("ScrollingFrame",scroll);inner.Name="InnerScroll"
inner.Size=UDim2.fromOffset(100,100);inner.CanvasSize=UDim2.fromOffset(100,200)
inner.ScrollBarThickness=0;inner.ScrollingDirection=Enum.ScrollingDirection.Y
child.Parent=inner
''')
        return self.frames()['InnerScroll']['id']

    def test_nested_scroll_consumes_inner_then_outer_and_respects_axes(self):
        id=self.nested()
        self.assertEqual(self.frames()['ScrollChild']['scrollTarget'],id)
        self.wheel(20,140,id)
        self.assertEqual(self.execute('return inner.CanvasPosition.X,inner.CanvasPosition.Y'),[0,100])
        self.assertEqual(self.position(),[20,40])
        self.wheel(0,-130,id)
        self.assertEqual(self.execute('return inner.CanvasPosition.Y,scroll.CanvasPosition.Y'),[0,10])

    def test_disabled_inner_routes_to_parent_even_when_request_is_stale(self):
        id=self.nested();self.execute('inner.ScrollingEnabled=false')
        self.assertEqual(self.frames()['ScrollChild']['scrollTarget'],self.id)
        self.wheel(0,50,id)
        self.assertEqual(self.execute('return inner.CanvasPosition.Y,scroll.CanvasPosition.Y'),[0,50])

    def test_reject_invalid_deltas_target_types_and_hidden_targets(self):
        child_id=self.frames()['ScrollChild']['id']
        for id,delta in [(self.id,[4097,0]),(self.id,[True,0]),(self.id,[0]),(self.id,['2',0]),(child_id,[0,20])]:
            result=self.host.call({'op':'gui_action','action':{'id':id,'kind':'scroll','delta':delta}})
            self.assertFalse(result['ok'])
        self.assertEqual(self.position(),[0,0])
        self.execute('scroll.Visible=false')
        result=self.host.call({'op':'gui_action','action':{'id':self.id,'kind':'scroll','delta':[0,20]}})
        self.assertFalse(result['ok'])

    def test_automatic_canvas_content_can_scroll(self):
        self.execute('''
scroll.CanvasSize=UDim2.fromOffset(300,200);scroll.AutomaticCanvasSize=Enum.AutomaticSize.Y
child.Size=UDim2.fromOffset(200,500);child.Position=UDim2.fromOffset(0,0)
''')
        self.wheel(0,90);self.assertEqual(self.position(),[0,90])

if __name__=='__main__':unittest.main()
