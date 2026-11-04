"""Real native mouse picking: geometry, mutable world state, and exclusions."""
import unittest

from persistent_runtime import PersistentLuauHost
import test_camera_projection as camera
import test_cframe_transforms as transforms
import test_mouse_projection as projection


def node(kind, geometry=None):
    properties = {
        'CFrame': {'$cframe': {'position': [0, 0, -10], 'rotation': [1,0,0,0,1,0,0,0,1]}},
        'Size': {'$vector3': [4,4,4]}, 'CanQuery': True, 'CanCollide': True,
    }
    if geometry is not None:
        properties['CollisionGeometry'] = geometry
    return {'id': 'pick:test', 'name': 'PickTest', 'class_name': kind,
            'parent': 'scope:workspace', 'properties': properties}


class MousePickingTests(unittest.TestCase):
    execute = projection.MouseProjectionTests.execute
    update = projection.MouseProjectionTests.update
    move = projection.MouseProjectionTests.move
    check = projection.MouseProjectionTests.check

    def start(self, extra=None):
        snapshot = camera.snapshot(projection.PROFILE)
        snapshot['input_capabilities'] = {'MouseEnabled': True}
        snapshot['nodes'].extend(extra or [])
        self.host = PersistentLuauHost(snapshot)
        self.addCleanup(self.host.close)
        self.execute('''
mouse=game:GetService("Players").LocalPlayer:GetMouse()
camera=workspace.CurrentCamera;camera.CFrame=CFrame.identity;camera.FieldOfView=90
function part(z,kind)
 local p=Instance.new(kind or "Part",workspace)
 p.Size=Vector3.new(4,4,4);p.CFrame=CFrame.new(0,0,z);p.Anchored=true
 return p
end
''')
        self.move(400,300)

    def test_nearest_hit_aliases_readonly_and_miss_limit(self):
        self.start()
        self.check('''
local far=part(-20);local close=part(-10)
assert(mouse.Target==close and mouse.target==close)
vec(mouse.Hit.Position,Vector3.new(0,0,-8));frame(mouse.hit,mouse.Hit)
vec(mouse.Hit.LookVector,mouse.UnitRay.Direction)
assert(mouse.TargetSurface==Enum.NormalId.Back)
for _,k in ipairs({"Hit","hit","Target","target","TargetSurface"})do
 assert(not pcall(function()mouse[k]=mouse[k]end))
end
close:Destroy();assert(mouse.Target==far);far:Destroy()
assert(mouse.Target==nil and mouse.TargetSurface==Enum.NormalId.Right)
vec(mouse.Hit.Position,Vector3.new(0,0,-1000))
far=part(-1003);assert(mouse.Target==nil)
far.Position=Vector3.new(0,0,-1002);assert(mouse.Target==far)
vec(mouse.Hit.Position,Vector3.new(0,0,-1000))
''')

    def test_target_filter_excludes_container_and_descendants(self):
        self.start()
        self.check('''
assert(mouse.TargetFilter==nil)
local folder=Instance.new("Folder",workspace)
local nearPart=part(-10);nearPart.Parent=folder;local far=part(-20)
mouse.TargetFilter=folder;assert(mouse.Target==far)
mouse.TargetFilter=nearPart;assert(mouse.Target==far)
mouse.TargetFilter=nil;assert(mouse.Target==nearPart)
for _,bad in ipairs({1,"Part",{},Vector3.zero})do
 assert(not pcall(function()mouse.TargetFilter=bad end));assert(mouse.TargetFilter==nil)
end
mouse.TargetFilter=workspace;assert(mouse.Target==nil)
mouse.TargetFilter=folder;folder:Destroy();assert(mouse.Target==far)
''')

    def test_local_character_is_ignored_and_replacement_updates_exclusion(self):
        self.start()
        self.check('''
local player=game:GetService("Players").LocalPlayer
local root=player.Character.HumanoidRootPart
root.Size=Vector3.new(4,4,4);root.Shape=Enum.PartType.Block;root.CFrame=CFrame.new(0,0,-5)
local p=part(-15);assert(mouse.Target==p)
local new=Instance.new("Model",workspace);p.Parent=new
player.Character=new;assert(mouse.Target==root)
''')

    def test_local_mutations_and_reparent_are_visible_without_input(self):
        self.start()
        self.check('''
local p=part(-10);local far=part(-20)
p.CFrame=CFrame.new(10,0,-10);assert(mouse.Target==far)
p.CFrame=CFrame.new(0,0,-10);p.Size=Vector3.new(4,4,8)
vec(mouse.Hit.Position,Vector3.new(0,0,-6))
p.Parent=nil;assert(mouse.Target==far)
p.Parent=workspace;assert(mouse.Target==p)
p:Destroy();assert(mouse.Target==far)
''')

    def test_six_faces_use_part_local_surface_even_when_rotated(self):
        self.start()
        self.check('''
local p=part(-10);p.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.3,.7,-.2)
local cases={{Vector3.xAxis,Enum.NormalId.Right},{-Vector3.xAxis,Enum.NormalId.Left},
 {Vector3.yAxis,Enum.NormalId.Top},{-Vector3.yAxis,Enum.NormalId.Bottom},
 {Vector3.zAxis,Enum.NormalId.Back},{-Vector3.zAxis,Enum.NormalId.Front}}
for _,c in ipairs(cases)do
 local origin=p.CFrame:PointToWorldSpace(c[1]*10)
 camera.CFrame=CFrame.lookAt(origin,p.Position)
 assert(mouse.Target==p);assert(mouse.TargetSurface==c[2],tostring(mouse.TargetSurface))
 vec(mouse.Hit.Position,p.CFrame:PointToWorldSpace(c[1]*2))
end
''')

    def test_transparency_and_query_flags(self):
        self.start()
        self.check('''
local p=part(-10);local far=part(-20)
p.Transparency=1;p.CanCollide=false;assert(mouse.Target==p)
p.CanQuery=false;assert(mouse.Target==far)
p.CanCollide=true;assert(mouse.Target==p)
p.CanCollide=false;p.CanQuery=true;assert(mouse.Target==p)
''')

    def test_mouse_does_not_pick_geometry_from_a_nested_worldmodel(self):
        self.start()
        self.check('''
local isolated=Instance.new("WorldModel",workspace)
local inside=part(-5);inside.Parent=isolated
local outside=part(-10)
assert(mouse.Target==outside)
''')

    def test_round_shapes_do_not_use_bounding_box_surface(self):
        self.start()
        self.check('''
local p=part(-10);p.Shape=Enum.PartType.Ball
camera.CFrame=CFrame.new(1,0,0)
vec(mouse.Hit.Position,Vector3.new(1,0,-10+math.sqrt(3)))
camera.CFrame=CFrame.new(1.9,1.9,0);assert(mouse.Target==nil)
p.Shape=Enum.PartType.Cylinder;camera.CFrame=CFrame.new(0,1,0)
vec(mouse.Hit.Position,Vector3.new(0,1,-10+math.sqrt(3)))
camera.CFrame=CFrame.lookAt(Vector3.new(10,0,-10),p.Position)
vec(mouse.Hit.Position,Vector3.new(2,0,-10));assert(mouse.TargetSurface==Enum.NormalId.Right)
''')

    def test_wedge_and_corner_wedge_have_sloped_surfaces(self):
        self.start()
        self.check('''
local p=part(-10,"WedgePart")
camera.CFrame=CFrame.lookAt(Vector3.new(0,10,-10),Vector3.new(0,0,-10))
vec(mouse.Hit.Position,Vector3.new(0,0,-10))
p:Destroy();p=part(-10,"CornerWedgePart")
camera.CFrame=CFrame.lookAt(Vector3.new(1,10,-9),Vector3.new(1,0,-9))
vec(mouse.Hit.Position,Vector3.new(1,-1,-9))
''')

    def test_inside_solid_ray_exits_at_outer_surface(self):
        self.start()
        self.check('''
local p=part(0)
vec(mouse.Hit.Position,Vector3.new(0,0,-2));assert(mouse.TargetSurface==Enum.NormalId.Front)
for _,shape in ipairs({Enum.PartType.Ball,Enum.PartType.Cylinder})do
 p.Shape=shape;vec(mouse.Hit.Position,Vector3.new(0,0,-2))
end
''')

    def test_unknown_mesh_only_blocks_when_it_could_obscure_target(self):
        self.start([node('MeshPart')])
        self.check('''
local mesh=workspace.PickTest
local ok,err=pcall(function()return mouse.Target end)
assert(not ok and string.find(err,"collision geometry is unavailable"))
local p=part(-5);assert(mouse.Target==p)
p:Destroy();mesh.CFrame=CFrame.new(10,0,-10);assert(mouse.Target==nil)
mesh.CFrame=CFrame.new(0,0,-10);mouse.TargetFilter=mesh;assert(mouse.Target==nil)
''')

    def test_decoded_mesh_triangles_holes_and_local_transform(self):
        geometry={'kind':'triangle_mesh','vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],'triangles':[[0,1,2]]}
        self.start([node('MeshPart',geometry)])
        self.check('''
local mesh=workspace.PickTest
assert(mouse.Target==mesh);vec(mouse.Hit.Position,Vector3.new(0,0,-10))
camera.CFrame=CFrame.new(1.9,1.9,0);assert(mouse.Target==nil)
mesh.Size=Vector3.new(8,8,4);camera.CFrame=CFrame.new(1.5,1,0);assert(mouse.Target==mesh)
mesh.CFrame=CFrame.new(0,0,-20);vec(mouse.Hit.Position,Vector3.new(1.5,1,-20))
mesh.CFrame=CFrame.new(0,0,-20)*CFrame.Angles(0,math.pi/2,0)
camera.CFrame=CFrame.lookAt(Vector3.new(10,0,-20),Vector3.new(0,0,-20))
vec(mouse.Hit.Position,Vector3.new(0,0,-20))
''')

    def test_voxels_ignore_internal_faces_and_respect_empty_cells(self):
        self.start([node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-6],
            'cell_size':[2,2,2],'dimensions':[1,1,3],'occupancy':[True,True,False]})])
        self.check('''
assert(mouse.Target==workspace.PickTest);vec(mouse.Hit.Position,Vector3.new(0,0,-2))
camera.CFrame=CFrame.new(0,0,-3)
vec(mouse.Hit.Position,Vector3.new(0,0,-6))
camera.CFrame=CFrame.new(2,0,0);assert(mouse.Target==nil)
''')

    def test_missing_terrain_geometry_fails_explicitly(self):
        self.start([node('Terrain')])
        self.check('''
local ok,err=pcall(function()return mouse.Hit end)
assert(not ok and string.find(err,"terrain collision voxels are unavailable"))
mouse.TargetFilter=workspace.PickTest;assert(mouse.Target==nil)
''')

    def test_voxel_geometry_follows_local_pose_rotation_and_nonuniform_scale(self):
        self.start([node('MeshPart',{'kind':'voxel_grid','origin':[-1,-1,-11],
            'cell_size':[2,2,2],'dimensions':[1,1,1],'occupancy':[True]})])
        self.check('''
local mesh=workspace.PickTest
vec(mouse.Hit.Position,Vector3.new(0,0,-9))
mesh.Size=Vector3.new(8,4,12);mesh.CFrame=CFrame.new(0,0,-20)
vec(mouse.Hit.Position,Vector3.new(0,0,-17))
mesh.CFrame=CFrame.new(0,0,-20)*CFrame.Angles(0,math.pi/2,0)
camera.CFrame=CFrame.lookAt(Vector3.new(10,0,-20),Vector3.new(0,0,-20))
vec(mouse.Hit.Position,Vector3.new(3,0,-20));assert(mouse.TargetSurface==Enum.NormalId.Back)
camera.CFrame=CFrame.new(0,0,-20)
vec(mouse.Hit.Position,Vector3.new(0,0,-22))
''')

    def test_filter_changes_emit_property_signals_once(self):
        self.start()
        self.execute('''
filterChanges=0;changedNames={}
mouse:GetPropertyChangedSignal("TargetFilter"):Connect(function()filterChanges+=1 end)
mouse.Changed:Connect(function(k)table.insert(changedNames,k)end)
p=part(-10);mouse.TargetFilter=p;mouse.TargetFilter=p;mouse.TargetFilter=nil
''')
        self.assertEqual(self.check('return filterChanges,table.concat(changedNames,",")'),
                         [2,'TargetFilter,TargetFilter'])

    def test_move_callback_picks_with_the_new_cursor(self):
        self.start()
        self.execute('''
p=part(-10);observations={}
mouse.Move:Connect(function()
 table.insert(observations,mouse.Target==p)
end)
''')
        self.move(400,300);self.move(790,10)
        self.assertEqual(self.check('return observations[1],observations[2]'),[True,False])

    def test_malformed_mesh_rejects_bad_indices(self):
        self.start([node('MeshPart',{'kind':'triangle_mesh','vertices':[[0,0,-10],[1,0,-10],[0,1,-10]],'triangles':[[0,1,3]]})])
        self.check('''local ok,err=pcall(function()return mouse.Target end)
assert(not ok and string.find(err,"triangle index out of range"))''')

    def test_degenerate_mesh_fails_instead_of_producing_nan_hit(self):
        self.start([node('MeshPart',{'kind':'triangle_mesh','vertices':[[0,0,-10],[1,0,-10],[2,0,-10]],'triangles':[[0,1,2]]})])
        self.check('''local ok,err=pcall(function()return mouse.Hit end)
assert(not ok and string.find(err,"degenerate triangle"))''')

    def test_invalid_voxel_count_is_rejected_before_geometry_iteration(self):
        self.start([node('Terrain',{'kind':'voxel_grid','origin':[0,0,0],
            'cell_size':[1,1,1],'dimensions':[1000,1000,2],'occupancy':[]})])
        self.check('''local ok,err=pcall(function()return mouse.Target end)
assert(not ok and string.find(err,"voxel count limit exceeded"))''')

    def test_voxel_occupancy_requires_booleans(self):
        self.start([node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-10],
            'cell_size':[2,2,2],'dimensions':[1,1,1],'occupancy':[1]})])
        self.check('''local ok,err=pcall(function()return mouse.Target end)
assert(not ok and string.find(err,"occupancy must be boolean"))''')


class MousePickingBothHostsTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_mouse_picking_executes_in_standalone_and_persistent_hosts(self):
        self.run_script('''
local m=game:GetService("Players").LocalPlayer:GetMouse()
workspace.CurrentCamera.CFrame=CFrame.identity
local direction=m.UnitRay.Direction
local p=Instance.new("Part",workspace);p.Size=Vector3.one*4
p.CFrame=CFrame.lookAt(direction*10,Vector3.zero)
assert(m.Target==p);vec(m.Hit.Position,direction*8)
m.TargetFilter=p;assert(m.Target==nil)
-- Luau vectors round each multiplication to float32; the native intersection
-- keeps double precision until the public Vector3 result (one ULP at 1000).
assert((m.Hit.Position-direction*1000).Magnitude<.0002)
''',camera.snapshot(projection.PROFILE))


if __name__=='__main__':unittest.main()
