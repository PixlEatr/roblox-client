"""Camera projection and host viewport lifecycle in real source workers."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

import test_cframe_transforms as transforms


def snapshot(viewport=None):
    result=copy.deepcopy(transforms.SNAPSHOT)
    result['viewport']=viewport or {'size':[800,600]}
    return result


class CameraProjectionTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def host(self, data=None):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(data or snapshot())
        self.addCleanup(h.close)
        return h

    def execute(self,h,source):
        result=h.call({'op':'execute','source':transforms.HELPERS+source})
        self.assertTrue(result['ok'],result);self.assertTrue(result['completed'],result)
        self.assertFalse(result['task_errors'],result)
        return result['values']

    def update(self,h,viewport,actions=None):
        return h.call({'op':'update','delta':{'version':1,'delta':True,'actions':actions or [],'viewport':viewport}})

    def test_analytic_projection_depth_bounds_and_unit_rays(self):
        self.run_script('''
local c=workspace.CurrentCamera
c.CFrame=CFrame.identity;c.FieldOfView=90
assert(c.ViewportSize==Vector2.new(800,600))
local p,on=c:WorldToViewportPoint(Vector3.new(0,0,-10));vec(p,Vector3.new(400,300,10));assert(on)
p,on=c:WorldToViewportPoint(Vector3.new(5,5,-10));vec(p,Vector3.new(550,150,10));assert(on)
p,on=c:WorldToViewportPoint(Vector3.new(20,0,-10));vec(p,Vector3.new(1000,300,10));assert(not on)
p,on=c:WorldToViewportPoint(Vector3.new(0,0,10));near(p.Z,-10);assert(not on)
local r=c:ViewportPointToRay(400,300)
assert(typeof(r)=="Ray");vec(r.Origin,Vector3.new(0,0,c.NearPlaneZ));vec(r.Direction,-Vector3.zAxis)
r=c:ViewportPointToRay(400,300,5);vec(r.Origin,Vector3.new(0,0,c.NearPlaneZ-5))
r=c:ViewportPointToRay(550,150);vec(r.Direction,Vector3.new(.5,.5,-1).Unit)
near(r.Direction.Magnitude,1)
''',snapshot())

    def test_off_axis_ray_depth_starts_after_measured_near_plane(self):
        self.run_script('''
local c=workspace.CurrentCamera;c.CFrame=CFrame.identity;c.FieldOfView=90
local direction=Vector3.new(.5,.5,-1).Unit
local forward=direction:Dot(c.CFrame.LookVector)
local r=c:ViewportPointToRay(550,150)
vec(r.Direction,direction);vec(r.Origin,direction*(-c.NearPlaneZ/forward))
near(c.CFrame:PointToObjectSpace(r.Origin).Z,c.NearPlaneZ)
r=c:ViewportPointToRay(550,150,5)
vec(r.Origin,direction*(5-c.NearPlaneZ/forward))
near((r.Origin-c.CFrame.Position).Magnitude,5-c.NearPlaneZ/forward)
vec(c:ScreenPointToRay(550,150,5).Origin,r.Origin)
''',snapshot())

    def test_projection_inversion_with_camera_translation_and_rotation(self):
        self.run_script('''
local c=workspace.CurrentCamera
c.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.3,.7,-.2);c.FieldOfView=70
for _,pixel in {Vector2.new(400,300),Vector2.new(150,200),Vector2.new(650,400)} do
    local ray=c:ViewportPointToRay(pixel.X,pixel.Y)
    near(c.CFrame:PointToObjectSpace(ray.Origin).Z,c.NearPlaneZ)
    local projected,visible=c:WorldToViewportPoint(ray.Origin+ray.Direction*50)
    assert(math.abs(projected.X-pixel.X)<1e-4 and math.abs(projected.Y-pixel.Y)<1e-4)
    assert(visible)
end
local center=c:ViewportPointToRay(400,300)
vec(center.Direction,c.CFrame.LookVector)
c.Focus=CFrame.new(1,2,3)
vec(c:ViewportPointToRay(400,300).Direction,center.Direction)
''',snapshot())

    def test_safe_area_gui_inset_and_screen_coordinates(self):
        self.run_script('''
local c=workspace.CurrentCamera;c.CFrame=CFrame.identity;c.FieldOfView=90
assert(c.ViewportSize==Vector2.new(740,560))
local point=Vector3.new(0,0,-10)
local a,visible=c:WorldToViewportPoint(point);vec(a,Vector3.new(380,290,10));assert(visible)
local b,screenVisible=c:WorldToScreenPoint(point);vec(b,Vector3.new(375,254,10));assert(screenVisible)
local offAxis=c:WorldToViewportPoint(Vector3.new(5,5,-10));vec(offAxis,Vector3.new(530,140,10))
vec(c:ViewportPointToRay(a.X,a.Y).Direction,-Vector3.zAxis)
vec(c:ScreenPointToRay(b.X,b.Y).Direction,-Vector3.zAxis)
local viewport=c:ViewportPointToRay(100,100,0)
local screen=c:ScreenPointToRay(95,64,0)
vec(screen.Direction,viewport.Direction);vec(screen.Origin,viewport.Origin)
''',snapshot({'size':[800,600],'device_safe_insets':[20,10,40,30],'gui_inset':[5,36]}))

    def test_readonly_viewport_fov_clamping_and_invalid_receivers(self):
        self.run_script('''
local c=workspace.CurrentCamera;c.CFrame=CFrame.identity
for _,bad in {
 function()c.ViewportSize=Vector2.new(100,100)end,
 function()c:ViewportPointToRay(math.huge,0)end,
 function()c:ViewportPointToRay(0,0,0/0)end,
 function()c:WorldToViewportPoint({})end,
 function()c.WorldToViewportPoint(Instance.new("Part"),Vector3.zero)end,
 function()c.FieldOfView=0/0 end,
} do assert(not pcall(bad))end
c.FieldOfView=200;assert(c.FieldOfView==120)
c.FieldOfView=-10;assert(c.FieldOfView==1)
c.FieldOfView=90;assert(c.FieldOfView==90)
assert(client.propertyOrigin(c,"ViewportSize")=="local_viewport")
assert(c.ViewportSize==Vector2.new(800,600))
''',snapshot())

    def test_unknown_camera_rotation_remains_unavailable(self):
        data=snapshot()
        data['nodes'].append({'id':'camera:unknown','class_name':'Camera','name':'UnknownCamera','parent':'scope:workspace','properties':{
            'FieldOfView':70,'CFrame':{'$cframe':{'position':[0,0,0],'rotation_id':17}}}})
        ws=next(n for n in data['nodes'] if n['id']=='scope:workspace')
        ws['properties']['CurrentCamera']={'$ref':'camera:unknown'}
        self.run_script('''
local c=workspace.CurrentCamera
assert(c.ViewportSize==Vector2.new(800,600))
assert(not pcall(function()c:ViewportPointToRay(400,300)end))
assert(not pcall(function()c:WorldToViewportPoint(Vector3.new(0,0,-5))end))
''',data)

    def test_camera_selection_preserves_cameras_stored_in_folders(self):
        self.run_script('''
local folder=Instance.new("Folder",workspace)
local first=workspace.CurrentCamera;first.Parent=folder
local second=Instance.new("Camera",folder)
workspace.CurrentCamera=second
assert(first.Parent==folder and second.Parent==folder)
workspace.CurrentCamera=first
assert(workspace.CurrentCamera==first and second.Parent==folder)
assert(first.ViewportSize==Vector2.new(800,600))
''',snapshot())

    def test_current_camera_resizes_without_updating_stored_camera(self):
        h=self.host()
        self.execute(h, '''
local folder=Instance.new("Folder",workspace)
-- Cameras under Workspace are destroyed on selection; store this one outside it.
getgenv().old=workspace.CurrentCamera;old.Parent=game:GetService("ServerStorage")
getgenv().nextCamera=Instance.new("Camera",folder)
assert(nextCamera.ViewportSize==Vector2.new(1,1))
workspace.CurrentCamera=nextCamera
assert(nextCamera.ViewportSize==Vector2.new(800,600))
getgenv().changes=0;getgenv().oldChanges=0
nextCamera:GetPropertyChangedSignal("ViewportSize"):Connect(function()changes+=1 end)
old:GetPropertyChangedSignal("ViewportSize"):Connect(function()oldChanges+=1 end)
nextCamera.CFrame=CFrame.identity;nextCamera.FieldOfView=90
''')
        self.assertTrue(self.update(h,{'size':[1200,600]})['ok'])
        self.assertEqual(self.execute(h,'''local p=nextCamera:WorldToViewportPoint(Vector3.new(0,0,-5))
return changes,oldChanges,old.ViewportSize.X,nextCamera.ViewportSize.X,p.X'''),[1,0,800,1200,600])
        self.assertTrue(self.update(h,{'size':[1200,600]})['ok'])
        self.assertEqual(self.execute(h,'return changes,oldChanges'),[1,0])
        self.execute(h,'old.Parent=workspace;workspace.CurrentCamera=old;assert(old.ViewportSize==Vector2.new(1200,600))')

    def test_invalid_updates_preserve_geometry_and_replicated_fields_cannot_override(self):
        h=self.host()
        camera_id=self.execute(h,'return workspace.CurrentCamera')[0]['$ref']
        for bad in [{'size':[0,600]},{'size':[800,False]},{'size':[800,600],'device_safe_insets':[400,0,400,0]},
                    {'size':[800,600],'gui_inset':[-1,0]},{'size':[800,600],'unknown':1},{},[]]:
            with self.subTest(viewport=bad):
                self.assertFalse(self.update(h,bad)['ok'])
                self.assertEqual(self.execute(h,'return workspace.CurrentCamera.ViewportSize.X'),[800])
        self.execute(h,'getgenv().changed=0;workspace.CurrentCamera:GetPropertyChangedSignal("ViewportSize"):Connect(function()changed+=1 end)')
        result=self.update(h,{'size':[800,600]},[{'sequence':8,'kind':'set','id':camera_id,'fields':{'ViewportSize':{'$vector2':[9999,9999]}}}])
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.execute(h,'return workspace.CurrentCamera.ViewportSize.X,changed'),[800,0])

    def test_absent_viewport_retains_initial_size_and_global_replacement_is_safe(self):
        h=self.host(copy.deepcopy(transforms.SNAPSHOT))
        self.assertEqual(self.execute(h,'return workspace.CurrentCamera.ViewportSize.X,workspace.CurrentCamera.ViewportSize.Y'),[1,1])
        self.execute(h,'''getgenv().Ray={new=function()error("global constructor called")end}
getgenv().Vector2={new=function()error("global constructor called")end}''')
        self.assertTrue(self.update(h,{'size':[800,600]})['ok'])
        self.assertEqual(self.execute(h,'''local c=workspace.CurrentCamera;c.CFrame=CFrame.identity
return c.ViewportSize.X,typeof(c:ViewportPointToRay(400,300)),c:ViewportPointToRay(400,300).Direction.Z'''),[800,'Ray',-1])

    def test_world_adapter_copies_frontend_viewport_into_worker(self):
        from test_world_snapshot import fixture
        world,_=fixture()
        frontend={'size':[1024,768],'gui_inset':[0,36]}
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            runtime=transforms.persistent_runtime.PersistentClientScriptRuntime(lambda:world,123,viewport=frontend)
            self.addCleanup(runtime.close)
            frontend['size'][0]=1
            runtime._ensure()
        self.assertEqual(self.execute(runtime.host,'return workspace.CurrentCamera.ViewportSize.X,workspace.CurrentCamera.ViewportSize.Y'),[1024,768])

    def test_display_and_camera_share_full_surface_and_gui_offsets(self):
        h=self.host(snapshot({'size':[800,600],'device_safe_insets':[20,10,40,30],'gui_inset':[5,36]}))
        self.assertEqual(self.execute(h,'''local inset=game:GetService("GuiService"):GetGuiInset()
local c=workspace.CurrentCamera;c.CFrame=CFrame.identity;c.FieldOfView=90
local p=c:WorldToScreenPoint(Vector3.new(0,0,-10))
return inset.X,inset.Y,p.X,p.Y'''),[25,46,375,254])
        result=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[1000,700],'device_insets':[10,20,30,40],'core_insets':[20,60,30,40]}}})
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.execute(h,'''local c=workspace.CurrentCamera
local inset=game:GetService("GuiService"):GetGuiInset()
local p=c:WorldToScreenPoint(Vector3.new(0,0,-10))
return c.ViewportSize.X,c.ViewportSize.Y,inset.X,inset.Y,p.X,p.Y'''),[960,640,20,60,480,290])

    def test_inset_only_update_changes_screen_projection_without_resize_event(self):
        h=self.host()
        self.execute(h,'''local c=workspace.CurrentCamera;c.CFrame=CFrame.identity;c.FieldOfView=90
getgenv().resizeCalls=0;c:GetPropertyChangedSignal("ViewportSize"):Connect(function()resizeCalls+=1 end)''')
        self.assertTrue(self.update(h,{'size':[800,600],'gui_inset':[0,40]})['ok'])
        self.assertEqual(self.execute(h,'''local c=workspace.CurrentCamera
local p=c:WorldToScreenPoint(Vector3.new(0,0,-10))
return resizeCalls,c.ViewportSize.Y,p.Y,c:ScreenPointToRay(400,260).Direction.Z'''),[0,600,260,-1])


if __name__=='__main__':unittest.main()
