"""Native Luau coverage of current primitive and rigid assembly mass state."""
import copy
import unittest
import test_cframe_transforms as transforms
import test_joint_poses as poses


class PartMassTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script
    host = poses.JointPoseTests.host
    execute = poses.JointPoseTests.execute

    def test_size_material_and_custom_density_replace_constructor_mass(self):
        self.run_script('''
local p=Instance.new("Part")
assert(p.Shape==Enum.PartType.Block and p.Material==Enum.Material.Plastic)
p.Size=Vector3.new(2,3,4);near(p.Mass,16.8);near(p:GetMass(),16.8)
p.Material=Enum.Material.Wood;near(p.Mass,8.4)
p.Size=Vector3.new(4,3,4);near(p:getMass(),16.8)
p.CustomPhysicalProperties=PhysicalProperties.new(2,.1,.2);near(p.Mass,96)
p.Material=Enum.Material.Metal;near(p.Mass,96)
p.CustomPhysicalProperties=nil;near(p.Mass,376.8)
assert(client.propertyOrigin(p,"Mass")=="local")
''')

    def test_shape_volumes_and_local_centers(self):
        self.run_script('''
local p=Instance.new("Part",workspace);p.Size=Vector3.new(6,6,6)
p.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
p.Shape=Enum.PartType.Ball;near(p.Mass,36*math.pi);vec(p.CenterOfMass,Vector3.zero)
p.Shape=Enum.PartType.Cylinder;near(p.Mass,54*math.pi)
p.Size=Vector3.new(2,4,6)
p.Shape=Enum.PartType.Ball;near(p.Mass,4*math.pi/3)
p.Shape=Enum.PartType.Cylinder;near(p.Mass,8*math.pi)
p.Shape=Enum.PartType.Wedge;near(p.Mass,24);vec(p.CenterOfMass,Vector3.new(0,-4/6,1))
p.Shape=Enum.PartType.CornerWedge;near(p.Mass,16);vec(p.CenterOfMass,Vector3.new(.25,-1,-.75))
p.Shape=Enum.PartType.Block;near(p.Mass,48);vec(p.CenterOfMass,Vector3.zero)
for _,class in {"WedgePart","CornerWedgePart"} do
 local q=Instance.new(class,workspace);q.Size=p.Size
 q.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
 p.Shape=class=="WedgePart" and Enum.PartType.Wedge or Enum.PartType.CornerWedge
 near(q.Mass,p.Mass);vec(q.CenterOfMass,p.CenterOfMass)
end
''')

    def test_welded_center_uses_mass_and_refreshes_after_changes(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Size=Vector3.one;a.RootPriority=100
local b=Instance.new("Part",workspace);b.Size=Vector3.one;b.CFrame=CFrame.new(10,0,0)
a.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
b.CustomPhysicalProperties=PhysicalProperties.new(3,0,0)
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
near(a.AssemblyMass,4);near(b.AssemblyMass,4)
vec(a.AssemblyCenterOfMass,Vector3.new(7.5,0,0));vec(b.AssemblyCenterOfMass,a.AssemblyCenterOfMass)
a.AssemblyAngularVelocity=Vector3.new(0,2,0)
vec(b:GetVelocityAtPosition(Vector3.new(10,0,0)),Vector3.new(0,0,-5))
b.Size=Vector3.new(3,1,1);near(a.AssemblyMass,10);vec(a.AssemblyCenterOfMass,Vector3.new(9,0,0))
b.Massless=true;near(a.AssemblyMass,1);near(b.Mass,9);vec(b.AssemblyCenterOfMass,Vector3.zero)
b.Massless=false;w.Enabled=false;near(a.AssemblyMass,1);near(b.AssemblyMass,9)
vec(b.AssemblyCenterOfMass,Vector3.new(10,0,0))
''')

    def test_massless_root_contributes_and_anchored_root_controls_center(self):
        self.run_script('''
local a=Instance.new("WedgePart",workspace);a.Size=Vector3.new(2,6,6);a.RootPriority=100
a.CFrame=CFrame.new(10,20,30)*CFrame.Angles(0,0,math.pi/2)
a.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
local b=Instance.new("Part",workspace);b.Size=Vector3.one
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
a.Massless=true;b.Massless=true;near(b.AssemblyMass,36)
vec(a.AssemblyCenterOfMass,Vector3.new(11,20,31))
a.Anchored=true;assert(a.AssemblyMass==math.huge and b.AssemblyMass==math.huge)
near(a.Mass,36);vec(b.AssemblyCenterOfMass,Vector3.new(11,20,31))
a.Anchored=false;near(a.AssemblyMass,36)
''')

    def test_disconnected_clones_and_reparenting_use_current_state(self):
        self.run_script('''
local m=Instance.new("Model",workspace)
local a=Instance.new("Part",m);a.Size=Vector3.one;a.CFrame=CFrame.new(4,5,6)
local b=Instance.new("Part",m);b.Size=Vector3.one;b.CFrame=CFrame.new(8,5,6)
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
near(a.AssemblyMass,1.4);vec(a.AssemblyCenterOfMass,Vector3.new(6,5,6))
local clone=m:Clone();clone.Parent=workspace
near(clone:FindFirstChildWhichIsA("Part").AssemblyMass,1.4)
m.Parent=nil;near(a.AssemblyMass,.7);vec(a.AssemblyCenterOfMass,Vector3.new(4,5,6))
m.Parent=workspace;near(a.AssemblyMass,1.4)
b:Destroy();near(a.AssemblyMass,.7)
''')

    def test_computed_property_signals_follow_member_and_joint_changes(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Size=Vector3.one
local b=Instance.new("Part",workspace);b.Size=Vector3.one;b.CFrame=CFrame.new(4,0,0)
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
local mass,center=0,0
a:GetPropertyChangedSignal("AssemblyMass"):Connect(function()mass+=1 end)
a.Changed:Connect(function(k)if k=="AssemblyCenterOfMass" then center+=1 end end)
b.Size=Vector3.new(3,1,1);task.wait(.001)
assert(mass==1 and center==1);near(a.AssemblyMass,2.8);vec(a.AssemblyCenterOfMass,Vector3.new(3,0,0))
task.wait(.001);assert(mass==1 and center==1)
w.Enabled=false;task.wait(.001);assert(mass==2 and center==2)
vec(a.AssemblyCenterOfMass,Vector3.zero)
''')

    def test_material_variants_unknown_geometry_and_bad_assignments(self):
        self.run_script('''
local p=Instance.new("Part",workspace)
assert(not pcall(function()p.Shape=Enum.Material.Plastic end))
assert(not pcall(function()p.Material=Enum.PartType.Block end))
assert(not pcall(function()p.Mass=20 end))
assert(not pcall(function()p.AssemblyCenterOfMass=Vector3.zero end))
p.MaterialVariant="UnknownDensity";assert(not pcall(function()return p.Mass end))
p.CustomPhysicalProperties=PhysicalProperties.new(1,0,0);near(p.Mass,9.6)
local mesh=Instance.new("MeshPart",workspace)
assert(not pcall(function()return mesh.Mass end))
assert(not pcall(function()return mesh.AssemblyCenterOfMass end))
local method=p.GetMass;p:Destroy();assert(not pcall(method,p))
assert(not pcall(method,Instance.new("Folder")))
''')

    def test_received_mass_and_center_are_preserved_without_fabricating_missing_values(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'].append({'id':'mass-part','class_name':'MeshPart','name':'MassPart',
            'parent':'scope:workspace','properties':{
                'Mass':12,'AssemblyMass':30,
                'CenterOfMass':{'$vector3':[1,2,3]},
                'AssemblyCenterOfMass':{'$vector3':[10,20,30]}}})
        data['nodes'].append({'id':'unknown-mass','class_name':'Part','name':'UnknownMass',
            'parent':'scope:workspace','properties':{}})
        self.run_script('''
local p=workspace.MassPart
near(p.Mass,12);near(p:GetMass(),12);near(p.AssemblyMass,30)
vec(p.CenterOfMass,Vector3.new(1,2,3));vec(p.AssemblyCenterOfMass,Vector3.new(10,20,30))
assert(client.propertyOrigin(p,"Mass")=="received")
local q=workspace.UnknownMass
assert(not pcall(function()return q:GetMass()end))
assert(not pcall(function()return q.AssemblyCenterOfMass end))
''',snapshot=data)

    def test_persistent_scripts_share_mass_updates_and_notifications(self):
        h=self.host()
        self.execute(h,'''getgenv().p=Instance.new("Part",workspace);p.Size=Vector3.one
getgenv().massChanges=0
p:GetPropertyChangedSignal("Mass"):Connect(function()massChanges+=1 end)''')
        self.execute(h,'p.Size=Vector3.new(2,2,2);task.wait(.001)')
        result=self.execute(h,'''near(p:GetMass(),5.6);near(p.AssemblyMass,5.6)
assert(massChanges==1);vec(p.AssemblyCenterOfMass,p.Position)''')
        self.assertFalse(result['mutations'],result)


if __name__ == '__main__':
    unittest.main()
