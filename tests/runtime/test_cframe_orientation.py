"""Authored camera/rotation scripts tested in both compiled Luau hosts."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

import test_cframe_transforms as transforms


class CFrameOrientationTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def test_look_at_direction_custom_up_and_vertical_poles(self):
        self.run_script('''
local p=Vector3.new(3,4,5)
frame(CFrame.lookAt(p,p-Vector3.zAxis),CFrame.new(p))
frame(CFrame.new(p,p-Vector3.zAxis),CFrame.new(p))
local along=CFrame.lookAlong(p,Vector3.xAxis)
vec(along.Position,p);vec(along.LookVector,Vector3.xAxis)
vec(along.UpVector,Vector3.yAxis);vec(along.RightVector,Vector3.zAxis)
frame(along,CFrame.lookAt(p,p+Vector3.xAxis))
local custom=CFrame.lookAlong(p,-Vector3.zAxis,Vector3.xAxis)
vec(custom.UpVector,Vector3.xAxis)
vec(custom.RightVector,-Vector3.yAxis)
for _,d in {Vector3.yAxis,-Vector3.yAxis} do
    local vertical=CFrame.lookAlong(p,d)
    vec(vertical.LookVector,d);vec(vertical.UpVector,Vector3.xAxis)
    frame(vertical:Inverse()*vertical,CFrame.identity)
end
local tilted=CFrame.lookAlong(p,Vector3.new(1,2,3),Vector3.new(0,2,0))
local length=math.sqrt(14)
vec(tilted.LookVector,Vector3.new(1/length,2/length,3/length))
frame(tilted:Inverse()*tilted,CFrame.identity)
''')

    def test_quaternion_constructor_normalizes_and_preserves_position(self):
        self.run_script('''
local s=math.sqrt(.5)
for _,scale in {1,2,-3,1e300,1e-300} do
    frame(CFrame.new(1,2,3,0,s*scale,0,s*scale),
          CFrame.new(1,2,3)*CFrame.Angles(0,math.pi/2,0))
end
frame(CFrame.new(0,0,0,1,0,0,0),CFrame.Angles(math.pi,0,0))
frame(CFrame.new(0,0,0,0,1,0,0),CFrame.Angles(0,math.pi,0))
frame(CFrame.new(0,0,0,0,0,1,0),CFrame.Angles(0,0,math.pi))
frame(CFrame.new(0,0,0,0,0,0,-2),CFrame.identity)
''')

    def test_lerp_uses_constant_angle_speed_and_exact_endpoints(self):
        self.run_script('''
local a=CFrame.new(2,4,6)
local b=CFrame.new(10,12,14)*CFrame.Angles(0,2*math.pi/3,0)
assert(a:Lerp(b,0)==a);assert(a:Lerp(b,1)==b)
for _,alpha in {0,.1,.25,.5,.75,.9,1} do
    local value=a:Lerp(b,alpha)
    vec(value.Position,Vector3.new(2+8*alpha,4+8*alpha,6+8*alpha))
    frame(value.Rotation,CFrame.Angles(0,2*math.pi/3*alpha,0))
    frame(value.Rotation:Inverse()*value.Rotation,CFrame.identity)
end
frame(a:Lerp(b,.4):Lerp(a:Lerp(b,.6),.5),a:Lerp(b,.5))
frame(a:Lerp(a,.123),a)
frame(a:Lerp(CFrame.new(10,12,14),.25),CFrame.new(4,6,8))
''')

    def test_lerp_shortest_arc_half_turn_and_tiny_rotations(self):
        self.run_script('''
local a=CFrame.Angles(0,math.rad(170),0)
local b=CFrame.Angles(0,math.rad(-170),0)
frame(a:Lerp(b,.5),CFrame.Angles(0,math.pi,0))
near(a:AngleBetween(b),math.rad(20))
near((a+Vector3.new(100,200,300)):AngleBetween(b),math.rad(20))
for _,axis in {Vector3.xAxis,Vector3.yAxis,Vector3.zAxis,Vector3.new(1,2,3)} do
    local half=CFrame.fromAxisAngle(axis,math.pi)
    local mid=CFrame.identity:Lerp(half,.5)
    near(CFrame.identity:AngleBetween(mid),math.pi/2)
    frame(mid*mid,half)
    local tiny=CFrame.fromAxisAngle(axis,1e-7)
    local value=CFrame.identity:Lerp(tiny,.3)
    near(value:AngleBetween(CFrame.identity),3e-8)
    frame(value:Inverse()*value,CFrame.identity)
end
''')

    def test_axis_angle_and_euler_round_trips_including_gimbal_lock(self):
        self.run_script('''
for _,angle in {0,1e-7,.4,math.pi/2,math.pi,-math.pi,-1.5} do
    for _,axis in {Vector3.xAxis,Vector3.yAxis,Vector3.zAxis,Vector3.new(1,-2,3)} do
        local original=CFrame.fromAxisAngle(axis,angle)
        local v,r=original:ToAxisAngle()
        near(v.X*v.X+v.Y*v.Y+v.Z*v.Z,1)
        frame(CFrame.fromAxisAngle(v,r),original)
    end
end
for _,pitch in {-math.pi/2,-math.pi/2+1e-8,-.5,0,.7,math.pi/2-1e-8,math.pi/2} do
    for _,yaw in {-2,.3,2.5} do
        local xyz=CFrame.Angles(yaw,pitch,-.8)
        frame(CFrame.Angles(xyz:ToEulerAnglesXYZ()),xyz)
        local yxz=CFrame.fromOrientation(pitch,yaw,-.8)
        frame(CFrame.fromOrientation(yxz:ToOrientation()),yxz)
        frame(CFrame.fromEulerAnglesYXZ(yxz:ToEulerAnglesYXZ()),yxz)
    end
end
local original=CFrame.new(4,5,6)*CFrame.Angles(.2,.3,.4)
frame(CFrame.new(original:components()),original)
''')

    def test_rotation_between_vectors_and_orthonormalization(self):
        self.run_script('''
vec(CFrame.fromRotationBetweenVectors(Vector3.yAxis,Vector3.xAxis)*Vector3.yAxis,Vector3.xAxis)
for _,v in {Vector3.xAxis,Vector3.yAxis,Vector3.zAxis,Vector3.new(1,2,3)} do
    frame(CFrame.fromRotationBetweenVectors(v,v),CFrame.identity)
    local opposite=CFrame.fromRotationBetweenVectors(v,-v)
    vec(opposite*v,-v);near(opposite:AngleBetween(CFrame.identity),math.pi)
end
local from=Vector3.new(2,0,0)
vec(CFrame.fromRotationBetweenVectors(from,Vector3.new(0,5,0))*from,Vector3.new(0,2,0))
local basis=CFrame.fromMatrix(Vector3.new(4,5,6),Vector3.new(2,0,0),Vector3.new(1,3,0))
vec(basis.ZVector,Vector3.zAxis)
vec(basis.XVector,Vector3.new(2,0,0))
frame(basis:Orthonormalize(),CFrame.new(4,5,6))
local rotated=CFrame.Angles(.4,.7,-.8)*basis
frame(rotated:Orthonormalize().Rotation,CFrame.Angles(.4,.7,-.8))
''')

    def test_invalid_inputs_and_unknown_rotation_remain_explicit(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in snapshot['nodes'] if n['id']=='scope:root-1')
        root['properties']['CFrame']={'$cframe':{'position':[1,2,3],'rotation_id':'unknown'}}
        self.run_script('''
local unknown=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart.CFrame
for _,f in {
    function()return CFrame.lookAt(Vector3.zero,Vector3.zero)end,
    function()return CFrame.lookAlong(Vector3.zero,Vector3.xAxis,Vector3.zero)end,
    function()return CFrame.new(0,0,0,0,0,0,0)end,
    function()return CFrame.new(0,0,0,0,0,0,math.huge)end,
    function()return CFrame.identity:Lerp(CFrame.identity,0/0)end,
    function()return CFrame.identity:Lerp(unknown,0)end,
    function()return unknown:ToAxisAngle()end,
    function()return unknown:ToOrientation()end,
    function()return unknown:Orthonormalize()end,
    function()return CFrame.fromRotationBetweenVectors(Vector3.zero,Vector3.xAxis)end,
    function()return CFrame.fromMatrix(Vector3.zero,Vector3.xAxis,Vector3.xAxis)end,
} do assert(not pcall(f))end
frame(CFrame.lookAlong(Vector3.zero,-Vector3.zAxis),CFrame.identity)
''',snapshot)

    def test_persistent_camera_animation_keeps_orientation_between_jobs(self):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            host=transforms.persistent_runtime.PersistentLuauHost(copy.deepcopy(transforms.SNAPSHOT))
        self.addCleanup(host.close)
        first=host.call({'op':'execute','source':transforms.HELPERS+'''
getgenv().startPose=CFrame.lookAt(Vector3.new(0,3,10),Vector3.new(0,3,0))
getgenv().endPose=CFrame.lookAt(Vector3.new(10,3,0),Vector3.new(0,3,0))
workspace.CurrentCamera.CFrame=startPose
return true
'''})
        self.assertTrue(first['ok'],first)
        for alpha in (.25,.5,.75,1):
            result=host.call({'op':'execute','source':transforms.HELPERS+f'''
workspace.CurrentCamera.CFrame=startPose:Lerp(endPose,{alpha})
local cf=workspace.CurrentCamera.CFrame
vec(cf.Position,Vector3.new(10*{alpha},3,10*(1-{alpha})))
near(startPose:AngleBetween(cf),math.pi/2*{alpha})
frame(cf,workspace.CurrentCamera.CoordinateFrame)
return true
'''})
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['values'],[True])
            self.assertFalse(result['task_errors'],result)


if __name__=='__main__':unittest.main()
