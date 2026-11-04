"""Host surface -> native GUI layout, callbacks, and camera compatibility."""
import copy
import unittest

from display_state import validate_display
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_persistent_runtime import SNAPSHOT
from test_world_snapshot import fixture


def udim2(xs, xo, ys, yo):
    return {'$udim2': [{'$udim': [xs, xo]}, {'$udim': [ys, yo]}]}


PROFILE = {'size': [1000, 800], 'device_insets': [10, 0, 10, 0],
           'core_insets': [10, 40, 10, 0]}


def layout_snapshot(profile=PROFILE):
    s = copy.deepcopy(SNAPSHOT)
    if profile is not None:
        s['display'] = copy.deepcopy(profile)
    s['class_parents'].update({'Frame': 'GuiObject', 'GuiObject': 'GuiBase2d'})
    def node(id, cls, parent, properties):
        return {'id': id, 'name': id, 'class_name': cls, 'parent': parent, 'properties': properties}
    s['nodes'] += [node('Gui', 'PlayerGui', 'scope:player-1', {}),
                   node('Screen', 'ScreenGui', 'Gui', {'IgnoreGuiInset': False, 'ScreenInsets': 2}),
                   node('Frame', 'Frame', 'Screen', {'Size': udim2(.5, 10, .5, -20),
                        'Position': udim2(.5, 0, .5, 0), 'AnchorPoint': {'$vector2': [.5, .5]}, 'Rotation': 0}),
                   node('Child', 'Frame', 'Frame', {'Size': udim2(.5, 0, .5, 0),
                        'Position': udim2(0, 10, 0, 20), 'AnchorPoint': {'$vector2': [0, 0]}, 'Rotation': 0})]
    return s


class DisplayLayoutTests(unittest.TestCase):
    def host(self, snapshot=None):
        host = PersistentLuauHost(snapshot or layout_snapshot())
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result.get('task_errors', []), [])
        return result['values']

    def update(self, host, **fields):
        return host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
            'version': 1, 'delta': True, 'actions': [], **fields}})

    def test_screen_nested_boxes_camera_and_insets_share_the_surface(self):
        self.assertEqual(self.execute(self.host(), '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
local f,c=s.Frame,s.Frame.Child
local a,b=game:GetService("GuiService"):GetGuiInset()
local v=workspace.CurrentCamera.ViewportSize
return typeof(s.AbsoluteSize),s.AbsoluteSize.X,s.AbsoluteSize.Y,s.AbsolutePosition.X,s.AbsolutePosition.Y,
 f.AbsoluteSize.X,f.AbsoluteSize.Y,f.AbsolutePosition.X,f.AbsolutePosition.Y,
 c.AbsoluteSize.X,c.AbsoluteSize.Y,c.AbsolutePosition.X,c.AbsolutePosition.Y,
 v.X,v.Y,a.X,a.Y,b.X,b.Y,client.propertyOrigin(s,"AbsoluteSize")
'''), ['Vector2', 980, 760, 0, 0, 500, 360, 240, 200, 250, 180, 250, 220,
       980, 800, 10, 40, 10, 0, 'local_display'])

    def test_resize_updates_descendant_callbacks_once_and_keeps_inactive_camera(self):
        host = self.host()
        self.execute(host, '''
getgenv().s=game:GetService("Players").LocalPlayer.Gui.Screen
getgenv().old=workspace.CurrentCamera;old.Parent=game:GetService("ServerStorage")
getgenv().calls=0;getgenv().changed=0
s.Frame.Child:GetPropertyChangedSignal("AbsoluteSize"):Connect(function()calls+=1 end)
s.Changed:Connect(function(key)if key=="AbsoluteSize"then changed+=1 end end)
workspace.CurrentCamera=Instance.new("Camera",workspace)
''')
        profile = {**PROFILE, 'size': [1200, 900]}
        self.assertTrue(self.update(host, display=profile)['ok'])
        self.assertEqual(self.execute(host, '''return calls,changed,s.Frame.Child.AbsoluteSize.X,
 s.Frame.Child.AbsolutePosition.Y,old.ViewportSize.X,workspace.CurrentCamera.ViewportSize.X'''),
                         [1, 1, 300, 245, 980, 1180])
        self.assertTrue(self.update(host, display=profile)['ok'])
        self.assertEqual(self.execute(host, 'return calls,changed'), [1, 1])

    def test_local_ancestor_change_and_ignore_inset_recalculate(self):
        host = self.host()
        self.assertEqual(self.execute(host, '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
s.IgnoreGuiInset=true
local y,h=s.AbsolutePosition.Y,s.AbsoluteSize.Y
s.Frame.Position=UDim2.new(0,100,0,200)
return y,h,s.Frame.Child.AbsolutePosition.X,s.Frame.Child.AbsolutePosition.Y,
 not pcall(function()s.AbsoluteSize=Vector2.new(3,4)end)
'''), [-40, 800, -140, -10, True])

    def test_full_surface_and_explicit_topbar_rectangle(self):
        s = layout_snapshot({**PROFILE, 'topbar_insets': [100, 0, 200, 760]})
        s['nodes'][-3]['properties']['ScreenInsets'] = 3
        self.assertEqual(self.execute(self.host(s), '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
return s.AbsoluteSize.X,s.AbsoluteSize.Y,s.AbsolutePosition.X,s.AbsolutePosition.Y
'''), [700, 40, 90, -40])
        s['nodes'][-3]['properties']['ScreenInsets'] = 0
        self.assertEqual(self.execute(self.host(s), '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
return s.AbsoluteSize.X,s.AbsoluteSize.Y,s.AbsolutePosition.X,s.AbsolutePosition.Y
'''), [1000, 800, -10, -40])

    def test_headless_and_detached_roots_have_no_display_area(self):
        self.assertEqual(self.execute(self.host(layout_snapshot(None)), '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
return s.AbsoluteSize.X,s.AbsoluteSize.Y
'''), [0, 0])
        self.assertEqual(self.execute(self.host(), '''
local s=game:GetService("Players").LocalPlayer.Gui.Screen
s.Parent=nil;return s.AbsoluteSize.X,s.AbsoluteSize.Y
'''), [0, 0])

    def test_bad_host_profile_does_not_partially_update_native_state(self):
        host = self.host()
        for bad in [{'size': [12, True]}, {'size': [40, 30], 'core_insets': [0, 40, 0, 0]},
                    {'size': [300, 200], 'extra': 1}, None]:
            with self.subTest(bad=bad):
                self.assertFalse(self.update(host, display=bad)['ok'])
                self.assertEqual(self.execute(host, 'return workspace.CurrentCamera.ViewportSize.X'), [980])

    def test_replication_cannot_overwrite_computed_dimensions(self):
        host = self.host()
        result = self.update(host, actions=[{'sequence': 8, 'kind': 'set', 'id': 'Screen',
                                            'fields': {'AbsoluteSize': {'$vector2': [9, 9]}}}])
        self.assertTrue(result['ok'])
        self.assertEqual(self.execute(host, 'return game:GetService("Players").LocalPlayer.Gui.Screen.AbsoluteSize.X'), [980])

    def test_automatic_layout_accepts_scale_content_with_known_visibility(self):
        s = layout_snapshot()
        s['nodes'][-2]['properties']['AutomaticSize'] = 1
        # The authored size remains the minimum when scaled children fit.
        s['nodes'][-1]['properties']['Visible'] = True
        self.assertEqual(self.execute(self.host(s), '''
local f=game:GetService("Players").LocalPlayer.Gui.Screen.Frame
return f.AbsoluteSize.X,f.AbsoluteSize.Y,f.Child.AbsoluteSize.X,f.Child.AbsoluteSize.Y
'''), [500,360,250,180])

    def test_automatic_layout_requires_child_visibility_evidence(self):
        s = layout_snapshot()
        s['nodes'][-2]['properties']['AutomaticSize'] = 1
        self.assertEqual(self.execute(self.host(s), '''
local ok,err=pcall(function()return game:GetService("Players").LocalPlayer.Gui.Screen.Frame.AbsoluteSize end)
return ok,string.find(err,"content visibility unavailable",1,true)~=nil
'''), [False, True])

    def test_adapter_queues_before_vm_start_and_updates_same_vm(self):
        world, _ = fixture()
        runtime = PersistentClientScriptRuntime(lambda: world, 123)
        self.addCleanup(runtime.close)
        profile = copy.deepcopy(PROFILE)
        self.assertTrue(runtime.update_display(profile)['ok'])
        profile['size'][0] = 200
        runtime._ensure()
        host = runtime.host
        self.assertEqual(self.execute(host, 'return workspace.CurrentCamera.ViewportSize.X'), [980])
        self.assertTrue(runtime.update_display({'size': [640, 480]})['ok'])
        self.assertIs(runtime.host, host)
        self.assertEqual(self.execute(host, 'return workspace.CurrentCamera.ViewportSize.X'), [640])

    def test_profile_validation_and_copying(self):
        profile = copy.deepcopy(PROFILE)
        result = validate_display(profile)
        profile['size'][0] = 1
        self.assertEqual(result['size'], [1000, 800])
        for bad in [[], {'size': [True, 2]}, {'size': [float('nan'), 2]},
                    {'size': [100, 100], 'device_insets': [10, 0, 0, 0], 'core_insets': [0, 0, 0, 0]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_display(bad)


if __name__ == '__main__':
    unittest.main()
