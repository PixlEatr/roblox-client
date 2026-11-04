"""Assembly motion queries execute as real Luau without network sessions."""
import copy
import unittest
import test_cframe_transforms as transforms
import test_joint_poses as poses
import test_joint_activation as activation


class PartMotionQueryTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script
    host = poses.JointPoseTests.host
    execute = poses.JointPoseTests.execute
    partial_snapshot = poses.JointPoseTests.partial_snapshot
    update = activation.JointActivationTests.update

    def test_closest_point_uses_current_pose_and_preserves_interior_points(self):
        self.run_script('''
local p=Instance.new("Part")
p.Size=Vector3.new(4,6,8)
p.CFrame=CFrame.new(20,-3,15)*CFrame.Angles(.3,.4,.5)
p.PivotOffset=CFrame.new(100,0,0)
local inside=p.CFrame*Vector3.new(1,2,3)
assert(p:GetClosestPointOnSurface(inside)==inside)
vec(p:GetClosestPointOnSurface(p.CFrame*Vector3.new(7,5,-2)),p.CFrame*Vector3.new(2,3,-2))
p.Size=Vector3.new(2,2,2)
vec(p:GetClosestPointOnSurface(p.CFrame*Vector3.new(7,5,-2)),p.CFrame*Vector3.new(1,1,-1))
local closest=p.GetClosestPointOnSurface
assert(not pcall(closest,workspace,Vector3.zero))
assert(not pcall(closest,p,"0,0,0"))
assert(not pcall(closest,p,Vector3.xAxis/0))
p:Destroy()
assert(not pcall(closest,p,Vector3.zero))
''')

    def test_closest_point_uses_round_and_sloped_shapes(self):
        self.run_script('''
local p=Instance.new("Part")
local sqrt3=math.sqrt(3)
p.Shape=Enum.PartType.Ball
p.Size=Vector3.new(8,4,2)
vec(p:GetClosestPointOnSurface(Vector3.new(2.5,2*sqrt3,0)),Vector3.new(2,sqrt3,0))
assert(p:GetClosestPointOnSurface(Vector3.new(1,1,0))==Vector3.new(1,1,0))
p.Shape=Enum.PartType.Cylinder
p.Size=Vector3.new(6,8,4)
vec(p:GetClosestPointOnSurface(Vector3.new(10,2.5,2*sqrt3)),Vector3.new(3,2,sqrt3))
vec(p:GetClosestPointOnSurface(Vector3.new(-10,1,1)),Vector3.new(-3,1,1))
local wedge=Instance.new("WedgePart")
wedge.Size=Vector3.new(4,4,4)
vec(wedge:GetClosestPointOnSurface(Vector3.new(1,2,0)),Vector3.new(1,1,1))
vec(wedge:GetClosestPointOnSurface(Vector3.new(4,2,0)),Vector3.new(2,1,1))
vec(wedge:GetClosestPointOnSurface(Vector3.new(4,5,5)),Vector3.new(2,2,2))
local corner=Instance.new("CornerWedgePart")
corner.Size=Vector3.new(4,4,4)
vec(corner:GetClosestPointOnSurface(Vector3.new(0,2,0)),Vector3.new(2/3,2/3,-2/3))
assert(corner:GetClosestPointOnSurface(Vector3.new(0,-1,0))==Vector3.new(0,-1,0))
''')

    def test_closest_point_uses_decoded_mesh_and_respects_box_fidelity(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        mesh={'id':'closest-mesh','class_name':'MeshPart','name':'ClosestMesh',
            'parent':'scope:workspace','properties':{
                'CFrame':{'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}},
                'Size':{'$vector3':[2,2,2]},
                'CollisionGeometry':{'kind':'triangle_mesh','vertices':[[0,0,0],[2,0,0],[0,2,0],[0,0,2]],
                                     'triangles':[[0,2,1],[0,1,3],[0,3,2],[1,2,3]]}}}
        box=copy.deepcopy(mesh)
        box.update(id='closest-box',name='ClosestBox')
        box['properties']['CollisionFidelity']={'$enum':{'type':'CollisionFidelity','value':2}}
        missing=copy.deepcopy(mesh)
        missing.update(id='closest-missing',name='ClosestMissing')
        del missing['properties']['CollisionGeometry']
        surface=copy.deepcopy(mesh)
        surface.update(id='closest-open',name='ClosestOpen')
        surface['properties']['CollisionGeometry']['triangles']=[[0,2,1]]
        data['nodes'].extend([mesh,box,missing,surface])
        self.run_script('''
local mesh=workspace.ClosestMesh
local inside=Vector3.new(.25,.25,.25)
assert(mesh:GetClosestPointOnSurface(inside)==inside)
vec(mesh:GetClosestPointOnSurface(Vector3.new(2,2,2)),Vector3.new(2/3,2/3,2/3))
vec(mesh:GetClosestPointOnSurface(Vector3.new(-2,-3,1)),Vector3.new(0,0,1))
assert(mesh:GetClosestPointOnSurface(Vector3.zero)==Vector3.zero)
vec(workspace.ClosestOpen:GetClosestPointOnSurface(inside),Vector3.new(.25,.25,0))
vec(workspace.ClosestBox:GetClosestPointOnSurface(Vector3.new(2,2,2)),Vector3.one)
assert(not pcall(function()workspace.ClosestMissing:GetClosestPointOnSurface(Vector3.zero)end))
mesh.Size=Vector3.new(4,2,2)
mesh.CFrame=CFrame.new(10,0,0)*CFrame.Angles(0,0,math.pi/2)
local projected=Vector3.new(10-2/3,4/3,2/3)
vec(mesh:GetClosestPointOnSurface(projected+Vector3.new(-2,1,2)),projected)
''',snapshot=data)

    def test_local_primitive_velocity_includes_rotation_about_moved_center(self):
        self.run_script('''
local p=Instance.new("Part",workspace)
p.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.2,.4,.6)
p.AssemblyLinearVelocity=Vector3.new(3,4,5)
p.AssemblyAngularVelocity=Vector3.new(0,2,0)
vec(p:GetVelocityAtPosition(Vector3.new(13,20,30)),Vector3.new(3,4,-1))
vec(p:GetVelocityAtPosition(p.Position),Vector3.new(3,4,5))
p.AssemblyAngularVelocity=Vector3.zero
vec(p:GetVelocityAtPosition(Vector3.new(-100,300,-900)),Vector3.new(3,4,5))
p.Anchored=true
vec(p:GetVelocityAtPosition(Vector3.zero),Vector3.new(3,4,5))
''')

    def test_rigid_members_share_root_linear_velocity(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.RootPriority=100
local b=Instance.new("Part",workspace);b.CFrame=CFrame.new(5,0,0)
local weld=Instance.new("WeldConstraint",a);weld.Part0=a;weld.Part1=b
a.AssemblyLinearVelocity=Vector3.new(2,3,4)
vec(b:GetVelocityAtPosition(Vector3.new(20,30,40)),Vector3.new(2,3,4))
weld.Enabled=false
vec(b:GetVelocityAtPosition(Vector3.new(20,30,40)),Vector3.zero)
''')

    def test_grounded_tracks_rigid_anchor_changes_not_floor_contact(self):
        self.run_script('''
local floor=Instance.new("Part",workspace);floor.Anchored=true
local a=Instance.new("Part",workspace);local b=Instance.new("Part",workspace)
assert(floor:IsGrounded() and not a:IsGrounded())
local weld=Instance.new("WeldConstraint",a);weld.Part0=a;weld.Part1=b
assert(not a:IsGrounded() and not b:IsGrounded())
b.Anchored=true;assert(a:IsGrounded() and b:IsGrounded())
weld.Enabled=false;assert(not a:IsGrounded() and b:IsGrounded())
weld.Enabled=true;assert(a:IsGrounded())
b.Anchored=false;assert(not a:IsGrounded())
weld:Destroy();b.Anchored=true;assert(not a:IsGrounded())
''')

    def test_flexible_connection_does_not_ground_free_part(self):
        self.run_script('''
local a=Instance.new("Part",workspace);local b=Instance.new("Part",workspace);b.Anchored=true
local spring=Instance.new("SpringConstraint",a)
spring.Attachment0=Instance.new("Attachment",a);spring.Attachment1=Instance.new("Attachment",b)
assert(not a:IsGrounded() and b:IsGrounded())
''')

    def test_received_center_of_mass_is_used_in_world_space(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'].append({'id':'motion-part','class_name':'MeshPart','name':'MotionPart',
            'parent':'scope:workspace','properties':{
                'AssemblyLinearVelocity':{'$vector3':[1,2,3]},
                'AssemblyAngularVelocity':{'$vector3':[0,0,2]},
                'AssemblyCenterOfMass':{'$vector3':[10,0,0]},
                'Anchored':False}})
        self.run_script('''
vec(workspace.MotionPart:GetVelocityAtPosition(Vector3.new(10,3,0)),Vector3.new(-5,2,3))
assert(not workspace.MotionPart:IsGrounded())
''',snapshot=data)

    def test_unknown_received_state_is_not_a_zero_or_a_guessed_center(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'].append({'id':'motion-part','class_name':'Part','name':'MotionPart',
            'parent':'scope:workspace','properties':{
                'AssemblyLinearVelocity':{'$vector3':[0,0,0]},
                'AssemblyAngularVelocity':{'$vector3':[0,1,0]},
                'CFrame':{'$cframe':{'position':[10,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}}}})
        self.run_script('''
local p=workspace.MotionPart
assert(type(p.GetVelocityAtPosition)=="function")
local ok,err=pcall(function()p:GetVelocityAtPosition(Vector3.zero)end)
assert(not ok and string.find(err,"center of mass"))
assert(not pcall(function()p:IsGrounded()end))
''',snapshot=data)

    def test_bad_receivers_inputs_and_destroyed_parts_fail(self):
        self.run_script('''
local p=Instance.new("Part",workspace)
local velocity,grounded=p.GetVelocityAtPosition,p.IsGrounded
assert(not pcall(velocity,Instance.new("Folder"),Vector3.zero))
assert(not pcall(grounded,Instance.new("Folder")))
assert(not pcall(velocity,p,{}))
assert(not pcall(function()return velocity(p,Vector3.new(math.huge,0,0))end))
p.AssemblyAngularVelocity=Vector3.new(0,3e38,0)
local ok,err=pcall(velocity,p,Vector3.new(3e38,0,0))
assert(not ok and string.find(err,"finite Vector3 range"))
p:Destroy();assert(not pcall(velocity,p,Vector3.zero));assert(not pcall(grounded,p))
''')

    def test_received_changes_refresh_queries_without_outbound_writes(self):
        data=self.partial_snapshot()
        props=data['nodes'][-2]['properties']
        props.update(AssemblyLinearVelocity={'$vector3':[1,0,0]},
                     AssemblyAngularVelocity={'$vector3':[0,0,2]},
                     AssemblyCenterOfMass={'$vector3':[4,0,0]})
        h=self.host(data)
        result=self.execute(h,'''assert(not workspace.PB:IsGrounded())
vec(workspace.PB:GetVelocityAtPosition(Vector3.new(4,1,0)),Vector3.new(-1,0,0))''')
        self.assertFalse(result['mutations'],result)
        self.update(h,[{'kind':'set','id':'pa','fields':{'Anchored':True}},
                       {'kind':'set','id':'pb','fields':{
                           'AssemblyLinearVelocity':{'$vector3':[3,0,0]},
                           'AssemblyCenterOfMass':{'$vector3':[4,1,0]}}}])
        result=self.execute(h,'''assert(workspace.PB:IsGrounded())
vec(workspace.PB:GetVelocityAtPosition(Vector3.new(4,1,0)),Vector3.new(3,0,0))''')
        self.assertFalse(result['mutations'],result)
        self.update(h,[{'kind':'set','id':'pm','fields':{'Enabled':False}}])
        self.execute(h,'assert(not workspace.PB:IsGrounded())')


if __name__ == '__main__':
    unittest.main()
