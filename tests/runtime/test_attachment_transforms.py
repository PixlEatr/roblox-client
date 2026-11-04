"""Attachment/Bone geometry and persistent updates; no account or live server."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms
from test_part_transforms import pose


class AttachmentTransformTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def snapshot(self, attachment=None, parent='ap'):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'] += [
            {'id':'ap','class_name':'Part','name':'AttachmentPart','parent':'scope:workspace','properties':{'CFrame':pose((10,0,0))}},
            {'id':'att','class_name':'Attachment','name':'Point','parent':parent,'properties':attachment if attachment is not None else {'CFrame':pose((1,2,3))}},
        ]
        return data

    def host(self, snapshot=None):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(snapshot or self.snapshot())
        self.addCleanup(h.close)
        self.execute(h,'getgenv().p=workspace.AttachmentPart;getgenv().a=p.Point')
        return h

    def execute(self,h,source):
        r=h.call({'op':'execute','source':transforms.HELPERS+source,'timeout_ms':1000})
        self.assertTrue(r['ok'],r);self.assertFalse(r['task_errors'],r);return r

    def update(self,h,node,fields,sequence=1,ok=True):
        r=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':transforms.SNAPSHOT['sequence']+sequence,'kind':'set','id':node,'fields':fields}]},'timeout_ms':100})
        self.assertEqual(r['ok'],ok,r)
        if ok:self.assertFalse(r['task_errors'],r)
        return r

    def test_local_pose_aliases_and_rotation_orders(self):
        self.run_script('''local a=Instance.new("Attachment")
a.Position=Vector3.new(1,2,3);a.Orientation=Vector3.new(20,30,40)
frame(a.CFrame,CFrame.new(1,2,3)*CFrame.fromOrientation(math.rad(20),math.rad(30),math.rad(40)))
a.Rotation=Vector3.new(-12,35,16)
frame(a.CFrame,CFrame.new(1,2,3)*CFrame.Angles(math.rad(-12),math.rad(35),math.rad(16)))
frame(a.WorldCFrame,a.CFrame);vec(a.WorldPosition,a.Position)
vec(a.Axis,a.CFrame.RightVector);vec(a.SecondaryAxis,a.CFrame.UpVector)
vec(a:GetAxis(),a.Axis);vec(a:GetSecondaryAxis(),a.SecondaryAxis)
local x,y,z=a.CFrame:ToOrientation();vec(a.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))''')

    def test_nested_attachments_follow_nearest_transform_through_folders(self):
        self.run_script('''local p=Instance.new("Part",workspace)
p.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.2,.4,.6)
p.PivotOffset=CFrame.new(100,0,0)
local a=Instance.new("Attachment",Instance.new("Folder",p));a.CFrame=CFrame.new(1,2,3)*CFrame.Angles(.3,.1,.7)
local b=Instance.new("Attachment",Instance.new("Folder",a));b.CFrame=CFrame.new(-4,5,6)*CFrame.Angles(.5,-.2,.8)
frame(a.WorldCFrame,p.CFrame*a.CFrame);frame(b.WorldCFrame,p.CFrame*a.CFrame*b.CFrame)
vec(b.WorldAxis,b.WorldCFrame.RightVector);vec(b.WorldSecondaryAxis,b.WorldCFrame.UpVector)
p.CFrame=CFrame.new(-3,-6,-9)*CFrame.Angles(.7,.2,.1)
frame(b.WorldCFrame,p.CFrame*a.CFrame*b.CFrame)
b.Parent=workspace;frame(b.WorldCFrame,b.CFrame)
b.Parent=nil;frame(b.WorldCFrame,b.CFrame)''')

    def test_world_setters_compute_local_pose_and_preserve_other_components(self):
        self.run_script('''local p=Instance.new("Part");p.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.2,.7,-.4)
local a=Instance.new("Attachment",p)
local target=CFrame.new(-5,12,8)*CFrame.Angles(.8,-.3,.2)
a.WorldCFrame=target;frame(a.WorldCFrame,target);frame(a.CFrame,p.CFrame:ToObjectSpace(target))
local rot=a.CFrame.Rotation;a.WorldPosition=Vector3.new(40,50,60)
vec(a.WorldPosition,Vector3.new(40,50,60));frame(a.CFrame.Rotation,rot)
a.WorldOrientation=Vector3.new(15,25,35)
frame(a.WorldCFrame,CFrame.new(40,50,60)*CFrame.fromOrientation(math.rad(15),math.rad(25),math.rad(35)))
assert(not pcall(function()a.WorldRotation=Vector3.zero end))''')

    def test_axes_normalize_and_reproject_and_deprecated_methods_work(self):
        self.run_script('''local a=Instance.new("Attachment");a.Position=Vector3.new(1,2,3)
a.Axis=Vector3.new(2,1,0);near(a.Axis.Magnitude,1);near(a.SecondaryAxis.Magnitude,1);near(a.Axis:Dot(a.SecondaryAxis),0)
vec(a.Axis,Vector3.new(2,1,0).Unit)
assert(select('#',a:SetSecondaryAxis(Vector3.new(1,3,2)))==0)
near(a.Axis:Dot(a.SecondaryAxis),0);near(a.SecondaryAxis.Magnitude,1)
frame(a.CFrame.Rotation,CFrame.fromMatrix(Vector3.zero,a.Axis,a.SecondaryAxis,a.Axis:Cross(a.SecondaryAxis)))
vec(a.Position,Vector3.new(1,2,3))
a.CFrame=CFrame.identity;assert(select('#',a:SetAxis(Vector3.yAxis))==0)
vec(a.Axis,Vector3.yAxis);near(a.Axis:Dot(a.SecondaryAxis),0)
assert(not pcall(function()a.Axis=Vector3.zero end))
assert(not pcall(function()a.SecondaryAxis=a.Axis end))
local get=a.GetAxis;assert(not pcall(get,Instance.new("Folder")))''')

    def test_world_axes_transform_directions_without_translation(self):
        self.run_script('''local p=Instance.new("Part");p.CFrame=CFrame.new(100,200,300)*CFrame.Angles(.3,.6,.9)
local a=Instance.new("Attachment",p);a.Position=Vector3.new(4,5,6)
local original=a.WorldPosition;local axis=Vector3.new(1,2,3).Unit
a.WorldAxis=axis;vec(a.WorldAxis,axis)
a.WorldSecondaryAxis=Vector3.new(-2,1,4)
near(a.WorldAxis:Dot(a.WorldSecondaryAxis),0);near(a.WorldSecondaryAxis.Magnitude,1)
vec(a.WorldPosition,original);vec(a.WorldAxis,p.CFrame:VectorToWorldSpace(a.Axis))''')

    def test_model_pivots_and_camera_ancestors(self):
        self.run_script('''local m=Instance.new("Model");m.WorldPivot=CFrame.new(10,20,30)*CFrame.Angles(.3,.4,.5)
local a=Instance.new("Attachment",m);a.CFrame=CFrame.new(1,2,3)
frame(a.WorldCFrame,m:GetPivot()*a.CFrame)
m:PivotTo(CFrame.new(40,50,60));frame(a.WorldCFrame,m:GetPivot()*a.CFrame)
local c=Instance.new("Camera");c.CFrame=CFrame.new(4,5,6)*CFrame.Angles(.1,.2,.3)
a.Parent=c;frame(a.WorldCFrame,c.CFrame*a.CFrame)''')

    def test_bone_reference_and_animated_transforms_are_distinct(self):
        self.run_script('''local p=Instance.new("MeshPart");p.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.1,.2,.3)
local a=Instance.new("Bone",p);a.CFrame=CFrame.new(1,2,3);a.Transform=CFrame.Angles(.4,.5,.6)
local b=Instance.new("Bone",a);b.CFrame=CFrame.new(4,5,6);b.Transform=CFrame.new(0,2,0)*CFrame.Angles(.2,.1,.4)
frame(a.TransformedCFrame,a.CFrame*a.Transform)
frame(b.WorldCFrame,p.CFrame*a.CFrame*b.CFrame)
frame(b.TransformedWorldCFrame,p.CFrame*a.CFrame*a.Transform*b.CFrame*b.Transform)
assert(not pcall(function()b.TransformedWorldCFrame=CFrame.identity end))
assert(not pcall(function()b.TransformedCFrame=CFrame.identity end))''')

    def test_get_constraints_returns_deduplicated_snapshot_and_tracks_disconnect(self):
        self.run_script('''local a,b=Instance.new("Attachment"),Instance.new("Attachment")
local one=Instance.new("HingeConstraint");one.Attachment0=a;one.Attachment1=a
local two=Instance.new("BallSocketConstraint");two.Attachment0=b;two.Attachment1=a
local other=Instance.new("HingeConstraint");other.Attachment0=b
local list=a:GetConstraints();assert(#list==2 and list[1]==one and list[2]==two)
one.Attachment0=b;one.Attachment1=nil;two:Destroy()
assert(#a:GetConstraints()==0 and #list==2)
local get=a.GetConstraints;assert(not pcall(get,Instance.new("Folder")))''')

    def test_unknown_local_rotation_preserves_position_without_inventing_angles(self):
        self.run_script('''local a=workspace.AttachmentPart.Point
vec(a.Position,Vector3.new(1,2,3));vec(a.WorldPosition,Vector3.new(11,2,3))
assert(not pcall(function()return a.WorldCFrame end));assert(not pcall(function()return a.Axis end))
assert(client.propertyOrigin(a,"WorldCFrame")=="unavailable")
a.WorldPosition=Vector3.new(14,5,6);vec(a.Position,Vector3.new(4,5,6))
assert(not pcall(function()return a.Orientation end))
a.WorldOrientation=Vector3.new(0,90,0)
frame(a.WorldCFrame,CFrame.new(14,5,6)*CFrame.Angles(0,math.pi/2,0))''',
            self.snapshot({'CFrame':{'$cframe':{'position':[1,2,3],'rotation_id':17}},'WorldCFrame':pose((99,99,99)),'Axis':{'$vector3':[1,0,0]}}))

    def test_unknown_parent_does_not_fall_back_to_origin(self):
        data=self.snapshot(parent='missing-parent')
        data['nodes'][0]['properties']['DetachedAttachment']={'$ref':'att'}
        # The reference avoids pretending an unresolved parent is in Workspace.
        self.run_script('''local a=workspace.DetachedAttachment
vec(a.Position,Vector3.new(1,2,3));assert(not pcall(function()return a.WorldPosition end))
assert(not pcall(function()a.WorldPosition=Vector3.new(5,6,7)end))
vec(a.Position,Vector3.new(1,2,3))''',data)

    def test_received_local_pose_and_parent_pose_refresh_world_values_and_events(self):
        h=self.host()
        self.execute(h,'''getgenv().calls=0;getgenv().positionCalls=0
a:GetPropertyChangedSignal("WorldPosition"):Connect(function()calls+=1;vec(a.WorldPosition,(p.CFrame*a.CFrame).Position)end)
a:GetPropertyChangedSignal("Position"):Connect(function()positionCalls+=1 end)''')
        self.update(h,'ap',{'CFrame':pose((20,0,0))})
        self.execute(h,'assert(calls==1 and positionCalls==0);vec(a.WorldPosition,Vector3.new(21,2,3))')
        self.update(h,'att',{'CFrame':pose((4,5,6)),'WorldPosition':{'$vector3':[99,99,99]}},2)
        self.execute(h,'assert(calls==2 and positionCalls==1);vec(a.WorldPosition,Vector3.new(24,5,6));assert(client.propertyOrigin(a,"WorldCFrame")=="derived_received")')
        self.update(h,'ap',{'CFrame':pose((20,0,0))},3)
        self.execute(h,'assert(calls==2)')

    def test_parent_changes_and_local_world_writes_notify_without_duplicate_events(self):
        h=self.host()
        self.execute(h,'''getgenv().keys={};a.Changed:Connect(function(key)keys[key]=(keys[key] or 0)+1 end)
a.WorldPosition=Vector3.new(15,6,7)''')
        self.execute(h,'assert(keys.WorldPosition==1 and keys.Position==1);keys={};a.Parent=workspace')
        self.execute(h,'assert(keys.WorldPosition==1 and keys.Parent==1);vec(a.WorldPosition,a.Position);keys={};a.Parent=workspace')
        self.execute(h,'assert(next(keys)==nil)')

    def test_invalid_received_bundle_and_mutation_budget_leave_geometry_unchanged(self):
        h=self.host()
        self.update(h,'att',{'Position':{'$vector3':[20,30,40]},'Axis':{'$vector3':[0,0,0]}},ok=False)
        self.execute(h,'''vec(a.Position,Vector3.new(1,2,3));vec(a.WorldPosition,Vector3.new(11,2,3))
local filler=Instance.new("Folder");local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()a.WorldPosition=Vector3.new(99,99,99)end))
vec(a.Position,Vector3.new(1,2,3));vec(a.WorldPosition,Vector3.new(11,2,3))''')

    def test_bone_transformed_events_track_parent_transform_only(self):
        h=self.host()
        self.execute(h,'''getgenv().b=Instance.new("Bone",p);getgenv().c=Instance.new("Bone",b)
c.CFrame=CFrame.new(1,2,3);getgenv().rest=0;getgenv().posed=0
c:GetPropertyChangedSignal("WorldCFrame"):Connect(function()rest+=1 end)
c:GetPropertyChangedSignal("TransformedWorldCFrame"):Connect(function()posed+=1 end)
b.Transform=CFrame.new(5,0,0)''')
        self.execute(h,'assert(rest==0 and posed==1);vec(c.TransformedWorldCFrame.Position,Vector3.new(16,2,3))')

    def test_clone_rebinds_parent_and_constraint_references(self):
        self.run_script('''local m=Instance.new("Model");local p=Instance.new("Part",m);p.CFrame=CFrame.new(10,20,30)
local a=Instance.new("Attachment",p);a.CFrame=CFrame.new(1,2,3)
local c=Instance.new("HingeConstraint",m);c.Attachment0=a
local copy=m:Clone();local ca=copy.Part.Attachment
copy.Part.Position=Vector3.new(40,50,60)
vec(ca.WorldPosition,Vector3.new(41,52,63));vec(a.WorldPosition,Vector3.new(11,22,33))
assert(ca:GetConstraints()[1]==copy.HingeConstraint and #ca:GetConstraints()==1)''')


if __name__=='__main__':unittest.main()
