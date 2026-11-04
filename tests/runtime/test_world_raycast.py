"""WorldRoot spatial queries executed by both native Luau hosts."""
import unittest

import test_camera_projection as camera
import test_cframe_transforms as transforms
import test_mouse_picking as picking
from persistent_runtime import PersistentLuauHost


def snapshot(extra=None):
    data=camera.snapshot()
    for n in data['nodes']:
        if n['id']=='scope:root-1':
            n['properties'].update(Size={'$vector3':[2,2,1]},Shape=1,Material=256,
                                   CanQuery=True,CanCollide=True)
    data['nodes'].extend(extra or [])
    return data


SETUP='''
local function part(z,parent)
 local p=Instance.new("Part",parent or workspace)
 p.Anchored=true;p.Size=Vector3.new(4,4,4);p.CFrame=CFrame.new(0,0,z)
 return p
end
local o=Vector3.zero;local d=Vector3.new(0,0,-100)
'''


class WorldRaycastTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(SETUP+source,data or snapshot())

    def test_nearest_result_fields_types_and_readonly_snapshot(self):
        self.check('''
local far=part(-20);local p=part(-10);p.Material=Enum.Material.Metal
local hit=workspace:Raycast(o,d)
assert(typeof(hit)=="RaycastResult" and hit.Instance==p)
near(hit.Distance,8);vec(hit.Position,Vector3.new(0,0,-8));vec(hit.Normal,Vector3.zAxis)
assert(hit.Material==Enum.Material.Metal)
for _,key in ipairs({"Instance","Position","Normal","Distance","Material"})do
 assert(not pcall(function()hit[key]=hit[key]end))
end
p.CFrame=CFrame.new(0,0,-30);p.Material=Enum.Material.Wood
near(hit.Distance,8);assert(hit.Material==Enum.Material.Metal)
assert(workspace:Raycast(o,d).Instance==far)
far:Destroy();p:Destroy();assert(workspace:Raycast(o,d)==nil)
assert(hit.Instance==p)
''')

    def test_direction_is_segment_length_and_does_not_depend_on_camera(self):
        self.check('''
local p=part(-10)
assert(workspace:Raycast(o,Vector3.new(0,0,-7.99))==nil)
near(workspace:Raycast(o,Vector3.new(0,0,-8)).Distance,8)
assert(workspace:Raycast(o,Vector3.zero)==nil)
workspace.CurrentCamera.CFrame=CFrame.new(100,100,100)
near(workspace:Raycast(o,d,nil).Distance,8)
p.Position=Vector3.new(0,0,-15002)
near(workspace:Raycast(o,Vector3.new(0,0,-15000)).Distance,15000)
assert(not pcall(function()workspace:Raycast(o,Vector3.new(0,0,-15001))end))
''')

    def test_params_defaults_canonical_enum_and_mutable_identity(self):
        self.check('''
local p=RaycastParams.new();local same=p
assert(typeof(p)=="RaycastParams" and p.FilterType==Enum.RaycastFilterType.Exclude)
assert(#p.FilterDescendantsInstances==0 and p.IncludeInstances==nil and p.ExcludeInstances==nil)
assert(not p.IgnoreWater and not p.RespectCanCollide and not p.BruteForceAllSlow)
assert(p.CollisionGroup=="Default" and p.Margin==0 and p.SizeGradient==0)
assert(Enum.RaycastFilterType.Blacklist==Enum.RaycastFilterType.Exclude)
assert(Enum.RaycastFilterType.Whitelist==Enum.RaycastFilterType.Include)
assert(Enum.RaycastFilterType:FromName("Whitelist")==Enum.RaycastFilterType.Include)
assert(#Enum.RaycastFilterType:GetEnumItems()==2)
p.IgnoreWater=true;p.SizeGradient=-1;assert(same.IgnoreWater and same.SizeGradient==-1)
assert(RaycastParams.new()~=p)
''')

    def test_legacy_filter_descendants_include_and_add_to_filter(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local p=part(-10,folder);local far=part(-20)
local params=RaycastParams.new();params.FilterDescendantsInstances={folder}
assert(workspace:Raycast(o,d,params).Instance==far)
params.FilterType=Enum.RaycastFilterType.Include
assert(workspace:Raycast(o,d,params).Instance==p)
params.FilterDescendantsInstances={};assert(workspace:Raycast(o,d,params)==nil)
params:AddToFilter(far);assert(workspace:Raycast(o,d,params).Instance==far)
params:AddToFilter({folder});assert(workspace:Raycast(o,d,params).Instance==p)
''')

    def test_new_include_exclude_filters_and_nil_vs_empty(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local p=part(-10,folder);local far=part(-20,folder)
local outsider=part(-5);local params=RaycastParams.new()
params.IncludeInstances={folder};params.ExcludeInstances={p}
assert(workspace:Raycast(o,d,params).Instance==far)
params.ExcludeInstances={folder};assert(workspace:Raycast(o,d,params)==nil)
params.ExcludeInstances=nil;assert(workspace:Raycast(o,d,params).Instance==p)
params.IncludeInstances={};assert(workspace:Raycast(o,d,params)==nil)
params.IncludeInstances=nil;assert(workspace:Raycast(o,d,params).Instance==outsider)
''')

    def test_filter_arrays_are_copied_and_invalid_assignment_is_atomic(self):
        self.check('''
local p=part(-10);local far=part(-20);local params=RaycastParams.new()
local list={p};params.FilterDescendantsInstances=list;list[1]=far
assert(params.FilterDescendantsInstances[1]==p)
local read=params.FilterDescendantsInstances;read[1]=far
assert(workspace:Raycast(o,d,params).Instance==far)
for _,bad in ipairs({{p,12},{oops=p},{[2]=p},"bad"})do
 assert(not pcall(function()params.FilterDescendantsInstances=bad end))
 assert(params.FilterDescendantsInstances[1]==p)
end
assert(not pcall(function()params:AddToFilter({far,12})end))
assert(#params.FilterDescendantsInstances==1)
''')

    def test_default_raycast_includes_character_unlike_mouse(self):
        self.check('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.CFrame=CFrame.new(0,0,-5);root.Size=Vector3.new(4,4,4)
assert(workspace:Raycast(o,d).Instance==root)
local params=RaycastParams.new();params.ExcludeInstances={root.Parent}
local p=part(-10);assert(workspace:Raycast(o,d,params).Instance==p)
''')

    def test_filter_capacity_failure_preserves_existing_list(self):
        self.check('''
local p=part(-10);local params=RaycastParams.new()
params.FilterDescendantsInstances=table.create(4096,p)
assert(not pcall(function()params:AddToFilter(p)end))
assert(#params.FilterDescendantsInstances==4096)
assert(not pcall(function()params.FilterDescendantsInstances=table.create(4097,p)end))
assert(#params.FilterDescendantsInstances==4096)
assert(workspace:Raycast(o,d,params)==nil)
''')

    def test_query_collision_flags_and_brute_force(self):
        self.check('''
local p=part(-10);local far=part(-20);local params=RaycastParams.new()
p.CanCollide=false;p.CanQuery=true
assert(workspace:Raycast(o,d,params).Instance==p)
params.RespectCanCollide=true;assert(workspace:Raycast(o,d,params).Instance==far)
params.RespectCanCollide=false;p.CanQuery=false
assert(workspace:Raycast(o,d,params).Instance==far)
params.BruteForceAllSlow=true;assert(workspace:Raycast(o,d,params).Instance==p)
params.BruteForceAllSlow=false;p.CanCollide=true
assert(workspace:Raycast(o,d,params).Instance==p)
''')

    def test_worldmodels_are_isolated_query_worlds(self):
        self.check('''
local model=Instance.new("WorldModel",workspace)
local inside=part(-10,model);local outside=part(-20)
assert(model:Raycast(o,d).Instance==inside)
assert(workspace:Raycast(o,d).Instance==outside)
model.Parent=nil;assert(model:Raycast(o,d).Instance==inside)
model:Destroy();assert(not pcall(function()model:Raycast(o,d)end))
''')

    def test_analytic_round_geometry_and_world_normal(self):
        self.check('''
local p=part(-10);p.Shape=Enum.PartType.Ball
local hit=workspace:Raycast(Vector3.new(1,0,0),d)
near(hit.Distance,10-math.sqrt(3));vec(hit.Normal,Vector3.new(.5,0,math.sqrt(3)/2))
p.Shape=Enum.PartType.Block;p.CFrame=CFrame.new(0,0,-10)*CFrame.Angles(0,math.pi/4,0)
hit=workspace:Raycast(Vector3.new(.1,0,0),d)
vec(hit.Normal,Vector3.new(math.sqrt(.5),0,math.sqrt(.5)))
''')

    def test_spherecast_reports_contact_point_travel_and_normal(self):
        self.check('''
local p=part(-10);p.Material=Enum.Material.Metal
local start=Vector3.new(2.6,0,0)
assert(workspace:Raycast(start,d)==nil)
local hit=workspace:Spherecast(start,1,d)
assert(typeof(hit)=="RaycastResult" and hit.Instance==p and hit.Material==Enum.Material.Metal)
near(hit.Distance,7.2);vec(hit.Position,Vector3.new(2,0,-8))
vec(hit.Normal,Vector3.new(.6,0,.8))
assert(workspace:Spherecast(Vector3.new(2.8,2.8,0),1,d)==nil)
assert(workspace:Spherecast(start,1,Vector3.new(0,0,-7))==nil)
p.Shape=Enum.PartType.Ball
hit=workspace:Spherecast(Vector3.new(2.4,0,0),1,d)
near(hit.Distance,8.2);vec(hit.Position,Vector3.new(1.6,0,-8.8));vec(hit.Normal,Vector3.new(.8,0,.6))
local tangent=workspace:Spherecast(Vector3.new(3,0,0),1,d)
near(tangent.Distance,10);vec(tangent.Position,Vector3.new(2,0,-10))
p.Shape=Enum.PartType.Block;p.CFrame=CFrame.new(12,5,-10)*CFrame.Angles(.3,.4,.5)
local cf=p.CFrame
hit=workspace:Spherecast(cf*Vector3.new(2.6,0,10),1,cf:VectorToWorldSpace(d))
near(hit.Distance,7.2);vec(hit.Position,cf*Vector3.new(2,0,2))
vec(hit.Normal,cf:VectorToWorldSpace(Vector3.new(.6,0,.8)))
''')

    def test_spherecast_skips_initial_overlap_and_uses_world_filters(self):
        self.check('''
local model=Instance.new("WorldModel",workspace)
local start=part(0,model);local nearPart=part(-8,model);local farPart=part(-15,model)
local outside=part(-4)
local hit=model:Spherecast(o,1,d)
assert(hit.Instance==nearPart);near(hit.Distance,5)
assert(workspace:Spherecast(o,1,d).Instance==outside)
local params=RaycastParams.new();params.ExcludeInstances={nearPart}
assert(model:Spherecast(o,1,d,params).Instance==farPart)
farPart.CanCollide=false;farPart.CanQuery=true;params.RespectCanCollide=true
assert(model:Spherecast(o,1,d,params)==nil)
params.RespectCanCollide=false
assert(model:Spherecast(o,1,d,params).Instance==farPart)
assert(model:Spherecast(o,1,Vector3.zero)==nil)
assert(model:Spherecast(o,1,Vector3.new(0,0,-1024)).Instance==nearPart)
for _,radius in {-1,0,257,math.huge,"1"} do
    assert(not pcall(model.Spherecast,model,o,radius,d))
end
assert(not pcall(model.Spherecast,model,o,1,Vector3.new(0,0,-1025)))
assert(not pcall(model.Spherecast,model,o,1,d,OverlapParams.new()))
''')

    def test_spherecast_uses_decoded_triangles_and_terrain_materials(self):
        mesh=picking.node('MeshPart',{'kind':'triangle_mesh',
            'vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],'triangles':[[0,1,2]]})
        mesh['properties']['Material']=1088
        terrain=picking.node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-6],
            'cell_size':[2,2,2],'dimensions':[1,1,3],'occupancy':[True,True,True],
            'materials':[896,2048,2048]})
        terrain.update(id='terrain:cast',name='CastTerrain')
        self.check('''
local start=Vector3.new(0,0,1)
local params=RaycastParams.new()
local hit=workspace:Spherecast(start,.5,d,params)
assert(hit.Instance==workspace.CastTerrain and hit.Material==Enum.Material.Water)
near(hit.Distance,.5);vec(hit.Position,Vector3.zero)
params.IgnoreWater=true
hit=workspace:Spherecast(start,.5,d,params)
assert(hit.Material==Enum.Material.Rock);near(hit.Distance,4.5)
vec(hit.Position,Vector3.new(0,0,-4));vec(hit.Normal,Vector3.zAxis)
params.ExcludeInstances={workspace.CastTerrain}
local mesh=workspace.PickTest
hit=workspace:Spherecast(o,1,d,params)
assert(hit.Instance==mesh and hit.Material==Enum.Material.Metal)
near(hit.Distance,9);vec(hit.Position,Vector3.new(0,0,-10))
assert(workspace:Spherecast(Vector3.new(0,0,-9.5),1,d,params)==nil)
mesh.CFrame=CFrame.new(0,0,-20)
near(workspace:Spherecast(o,1,d,params).Distance,19)
''',snapshot([mesh,terrain]))

    def test_blockcast_rotated_box_reports_contact_and_distance(self):
        self.check('''
local target=part(-10);target.Material=Enum.Material.Metal
local cf=CFrame.new(2.8,0,0)*CFrame.Angles(0,0,math.pi/4)
local size=Vector3.new(2,2,2)
assert(workspace:Raycast(cf.Position,d)==nil)
assert(workspace:Blockcast(cf,size,Vector3.new(0,0,-6.999))==nil)
local hit=workspace:Blockcast(cf,size,Vector3.new(0,0,-7))
assert(typeof(hit)=="RaycastResult" and hit.Instance==target and hit.Material==Enum.Material.Metal)
near(hit.Distance,7);near(hit.Position.Z,-8);vec(hit.Normal,Vector3.zAxis)
assert(hit.Position.X<=2 and math.abs(hit.Position.Y)<=2)
local impact=(cf-Vector3.new(0,0,hit.Distance)):PointToObjectSpace(hit.Position)
assert(math.abs(impact.X)<1.00001 and math.abs(impact.Y)<1.00001)
near(impact.Z,-1)
''')

    def test_blockcast_handles_curved_and_sloped_targets(self):
        self.check('''
local target=part(-10);target.Shape=Enum.PartType.Ball
local size=Vector3.one
assert(workspace:Blockcast(CFrame.new(2.4,2.4,0),size,d)==nil)
local hit=workspace:Blockcast(CFrame.new(2.2,0,0),size,d)
local z=math.sqrt(4-1.7^2)
near(hit.Distance,9.5-z)
vec(hit.Position,Vector3.new(1.7,0,-10+z))
vec(hit.Normal,Vector3.new(1.7,0,z)/2)
target:Destroy()
local wedge=Instance.new("WedgePart",workspace)
wedge.Anchored=true;wedge.Size=Vector3.new(4,4,4)
hit=workspace:Blockcast(CFrame.new(0,5,0),size,Vector3.new(0,-10,0))
assert(hit.Instance==wedge);near(hit.Distance,4)
near(hit.Position.Y,.5);near(hit.Position.Z,.5)
vec(hit.Normal,Vector3.new(0,1,-1).Unit)
''')

    def test_blockcast_skips_starting_overlap_and_keeps_world_filters(self):
        self.check('''
local model=Instance.new("WorldModel",workspace)
local start=part(0,model);local nearPart=part(-8,model);local farPart=part(-15,model)
local outside=part(-4)
local cf=CFrame.identity;local size=Vector3.new(2,2,2)
local hit=model:Blockcast(cf,size,d)
assert(hit.Instance==nearPart);near(hit.Distance,5)
assert(workspace:Blockcast(cf,size,d).Instance==outside)
local params=RaycastParams.new();params.ExcludeInstances={nearPart}
assert(model:Blockcast(cf,size,d,params).Instance==farPart)
farPart.CanCollide=false;farPart.CanQuery=true;params.RespectCanCollide=true
assert(model:Blockcast(cf,size,d,params)==nil)
assert(model:Blockcast(cf,size,Vector3.zero)==nil)
for _,bad in {Vector3.zero,Vector3.new(-1,1,1),Vector3.new(513,1,1)} do
    assert(not pcall(model.Blockcast,model,cf,bad,d))
end
assert(not pcall(model.Blockcast,model,cf,size,Vector3.new(0,0,-1025)))
assert(not pcall(model.Blockcast,model,cf,size,d,OverlapParams.new()))
''')

    def test_blockcast_uses_decoded_meshes_and_terrain_water_filter(self):
        mesh=picking.node('MeshPart',{'kind':'triangle_mesh',
            'vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],'triangles':[[0,1,2]]})
        mesh['properties']['Material']=1088
        terrain=picking.node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-6],
            'cell_size':[2,2,2],'dimensions':[1,1,3],'occupancy':[True,True,True],
            'materials':[896,2048,2048]})
        terrain.update(id='terrain:blockcast',name='CastTerrain')
        self.check('''
local cf=CFrame.new(0,0,1);local size=Vector3.one
local params=RaycastParams.new()
local hit=workspace:Blockcast(cf,size,d,params)
assert(hit.Instance==workspace.CastTerrain and hit.Material==Enum.Material.Water)
near(hit.Distance,.5);near(hit.Position.Z,0)
params.IgnoreWater=true
hit=workspace:Blockcast(cf,size,d,params)
assert(hit.Material==Enum.Material.Rock);near(hit.Distance,4.5)
near(hit.Position.Z,-4);vec(hit.Normal,Vector3.zAxis)
params.ExcludeInstances={workspace.CastTerrain}
local mesh=workspace.PickTest
hit=workspace:Blockcast(cf,size,d,params)
assert(hit.Instance==mesh and hit.Material==Enum.Material.Metal)
near(hit.Distance,10.5);near(hit.Position.Z,-10)
assert(workspace:Blockcast(CFrame.new(0,0,-9.8),size,d,params)==nil)
mesh.CFrame=CFrame.new(0,0,-20)
near(workspace:Blockcast(cf,size,d,params).Distance,20.5)
''',snapshot([mesh,terrain]))

    def test_decoded_terrain_material_and_ignore_water(self):
        terrain=picking.node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-6],
            'cell_size':[2,2,2],'dimensions':[1,1,3],'occupancy':[True,True,True],
            'materials':[896,2048,2048]})
        self.check('''
local params=RaycastParams.new()
local hit=workspace:Raycast(Vector3.new(0,0,1),d,params)
near(hit.Distance,1);assert(hit.Material==Enum.Material.Water)
params.IgnoreWater=true
hit=workspace:Raycast(Vector3.new(0,0,1),d,params)
near(hit.Distance,5);assert(hit.Material==Enum.Material.Rock)
vec(hit.Position,Vector3.new(0,0,-4));vec(hit.Normal,Vector3.zAxis)
''',snapshot([terrain]))

    def test_mesh_result_uses_source_material_and_updated_geometry(self):
        mesh=picking.node('MeshPart',{'kind':'triangle_mesh','vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],'triangles':[[0,1,2]]})
        mesh['properties']['Material']=1088
        self.check('''
local p=workspace.PickTest
local hit=workspace:Raycast(o,d);assert(hit.Instance==p and hit.Material==Enum.Material.Metal)
near(hit.Distance,10);vec(hit.Normal,Vector3.zAxis)
p.CFrame=CFrame.new(0,0,-20)
near(workspace:Raycast(o,d).Distance,20)
assert(workspace:Raycast(Vector3.new(1.9,1.9,0),d)==nil)
''',snapshot([mesh]))

    def test_unsupported_geometry_groups_and_margin_are_explicit(self):
        self.check('''
local p=part(-10);local params=RaycastParams.new()
params.CollisionGroup="Characters"
local ok,err=pcall(function()workspace:Raycast(o,d,params)end)
assert(not ok and string.find(err,"collision group matrix is unavailable"))
params.CollisionGroup="Default";params.Margin=1
ok,err=pcall(function()workspace:Raycast(o,d,params)end)
assert(not ok and string.find(err,"sphere casting"))
''')

    def test_invalid_parameters_and_receivers_leave_valid_queries_usable(self):
        self.check('''
local p=part(-10);local params=RaycastParams.new()
for _,bad in ipairs({
 function()params.FilterType=Enum.NormalId.Top end,
 function()params.IgnoreWater=1 end,
 function()params.RespectCanCollide="yes" end,
 function()params.CollisionGroup={}end,
 function()params.Margin=-1 end,
 function()params.Margin=math.huge end,
 function()params.SizeGradient=-2 end,
 function()params.SizeGradient=0/0 end,
 function()params.NotAField=true end,
 function()workspace:Raycast({},d)end,
 function()workspace:Raycast(o,Vector3.new(0/0,0,0))end,
 function()workspace:Raycast(o,d,{})end,
 function()workspace.Raycast(p,o,d)end,
})do assert(not pcall(bad))end
assert(workspace:Raycast(o,d,params).Instance==p)
''')


class PersistentRaycastTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute

    def start(self,extra=None):
        self.host=PersistentLuauHost(snapshot(extra));self.addCleanup(self.host.close)

    def test_result_can_be_returned_to_the_console_with_typed_fields(self):
        self.start()
        result=self.execute(SETUP+'''
local p=part(-10);p.Material=Enum.Material.Metal
return workspace:Raycast(o,d)
''')['values'][0]['$raycast_result']
        self.assertTrue(result['Instance']['$ref'].startswith('local:'))
        self.assertEqual(result['Position'],{'$vector3':[0,0,-8]})
        self.assertEqual(result['Normal'],{'$vector3':[0,0,1]})
        self.assertEqual(result['Distance'],8)
        self.assertEqual(result['Material'],{'$enum':{'type':'Material','value':1088}})

    def test_received_transform_updates_change_next_query_but_not_prior_result(self):
        n=picking.node('Part');n['properties'].update(Shape=1,Material=256)
        self.start([n])
        self.execute('oldHit=workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100))')
        changed=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':10,'kind':'set','id':'pick:test','fields':{
                'CFrame':{'$cframe':{'position':[0,0,-20],'rotation':[1,0,0,0,1,0,0,0,1]}}}}]}})
        self.assertTrue(changed['ok'],changed)
        self.assertEqual(self.execute('return oldHit.Distance,workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100)).Distance')['values'],[8,18])

    def test_hit_and_params_survive_bindable_event_delivery(self):
        self.start()
        self.execute(SETUP+'''
local p=part(-10);local params=RaycastParams.new()
local hit=workspace:Raycast(o,d,params)
local event=Instance.new("BindableEvent")
delivery=false
event.Event:Connect(function(received,options)
 assert(typeof(received)=="RaycastResult" and received.Instance==p)
 assert(received.Distance==8 and typeof(options)=="RaycastParams")
 assert(options==params)
 options.ExcludeInstances={p};assert(workspace:Raycast(o,d,params)==nil)
 delivery=true
end)
event:Fire(hit,params)
''')
        self.assertEqual(self.execute('return delivery')['values'],[True])

    def test_bindable_function_can_accept_params_and_return_typed_hit(self):
        self.start()
        values=self.execute(SETUP+'''
local p=part(-10);local options=RaycastParams.new()
local query=Instance.new("BindableFunction")
query.OnInvoke=function(params)
 assert(params==options)
 return workspace:Raycast(o,d,params)
end
local hit=query:Invoke(options)
assert(typeof(hit)=="RaycastResult" and hit.Instance==p)
options.ExcludeInstances={p};assert(query:Invoke(options)==nil)
return hit.Distance,hit.Material.Name
''')['values']
        self.assertEqual(values,[8,'Plastic'])


if __name__=='__main__':unittest.main()
