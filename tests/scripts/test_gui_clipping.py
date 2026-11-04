"""Native clipping projection; expectations are not Studio parity captures."""
import unittest
from persistent_runtime import PersistentLuauHost
from test_display_layout import layout_snapshot
import test_gui_list_layout as lists

SETUP='''
local screen=game:GetService("Players").LocalPlayer.Gui.Screen
getgenv().panel=Instance.new("Frame",screen);panel.Name="ClipPanel"
panel.Position=UDim2.fromOffset(30,40);panel.Size=UDim2.fromOffset(100,80)
getgenv().child=Instance.new("TextButton",panel);child.Name="ClipChild"
child.Size=UDim2.fromOffset(80,40);child.Position=UDim2.fromOffset(70,50)
'''

class GuiClippingTests(unittest.TestCase):
    execute=lists.GuiListLayoutTests.execute
    update=lists.GuiListLayoutTests.update

    def setUp(self):
        snapshot=layout_snapshot()
        snapshot['services']['StarterGui']='StarterGui'
        snapshot['nodes'].append({'id':'StarterGui','name':'StarterGui','class_name':'StarterGui',
                                  'parent':None,'properties':{}})
        self.host=PersistentLuauHost(snapshot);self.addCleanup(self.host.close)
        self.execute(SETUP)

    def frame(self,name='ClipChild'):
        result=self.host.call({'op':'render'})
        self.assertTrue(result['ok'],result.get('error'))
        return next(g for g in result['frame']['gui'] if g['name']==name)

    def test_default_and_explicit_clip_keeps_authored_geometry(self):
        self.assertEqual(self.execute('return panel.ClipsDescendants'),[False])
        original=self.frame()['box']
        self.assertNotIn('clip',self.frame())
        self.execute('panel.ClipsDescendants=true')
        self.assertEqual(self.frame()['clip'],[30,40,100,80])
        self.assertEqual(self.frame()['box'],original)
        self.assertNotIn('clip',self.frame('ClipPanel'))
        self.execute('panel.ClipsDescendants=false')
        self.assertNotIn('clip',self.frame())

    def test_nested_intersection_and_disjoint_bounds(self):
        self.execute('''
panel.ClipsDescendants=true
getgenv().inner=Instance.new("Frame",panel);inner.Size=UDim2.fromOffset(100,100)
inner.Position=UDim2.fromOffset(50,20);inner.ClipsDescendants=true;child.Parent=inner
''')
        self.assertEqual(self.frame()['clip'],[80,60,50,60])
        self.execute('inner.Position=UDim2.fromOffset(200,200)')
        self.assertEqual(self.frame()['clip'],[230,240,0,0])

    def test_scrolling_default_clips_viewport_not_canvas(self):
        self.execute('''
getgenv().scroll=Instance.new("ScrollingFrame",panel.Parent);scroll.Name="Scroll"
scroll.Position=UDim2.fromOffset(10,20);scroll.Size=UDim2.fromOffset(100,80)
scroll.CanvasSize=UDim2.fromOffset(100,500);scroll.CanvasPosition=Vector2.new(0,100)
child.Parent=scroll
''')
        self.assertEqual(self.execute('return scroll.ClipsDescendants'),[True])
        self.assertEqual(self.frame()['clip'],[10,20,100,80])
        self.assertEqual(self.frame()['box'][:2],[80,-30])
        self.execute('scroll.ClipsDescendants=false')
        self.assertNotIn('clip',self.frame())

    def test_rotation_flag_for_rotated_child(self):
        self.execute('panel.ClipsDescendants=true;child.Rotation=45')
        self.assertNotIn('clip',self.frame())
        self.execute('game:GetService("StarterGui").ClipsDescendantsSupportsRotation=true')
        self.assertEqual(self.frame()['clip'],[30,40,100,80])

    def test_rotated_parent_keeps_explicit_existing_layout_limit(self):
        self.execute('panel.ClipsDescendants=true;panel.Rotation=20')
        result=self.execute('return pcall(function()return child.AbsolutePosition end)')
        self.assertFalse(result[0])
        self.assertIn('rotated ancestor layout',result[1])

    def test_scale_padding_and_corner_do_not_change_clip_rectangle(self):
        self.execute('''
panel.ClipsDescendants=true;Instance.new("UIScale",panel).Scale=2
Instance.new("UICorner",panel).CornerRadius=UDim.new(0,20)
Instance.new("UIPadding",panel).PaddingLeft=UDim.new(0,10)
''')
        self.assertEqual(self.frame()['clip'],[30,40,200,160])

    def test_received_toggle_and_reparent_invalidate_clipping(self):
        panel_id=self.frame('ClipPanel')['id']
        self.update([{'sequence':10,'kind':'set','id':panel_id,'fields':{'ClipsDescendants':True}}])
        self.assertEqual(self.frame()['clip'],[30,40,100,80])
        self.execute('child.Parent=panel.Parent')
        self.assertNotIn('clip',self.frame())
        self.execute('child.Parent=panel')
        self.assertEqual(self.frame()['clip'],[30,40,100,80])
        self.update([{'sequence':11,'kind':'set','id':panel_id,'fields':{'ClipsDescendants':False}}])
        self.assertNotIn('clip',self.frame())

    def test_cloned_container_uses_its_own_bounds(self):
        self.execute('''
panel.ClipsDescendants=true
local clone=panel:Clone();clone.Name="ClonePanel";clone.Parent=panel.Parent
clone.Position=UDim2.fromOffset(400,300);clone.ClipChild.Name="CloneChild"
''')
        self.assertEqual(self.frame('CloneChild')['clip'],[400,300,100,80])
        self.assertEqual(self.frame()['clip'],[30,40,100,80])

    def test_local_type_validation_and_changed_signal(self):
        self.execute('''
getgenv().calls=0;panel:GetPropertyChangedSignal("ClipsDescendants"):Connect(function()calls+=1 end)
panel.ClipsDescendants=true;panel.ClipsDescendants=true;panel.ClipsDescendants=false
''')
        self.assertEqual(self.execute('''
return calls,pcall(function()panel.ClipsDescendants=1 end),
pcall(function()game:GetService("StarterGui").ClipsDescendantsSupportsRotation="yes" end)
''')[:3],[2,False,False])

if __name__=='__main__':unittest.main()
