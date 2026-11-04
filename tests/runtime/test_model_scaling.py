"""Canonical geometry scaling and transactional native model updates."""
import copy
import unittest

import test_cframe_transforms as transforms
import test_model_pivots as pivots
from test_part_transforms import pose


SETUP='''
local m=Instance.new("Model",workspace);m.WorldPivot=CFrame.new(10,20,30)*CFrame.Angles(.2,.3,.4)
local a=Instance.new("Part",m);a.Size=Vector3.new(2,4,6);a.CFrame=CFrame.new(12,24,36)*CFrame.Angles(.3,.5,.7)
local b=Instance.new("Part",m);b.Size=Vector3.new(3,5,7);b.CFrame=CFrame.new(5,10,15)*CFrame.Angles(-.1,.2,-.3)
local originalA,originalB=a.CFrame,b.CFrame
local sizeA,sizeB=a.Size,b.Size
'''


class ModelScalingTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source):return self.run_script(SETUP+source)

    def test_scale_methods_validate_arguments_and_keep_scale_property_nonscriptable(self):
        self.check('''
assert(m:GetScale()==1)
assert(not pcall(function()return m.Scale end))
assert(not pcall(function()m.Scale=3 end))
for _,bad in {
 function()m:ScaleTo(0)end,function()m:ScaleTo(-1)end,
 function()m:ScaleTo(math.huge)end,function()m:ScaleTo(0/0)end,
 function()m:ScaleTo("2")end,function()m:ScaleTo(Vector3.one)end,
 function()m:ScaleTo()end,function()m:ScaleTo(2,3)end,
 function()m.ScaleTo(a,2)end,function()m.GetScale(a)end,
 function()m:GetScale(1)end,
} do assert(not pcall(bad));assert(m:GetScale()==1);frame(a.CFrame,originalA);assert(a.Size==sizeA)end
assert(select('#',m:ScaleTo(2))==0 and m:GetScale()==2)
local dead=Instance.new("Model");local fn=dead.GetScale;dead:Destroy();assert(not pcall(fn,dead))
''')

    def test_geometry_uses_absolute_scale_and_fixed_world_pivot(self):
        self.check('''
local pivot=m:GetPivot();m:ScaleTo(2)
vec(a.Size,Vector3.new(4,8,12));vec(b.Size,Vector3.new(6,10,14))
vec(a.Position,Vector3.new(14,28,42));vec(b.Position,Vector3.new(0,0,0))
assert(a.CFrame.Rotation==originalA.Rotation);frame(m:GetPivot(),pivot)
m:ScaleTo(3);vec(a.Size,Vector3.new(6,12,18));vec(a.Position,Vector3.new(16,32,48))
m:ScaleTo(1);assert(a.CFrame==originalA and b.CFrame==originalB and a.Size==sizeA and b.Size==sizeB)
''')

    def test_primary_pivot_offsets_scale_without_moving_the_pivot(self):
        self.check('''
a.PivotOffset=CFrame.new(1,2,3)*CFrame.Angles(.6,.4,.2);m.PrimaryPart=a
local pivot=m:GetPivot();local offset=a.PivotOffset
m:ScaleTo(2);frame(m:GetPivot(),pivot)
vec(a.PivotOffset.Position,offset.Position*2);assert(a.PivotOffset.Rotation==offset.Rotation)
vec(a.Position,pivot.Position+(originalA.Position-pivot.Position)*2)
m:ScaleTo(1);assert(a.PivotOffset==offset and a.CFrame==originalA and b.CFrame==originalB)
''')

    def test_attachment_bone_and_joint_translations_scale_once(self):
        self.check('''
local at=Instance.new("Attachment",a);at.CFrame=CFrame.new(1,2,3)*CFrame.Angles(.2,.3,.4)
local bone=Instance.new("Bone",at);bone.CFrame=CFrame.new(2,3,4);bone.Transform=CFrame.new(1,0,0)
local weld=Instance.new("Weld",a);weld.Part0=a;weld.Part1=b
weld.C0=a.CFrame:ToObjectSpace(b.CFrame)
local c0,atCF,boneCF=weld.C0,at.CFrame,bone.CFrame
m:ScaleTo(2)
vec(at.Position,Vector3.new(2,4,6));vec(bone.Position,Vector3.new(4,6,8))
vec(bone.Transform.Position,Vector3.new(2,0,0));vec(weld.C0.Position,c0.Position*2)
assert(weld.C0.Rotation==c0.Rotation);frame(a.CFrame*weld.C0,b.CFrame*weld.C1)
frame(at.WorldCFrame,a.CFrame*at.CFrame)
m:ScaleTo(1);assert(at.CFrame==atCF and bone.CFrame==boneCF and weld.C0==c0)
''')

    def test_nested_models_keep_canonical_scales_and_scale_pivots(self):
        self.check('''
local child=Instance.new("Model",m);b.Parent=child;child.WorldPivot=CFrame.new(2,4,6)
local pivot=child.WorldPivot
child:ScaleTo(2);local before=b.Size
m:ScaleTo(3);assert(m:GetScale()==3 and child:GetScale()==6)
vec(b.Size,before*3);vec(child.WorldPivot.Position,Vector3.new(-14,-28,-42))
m:ScaleTo(1);assert(child:GetScale()==2 and child.WorldPivot==pivot and b.Size==before)
child:ScaleTo(1);assert(b.Size==sizeB)
''')

    def test_new_descendants_are_not_automatically_scaled_and_edits_rebase(self):
        self.check('''
m:ScaleTo(2)
local added=Instance.new("Part",m);added.Size=Vector3.new(4,6,8);added.CFrame=CFrame.new(20,30,40)
a.Size=Vector3.new(10,20,30)
m:ScaleTo(4);vec(added.Size,Vector3.new(8,12,16));vec(a.Size,Vector3.new(20,40,60))
vec(added.Position,Vector3.new(30,40,50))
m:ScaleTo(2);assert(a.Size==Vector3.new(10,20,30) and added.Size==Vector3.new(4,6,8))
b.Parent=workspace;local old=b.CFrame;m:ScaleTo(3);assert(b.CFrame==old)
''')

    def test_scaling_after_model_movement_uses_current_pivot(self):
        self.check('''
m:ScaleTo(2);m:TranslateBy(Vector3.new(100,200,300))
local pivot=m:GetPivot();local before=a.Position
m:ScaleTo(3);vec(a.Position,pivot.Position+(before-pivot.Position)*1.5)
frame(m:GetPivot(),pivot);m:ScaleTo(1);vec(a.Size,sizeA)
m:PivotTo(CFrame.new(5,6,7)*CFrame.Angles(.7,.4,.2))
local now=m:GetPivot();local position=a.Position;m:ScaleTo(2)
vec(a.Position,now.Position+(position-now.Position)*2)
''')

    def test_clone_retains_scale_and_original_geometry_cache(self):
        self.check('''
a.Name="A";b.Name="B";m:ScaleTo(2.75)
local clone=m:Clone();assert(clone:GetScale()==2.75)
clone:ScaleTo(1);assert(clone.A.CFrame==originalA and clone.B.CFrame==originalB)
assert(clone.A.Size==sizeA and clone.B.Size==sizeB)
assert(m:GetScale()==2.75)
m:ScaleTo(1);assert(a.CFrame==originalA and b.CFrame==originalB)
''')

    def test_visual_sound_and_humanoid_distance_properties(self):
        self.check('''
local mesh=Instance.new("SpecialMesh",a);mesh.MeshType=Enum.MeshType.FileMesh
mesh.Scale=Vector3.new(2,3,4);mesh.Offset=Vector3.new(1,2,3)
local sound=Instance.new("Sound",a);sound.RollOffMinDistance=10;sound.RollOffMaxDistance=100
local beam=Instance.new("Beam",a);beam.Width0=2;beam.Width1=3;beam.CurveSize0=4
local emitter=Instance.new("ParticleEmitter",a)
emitter.Size=NumberSequence.new({NumberSequenceKeypoint.new(0,2,.5),NumberSequenceKeypoint.new(1,4,1)})
emitter.Speed=NumberRange.new(5,10);emitter.Acceleration=Vector3.new(0,-4,0)
local hum=Instance.new("Humanoid",m);hum.HipHeight=3;hum.CameraOffset=Vector3.new(1,2,3)
m:ScaleTo(2)
vec(mesh.Scale,Vector3.new(4,6,8));vec(mesh.Offset,Vector3.new(2,4,6))
assert(sound.RollOffMinDistance==20 and sound.RollOffMaxDistance==200)
assert(beam.Width0==4 and beam.Width1==6 and beam.CurveSize0==8)
assert(emitter.Size.Keypoints[1].Value==4 and emitter.Size.Keypoints[1].Envelope==1)
assert(emitter.Size.Keypoints[2].Time==1 and emitter.Size.Keypoints[2].Value==8)
assert(emitter.Speed.Min==10 and emitter.Speed.Max==20);vec(emitter.Acceleration,Vector3.new(0,-8,0))
assert(hum.HipHeight==6);vec(hum.CameraOffset,Vector3.new(2,4,6))
m:ScaleTo(1);assert(emitter.Size.Keypoints[1].Value==2 and emitter.Size.Keypoints[1].Envelope==.5)
''')

    def test_unsupported_animation_families_fail_atomically(self):
        for name in ['Animator']:
            self.check('''
local unsupported=Instance.new("'''+name+'''",b)
local ok,err=pcall(function()m:ScaleTo(2)end)
assert(not ok and string.find(err,"not implemented"))
assert(m:GetScale()==1 and a.CFrame==originalA and b.CFrame==originalB and a.Size==sizeA)
unsupported:Destroy();m:ScaleTo(2);assert(m:GetScale()==2)
''')

    def test_own_character_animator_scales_motor_geometry_in_both_hosts(self):
        self.run_script('''
local char=game:GetService("Players").LocalPlayer.Character
local root=char.HumanoidRootPart
root.Anchored=true;root.Size=Vector3.new(2,2,1)
root.CFrame=CFrame.identity;root.PivotOffset=CFrame.identity;char.PrimaryPart=root
local arm=Instance.new("Part",char);arm.Size=Vector3.new(1,2,1);arm.CFrame=CFrame.new(0,3,0)
local motor=Instance.new("Motor6D",root);motor.Part0=root;motor.Part1=arm
motor.C0=CFrame.new(0,2,0);motor.C1=CFrame.new(0,-1,0)
Instance.new("Animator",char.Humanoid)
char:ScaleTo(2)
assert(char:GetScale()==2);vec(root.Size,Vector3.new(4,4,2))
vec(motor.C0.Position,Vector3.new(0,4,0));vec(motor.C1.Position,Vector3.new(0,-2,0))
vec(arm.Position,Vector3.new(0,6,0))
char:ScaleTo(1);vec(arm.Position,Vector3.new(0,3,0));vec(arm.Size,Vector3.new(1,2,1))
''')

    def test_mesh_types_are_typed_and_primitive_meshes_do_not_scale_twice(self):
        self.check('''
assert(#Enum.MeshType:GetEnumItems()==12 and Enum.MeshType.FileMesh.Value==5)
assert(Enum.MeshType:FromValue(6)==Enum.MeshType.Brick)
local meshes={}
for _,kind in {Enum.MeshType.Brick,Enum.MeshType.Wedge,Enum.MeshType.Sphere,Enum.MeshType.Cylinder} do
 local mesh=Instance.new("SpecialMesh",a);mesh.MeshType=kind
 assert(mesh.MeshType==kind);mesh.Scale=Vector3.new(2,3,4);table.insert(meshes,mesh)
end
for _,name in {"BlockMesh","CylinderMesh"} do
 local mesh=Instance.new(name,a);mesh.Scale=Vector3.new(2,3,4);table.insert(meshes,mesh)
end
assert(not pcall(function()meshes[1].MeshType=5 end))
assert(not pcall(function()meshes[1].MeshType=Enum.Material.Plastic end))
m:ScaleTo(2);vec(a.Size,sizeA*2)
for _,mesh in meshes do assert(mesh.Scale==Vector3.new(2,3,4))end
meshes[1].MeshType=Enum.MeshType.Head
assert(not pcall(function()m:ScaleTo(3)end));assert(m:GetScale()==2)
''')

    def test_missing_part_metadata_does_not_partially_update_model(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'] += [
            {'id':'scale:m','class_name':'Model','name':'ScaleModel','parent':'scope:workspace',
             'properties':{'WorldPivot':pose(),'PrimaryPart':None}},
            {'id':'scale:a','class_name':'Part','name':'A','parent':'scale:m','properties':{'CFrame':pose(),'Size':{'$vector3':[2,4,6]}}},
            {'id':'scale:b','class_name':'Part','name':'B','parent':'scale:m','properties':{'CFrame':pose((1,2,3))}},
        ]
        results=self.run_script('''
local m=workspace.ScaleModel
assert(m:GetScale()==1);assert(not pcall(function()m:ScaleTo(2)end))
assert(m:GetScale()==1);vec(m.A.Size,Vector3.new(2,4,6));frame(m.A.CFrame,CFrame.identity)
''',data)
        for result in results:self.assertFalse(any(m.get('id','').startswith('scale:') for m in result['mutations']))

    def test_weldconstraint_mass_and_spatial_queries_use_scaled_geometry(self):
        self.run_script('''
local m=Instance.new("Model",workspace);m.WorldPivot=CFrame.identity
local a=Instance.new("Part",m);a.Size=Vector3.new(2,2,2);a.CFrame=CFrame.new(10,0,0)
local b=Instance.new("Part",m);b.Size=Vector3.new(2,2,2);b.CFrame=CFrame.new(14,0,0)
a.CustomPhysicalProperties=PhysicalProperties.new(1,0,0);b.CustomPhysicalProperties=a.CustomPhysicalProperties
local weld=Instance.new("WeldConstraint",a);weld.Part0=a;weld.Part1=b
near(a.AssemblyMass,16);m:ScaleTo(2);near(a.AssemblyMass,128)
vec(a.AssemblyCenterOfMass,Vector3.new(24,0,0))
local params=RaycastParams.new();params.IncludeInstances={m}
local hit=workspace:Raycast(Vector3.new(0,0,0),Vector3.new(100,0,0),params)
assert(hit.Instance==a);near(hit.Distance,18)
local overlap=OverlapParams.new();overlap.IncludeInstances={m}
local found=workspace:GetPartBoundsInBox(CFrame.new(20,0,0),Vector3.one,overlap)
assert(#found==1 and found[1]==a)
m:ScaleTo(1);near(a.AssemblyMass,16)
''')


class PersistentModelScalingTests(unittest.TestCase):
    host=pivots.ModelPivotTests.host
    execute=pivots.ModelPivotTests.execute

    def test_round_trips_restore_original_values_exactly_across_jobs(self):
        h=self.host()
        self.execute(h,SETUP+'''getgenv().m=m;getgenv().a=a;getgenv().b=b
getgenv().originalA=originalA;getgenv().originalB=originalB;getgenv().sizeA=sizeA;getgenv().sizeB=sizeB''')
        for target in [1.31,2.79,.117,3.14159,.23]*3:
            self.execute(h,'m:ScaleTo('+str(target)+');m:ScaleTo(1)\nassert(a.CFrame==originalA and b.CFrame==originalB and a.Size==sizeA and b.Size==sizeB)')

    def test_callbacks_observe_all_geometry_and_scale_committed(self):
        h=self.host()
        self.execute(h,SETUP+'''getgenv().m=m;getgenv().a=a;getgenv().b=b;getgenv().calls=0
a:GetPropertyChangedSignal("Size"):Connect(function()
 assert(m:GetScale()==2 and b.Size==Vector3.new(6,10,14));vec(b.Position,Vector3.zero);calls+=1
end)
m:ScaleTo(2)''')
        self.assertEqual(self.execute(h,'return calls')['values'],[1])

    def test_budget_failure_preserves_geometry_scale_and_cache(self):
        h=self.host()
        self.execute(h,SETUP+'''getgenv().m=m;getgenv().a=a;getgenv().originalA=originalA
m:ScaleTo(2);getgenv().before=a.CFrame;getgenv().filler=Instance.new("Folder")''')
        self.execute(h,'''local i=0;while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()m:ScaleTo(3)end));assert(m:GetScale()==2 and a.CFrame==before)''')
        self.execute(h,'m:ScaleTo(1);assert(a.CFrame==originalA)')


if __name__=='__main__':unittest.main()
