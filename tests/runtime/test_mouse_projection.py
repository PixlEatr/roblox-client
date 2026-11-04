"""Host cursor and display -> legacy Mouse coordinates and perspective ray."""
import unittest
from persistent_runtime import PersistentLuauHost
import test_camera_projection as camera
import test_motion_math as motion
import test_cframe_transforms as transforms

PROFILE={'size':[800,600],'device_safe_insets':[20,10,40,30],'gui_inset':[5,36]}


class MouseProjectionTests(unittest.TestCase):
    execute=motion.MotionMathTests.execute

    def setUp(self):
        snapshot=camera.snapshot(PROFILE)
        snapshot['input_capabilities']={'MouseEnabled':True,'KeyboardEnabled':True}
        self.host=PersistentLuauHost(snapshot);self.addCleanup(self.host.close)
        self.execute('mouse=game:GetService("Players").LocalPlayer:GetMouse();camera=workspace.CurrentCamera;camera.CFrame=CFrame.identity;camera.FieldOfView=90')

    def update(self,**fields):
        result=self.host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'actions':[],**fields}})
        self.assertTrue(result['ok'],result.get('error'));self.assertFalse(result.get('task_errors'))
        return result

    def move(self,x,y):
        return self.update(input_events=[{'input_type':'MouseMovement','phase':'change',
            'position':[x,y,0],'mouse_location':[x,y],'delta':[0,0,0]}])

    def check(self,source):
        return self.execute(transforms.HELPERS+source)['values']

    def test_position_is_below_gui_inset_and_view_size_includes_topbar(self):
        self.move(400,300)
        self.assertEqual(self.check('return mouse.X,mouse.Y,mouse.ViewSizeX,mouse.ViewSizeY'),[375,254,800,600])
        self.assertEqual(self.check('''
return client.propertyOrigin(mouse,"X"),client.propertyOrigin(mouse,"Y"),
 client.propertyOrigin(mouse,"ViewSizeX"),client.propertyOrigin(mouse,"UnitRay")
'''),['local_input','local_input','local_display','local_camera'])

    def test_move_handler_reads_updated_values_without_property_change_signals(self):
        self.execute('''
observed={};changed=0
mouse.Move:Connect(function()table.insert(observed,{mouse.X,mouse.Y})end)
mouse:GetPropertyChangedSignal("X"):Connect(function()changed+=1 end)
mouse:GetPropertyChangedSignal("Y"):Connect(function()changed+=1 end)
mouse.Changed:Connect(function(key)if key=="X" or key=="Y" then changed+=1 end end)
''')
        self.move(125,146);self.move(225,246)
        self.assertEqual(self.check('return observed[1][1],observed[1][2],observed[2][1],observed[2][2],changed'),[100,100,200,200,0])

    def test_origin_and_unit_ray_start_at_camera_not_near_plane(self):
        self.move(400,300)
        self.check('''
camera.CFrame=CFrame.new(10,20,30)
local ray=mouse.UnitRay;assert(typeof(ray)=="Ray")
vec(ray.Origin,Vector3.new(10,20,30));vec(ray.Direction,-Vector3.zAxis);near(ray.Direction.Magnitude,1)
vec(mouse.Origin.Position,ray.Origin);vec(mouse.Origin.LookVector,ray.Direction)
local cameraRay=camera:ScreenPointToRay(mouse.X,mouse.Y)
assert((cameraRay.Origin-ray.Origin).Magnitude>0)
vec(cameraRay.Direction,ray.Direction)
''')

    def test_off_axis_translation_rotation_and_projection_inversion(self):
        self.move(550,150)
        self.check('''
vec(mouse.UnitRay.Direction,Vector3.new(.5,.5,-1).Unit)
camera.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.3,.7,-.2);camera.FieldOfView=70
local ray=mouse.UnitRay
vec(ray.Origin,camera.CFrame.Position);near(ray.Direction.Magnitude,1)
local pixel=camera:WorldToScreenPoint(ray.Origin+ray.Direction*50)
assert(math.abs(pixel.X-mouse.X)<1e-3 and math.abs(pixel.Y-mouse.Y)<1e-3)
vec(mouse.Origin.LookVector,ray.Direction)
near(mouse.Origin.RightVector:Dot(mouse.Origin.UpVector),0)
near(mouse.Origin.RightVector.Magnitude,1);near(mouse.Origin.UpVector.Magnitude,1)
''')

    def test_resize_inset_and_current_camera_changes_refresh_without_new_input(self):
        self.move(400,300)
        self.update(viewport={'size':[1000,800],'gui_inset':[0,50]})
        self.assertEqual(self.check('return mouse.X,mouse.Y,mouse.ViewSizeX,mouse.ViewSizeY'),[400,250,1000,800])
        self.check('''
camera.CFrame=CFrame.identity
vec(mouse.UnitRay.Direction,Vector3.new(-.25,.25,-1).Unit)
local nextCamera=Instance.new("Camera",workspace)
nextCamera.CFrame=CFrame.new(50,60,70)*CFrame.Angles(0,math.pi/2,0)
nextCamera.FieldOfView=90;workspace.CurrentCamera=nextCamera
vec(mouse.UnitRay.Origin,nextCamera.CFrame.Position)
vec(mouse.UnitRay.Direction,nextCamera:ScreenPointToRay(mouse.X,mouse.Y).Direction)
nextCamera:Destroy()
vec(mouse.UnitRay.Origin,workspace.CurrentCamera.CFrame.Position)
''')

    def test_fractional_outside_and_extreme_coordinates_have_bounded_integer_values(self):
        self.move(26.9,44.1)
        self.assertEqual(self.check('return mouse.X,mouse.Y'),[1,-1])
        self.move(-20,700)
        self.assertEqual(self.check('return mouse.X,mouse.Y'),[-45,654])
        self.check('near(mouse.UnitRay.Direction.Magnitude,1)')
        self.move(1e30,-1e30)
        self.assertEqual(self.check('return mouse.X,mouse.Y'),[2147483647,-2147483648])
        self.check('near(mouse.UnitRay.Direction.Magnitude,1)')

    def test_mouse_properties_are_readonly_and_inputobject_edits_do_not_move_cursor(self):
        self.execute('game:GetService("UserInputService").InputChanged:Connect(function(i)lastInput=i end)')
        self.move(400,300)
        self.check('''
local before=mouse.UnitRay
for _,name in ipairs({"X","Y","ViewSizeX","ViewSizeY","Origin","UnitRay"})do
 assert(not pcall(function()mouse[name]=mouse[name]end))
end
lastInput.Position=Vector3.new(999,999,999)
assert(mouse.X==375 and mouse.Y==254);vec(mouse.UnitRay.Direction,before.Direction)
''')

    def test_vertical_and_rolled_cameras_produce_finite_orthogonal_origin(self):
        self.move(400,300)
        self.check('''
for _,cf in ipairs({CFrame.Angles(math.pi/2,0,0),CFrame.Angles(-math.pi/2,0,0),CFrame.Angles(0,0,math.pi/2)})do
 camera.CFrame=cf;local o=mouse.Origin
 vec(o.LookVector,mouse.UnitRay.Direction);near(o.RightVector.Magnitude,1);near(o.UpVector.Magnitude,1)
 near(o.RightVector:Dot(o.UpVector),0);near(o.UpVector:Dot(o.LookVector),0)
end
vec(mouse.Origin.UpVector,Vector3.yAxis)
''')

    def test_detached_display_reports_zero_size_and_rejects_ray_until_reattached(self):
        self.move(400,300)
        self.update(display={'size':[0,0]})
        self.check('''assert(mouse.ViewSizeX==0 and mouse.ViewSizeY==0)
assert(not pcall(function()return mouse.UnitRay end))
assert(not pcall(function()return mouse.Origin end))''')
        self.update(display={'size':[800,600]})
        self.check('vec(mouse.UnitRay.Direction,-Vector3.zAxis)')

    def test_destroyed_mouse_cannot_follow_new_input_but_recreated_mouse_can(self):
        self.move(100,100)
        self.execute('oldMouse=mouse;mouse:Destroy()')
        self.move(400,300)
        self.check('''assert(not pcall(function()return oldMouse.UnitRay end))
mouse=game:GetService("Players").LocalPlayer:GetMouse();assert(mouse~=oldMouse)
assert(mouse.X==375 and mouse.Y==254);vec(mouse.UnitRay.Direction,-Vector3.zAxis)''')

    def test_invalid_host_update_preserves_coordinates_and_ray(self):
        self.move(400,300)
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'input_events':[{'input_type':'MouseMovement','phase':'change','mouse_location':[True,5]}]}})
        self.assertFalse(result['ok'])
        self.check('assert(mouse.X==375 and mouse.Y==254);vec(mouse.UnitRay.Direction,-Vector3.zAxis)')


class MouseProjectionBothHostsTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_properties_and_perspective_ray_are_available_in_both_hosts(self):
        self.run_script('''
local m=game:GetService("Players").LocalPlayer:GetMouse()
local c=workspace.CurrentCamera;c.CFrame=CFrame.identity;c.FieldOfView=90
assert(m.X==-25 and m.Y==-46 and m.ViewSizeX==800 and m.ViewSizeY==600)
vec(m.UnitRay.Origin,c.CFrame.Position)
vec(m.UnitRay.Direction,Vector3.new(-4/3,1,-1).Unit)
vec(m.Origin.LookVector,m.UnitRay.Direction)
assert(not pcall(function()m.X=4 end))
''',camera.snapshot(PROFILE))

if __name__=='__main__':unittest.main()
