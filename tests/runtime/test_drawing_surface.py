"""Drawing circles share the actual GUI projection and lifecycle."""
import unittest

from persistent_runtime import PersistentLuauHost
from test_display_layout import layout_snapshot


class DrawingSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(layout_snapshot())
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result.get('task_errors', []), [])
        return result['values']

    def circles(self):
        result = self.host.call({'op': 'render'})
        self.assertTrue(result['ok'], result)
        return [g for g in result['frame']['gui'] if g['name'] == 'DrawingCircle']

    def test_circle_properties_reach_the_visible_surface(self):
        self.execute('''
getgenv().circle=Drawing.new("Circle")
circle.Position=Vector2.new(400,300);circle.Radius=95
circle.Color=Color3.new(1,0,0);circle.Thickness=3;circle.Visible=true
''')
        circle, = self.circles()
        # The GUI projection uses coordinates relative to this fixture's
        # 40-pixel core inset, including IgnoreGuiInset screens.
        self.assertEqual(circle['box'], [305, 165, 190, 190, 0])
        self.assertEqual(circle['cornerRadius'], 95)
        self.assertEqual(circle['backgroundTransparency'], 1)
        self.assertEqual(circle['stroke'], {
            'color': {'$color3': [1, 0, 0]}, 'thickness': 3, 'transparency': 0})
        self.execute('''
circle.Radius=25;circle.Position=Vector2.new(100,80)
circle.Filled=true;circle.Transparency=.25;circle.Thickness=0
setrenderproperty(circle,"Color",Color3.new(0,1,0))
''')
        circle, = self.circles()
        self.assertEqual(circle['box'], [75, 15, 50, 50, 0])
        self.assertEqual(circle['cornerRadius'], 25)
        self.assertEqual(circle['background'], {'$color3': [0, 1, 0]})
        self.assertEqual(circle['backgroundTransparency'], .75)
        self.assertNotIn('stroke', circle)

    def test_visibility_destroy_and_cache_clear_remove_real_elements(self):
        self.assertEqual(self.execute('''
getgenv().a=Drawing.new("Circle");getgenv().b=Drawing.new("Circle")
a.Visible=true;b.Visible=true
local core=game:GetService("CoreGui")
getgenv().surface=core:FindFirstChild("DrawingSurface")
local corners=0
for _,child in surface:GetDescendants()do
 if child:IsA("UICorner")then
  assert(child.CornerRadius==UDim.new(1,0))
  assert(tostring(child.CornerRadius)=="1, 0");corners+=1
 end
end
return corners
'''), [2])
        self.assertEqual(len(self.circles()), 2)
        self.execute('a.Visible=false')
        self.assertEqual(len(self.circles()), 1)
        self.assertEqual(self.execute('''
a:Destroy();a:Destroy()
local remaining=#surface:GetChildren()
cleardrawcache()
return remaining,#surface:GetChildren(),a.__OBJECT_EXISTS,b.__OBJECT_EXISTS
'''), [1, 0, False, False])
        self.assertEqual(self.circles(), [])

    def test_ordinary_gui_corner_and_stroke_are_typed_and_projected(self):
        self.assertEqual(self.execute('''
local f=Instance.new("Frame",game:GetService("Players").LocalPlayer.Gui.Screen)
f.Name="RoundedFixture";f.Size=UDim2.fromOffset(200,100)
local c=Instance.new("UICorner",f);c.CornerRadius=UDim.new(.25,5)
local s=Instance.new("UIStroke",f);s.Color=Color3.new(.2,.4,.6)
s.Thickness=2;s.Transparency=.5
local copy=c:Clone()
local radiusOk=pcall(function()c.CornerRadius=4 end)
local colorOk=pcall(function()s.Color="red"end)
return copy.CornerRadius==c.CornerRadius,radiusOk,colorOk
'''), [True, False, False])
        result = self.host.call({'op': 'render'})
        rounded, = [g for g in result['frame']['gui'] if g['name'] == 'RoundedFixture']
        self.assertEqual(rounded['cornerRadius'], 30)
        self.assertEqual(rounded['stroke']['thickness'], 2)
        self.assertEqual(rounded['stroke']['transparency'], .5)


if __name__ == '__main__':
    unittest.main()
