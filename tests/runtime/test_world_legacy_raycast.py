"""Legacy Ray argument/tuple contracts over the shared native geometry engine."""
import unittest

from persistent_runtime import PersistentLuauHost
import test_cframe_transforms as transforms
import test_collision_groups as groups
import test_mouse_picking as picking
import test_world_overlap as overlap
import test_world_raycast as raycast


SETUP=raycast.SETUP+'''
local ray=Ray.new(o,d)
'''


class LegacyRaycastTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(SETUP+source,data or overlap.snapshot())

    def test_all_methods_return_four_values_with_nearest_hit(self):
        self.check('''
local far=part(-20);local p=part(-10);p.Material=Enum.Material.Metal
local calls={
 function()return workspace:FindPartOnRay(ray)end,
 function()return workspace:findPartOnRay(ray)end,
 function()return workspace:FindPartOnRayWithIgnoreList(ray,{})end,
 function()return workspace:FindPartOnRayWithWhitelist(ray,{workspace})end,
}
for _,call in calls do
 local values=table.pack(call());assert(values.n==4)
 assert(values[1]==p and values[4]==Enum.Material.Metal)
 vec(values[2],Vector3.new(0,0,-8));vec(values[3],Vector3.zAxis)
end
p:Destroy();assert(workspace:FindPartOnRay(ray)==far)
''')

    def test_miss_and_zero_ray_return_endpoint_zero_normal_and_air(self):
        self.check('''
for _,r in {ray,Ray.new(Vector3.new(1,2,3),Vector3.new(4,5,6)),Ray.new(Vector3.new(7,8,9),Vector3.zero)} do
 local values=table.pack(workspace:FindPartOnRay(r))
 assert(values.n==4 and values[1]==nil and values[4]==Enum.Material.Air)
 vec(values[2],r.Origin+r.Direction);vec(values[3],Vector3.zero)
end
local p=part(-10)
local item,position,normal,material=workspace:FindPartOnRayWithWhitelist(ray,{})
assert(item==nil and material==Enum.Material.Air);vec(position,o+d);vec(normal,Vector3.zero)
''')

    def test_ray_magnitude_is_segment_length_including_endpoint(self):
        self.check('''
local p=part(-10)
assert(workspace:FindPartOnRay(Ray.new(o,Vector3.new(0,0,-7.99)))==nil)
assert(workspace:FindPartOnRay(Ray.new(o,Vector3.new(0,0,-8)))==p)
assert(workspace:FindPartOnRay(Ray.new(o,Vector3.new(0,0,100)))==nil)
p.Position=Vector3.new(0,0,-15002)
assert(workspace:FindPartOnRay(Ray.new(o,Vector3.new(0,0,-15000)))==p)
assert(not pcall(function()workspace:FindPartOnRay(Ray.new(o,Vector3.new(0,0,-15001)))end))
''')

    def test_single_ignore_includes_descendants_and_local_character_by_default(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local a=part(-5,folder);local b=part(-10,a)
local c=part(-20)
assert(workspace:FindPartOnRay(ray,folder)==c)
assert(workspace:FindPartOnRay(ray,a)==c)
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.CFrame=CFrame.new(0,0,-2)
assert(workspace:FindPartOnRay(ray)==root)
assert(workspace:FindPartOnRay(ray,root.Parent)==a)
assert(workspace:FindPartOnRay(ray,workspace)==nil)
''')

    def test_ignore_whitelist_and_nil_terminated_lists(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local a=part(-5,folder);local b=part(-10);local c=part(-20)
assert(workspace:FindPartOnRayWithIgnoreList(ray,{folder,b})==c)
assert(workspace:FindPartOnRayWithWhitelist(ray,{folder,b})==a)
assert(workspace:FindPartOnRayWithWhitelist(ray,{b,c})==b)
local list={[1]=folder,[3]=b,[4]=true}
assert(workspace:FindPartOnRayWithIgnoreList(ray,list)==b)
assert(workspace:FindPartOnRayWithWhitelist(ray,list)==a)
list=setmetatable({},{__index=function()error("must not run")end})
assert(workspace:FindPartOnRayWithWhitelist(ray,list)==nil)
assert(workspace:FindPartOnRayWithIgnoreList(ray,list)==a)
''')

    def test_legacy_hits_match_modern_analytic_geometry(self):
        self.check('''
for _,kind in {"Part","WedgePart","CornerWedgePart"} do
 local p=Instance.new(kind,workspace);p.Size=Vector3.new(4,4,4);p.Material=Enum.Material.Metal
 p.CFrame=CFrame.new(0,0,-10)*CFrame.Angles(.2,.4,-.3)
 for _,shape in {Enum.PartType.Block,Enum.PartType.Ball,Enum.PartType.Cylinder} do
  if kind=="Part" then p.Shape=shape end
  for _,x in {-1,0,1} do
   local origin=Vector3.new(x,0,0);local modern=workspace:Raycast(origin,d)
   local item,position,normal,material=workspace:FindPartOnRay(Ray.new(origin,d))
   if modern then
    assert(item==modern.Instance and material==modern.Material)
    vec(position,modern.Position);vec(normal,modern.Normal)
   else assert(item==nil);vec(position,origin+d)end
  end
 end
 p:Destroy()
end
''')

    def test_camera_generated_ray_and_constructor_replacement(self):
        self.check('''
local camera=workspace.CurrentCamera
local p=part(-10);camera.CFrame=CFrame.identity
local cameraRay=camera:ViewportPointToRay(400,300)
assert(typeof(cameraRay)=="Ray")
local longCameraRay=Ray.new(cameraRay.Origin,cameraRay.Direction*100)
Ray={new=function()error("must not be called")end}
assert(workspace:FindPartOnRay(cameraRay.Unit)==nil)
-- The camera ray has unit length; preserve its direction and use a longer
-- previously authored ray without consulting the overwritten constructor.
assert(workspace:FindPartOnRay(ray)==p)
assert(workspace:FindPartOnRay(longCameraRay)==p)
''')

    def test_collision_flags_and_default_group_filter(self):
        data=overlap.snapshot()
        next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroupData']=groups.PUBLIC
        self.check('''
local a=part(-5);local b=part(-10)
a.CollisionGroup="TCAvatar_CollisionGroup"
assert(workspace:FindPartOnRay(ray)==b)
assert(workspace:FindPartOnRayWithWhitelist(ray,{a})==nil)
a.CollisionGroup="Default";a.CanCollide=false;a.CanQuery=false
assert(workspace:FindPartOnRay(ray)==b)
a.CanQuery=true;assert(workspace:FindPartOnRay(ray)==a)
a.CanQuery=false;a.CanCollide=true;assert(workspace:FindPartOnRay(ray)==a)
''',data)

    def test_worldmodel_boundaries_detached_queries_and_local_reparent(self):
        self.check('''
local model=Instance.new("WorldModel",workspace)
local a=part(-5,model);local b=part(-10)
assert(model:FindPartOnRay(ray)==a and workspace:FindPartOnRay(ray)==b)
model.Parent=nil;assert(model:FindPartOnRayWithWhitelist(ray,{model})==a)
a.Parent=workspace;assert(model:FindPartOnRay(ray)==nil and workspace:FindPartOnRay(ray)==a)
model:Destroy();assert(not pcall(function()model:FindPartOnRay(ray)end))
''')

    def test_decoded_voxel_water_flags_use_correct_argument_positions(self):
        terrain=picking.node('Terrain',{'kind':'voxel_grid','origin':[-1,-1,-6],
            'cell_size':[2,2,2],'dimensions':[1,1,3],'occupancy':[True,True,True],
            'materials':[896,2048,2048]})
        self.check('''
local terrain=workspace.PickTest;local r=Ray.new(Vector3.new(0,0,1),d)
local item,position,normal,material=workspace:FindPartOnRay(r)
assert(item==terrain and material==Enum.Material.Water)
vec(position,Vector3.zero);vec(normal,Vector3.zAxis)
for _,cubes in {false,true} do
 local p,hit,n,m=workspace:FindPartOnRay(r,nil,cubes,true)
 assert(p==terrain and m==Enum.Material.Rock);vec(hit,Vector3.new(0,0,-4));vec(n,Vector3.zAxis)
 p,hit,n,m=workspace:FindPartOnRayWithIgnoreList(r,{},cubes,true)
 assert(p==terrain and m==Enum.Material.Rock);vec(hit,Vector3.new(0,0,-4))
end
local p,hit,n,m=workspace:FindPartOnRayWithWhitelist(r,{terrain},true)
assert(p==terrain and m==Enum.Material.Rock);vec(hit,Vector3.new(0,0,-4))
assert(workspace:FindPartOnRayWithIgnoreList(r,{terrain},true,true)==nil)
''',overlap.snapshot([terrain]))

    def test_ignore_water_does_not_skip_water_material_on_ordinary_parts(self):
        self.check('''
local p=part(-10);p.Material=Enum.Material.Water
assert(workspace:FindPartOnRay(ray,nil,false,true)==p)
assert(workspace:FindPartOnRayWithWhitelist(ray,{p},true)==p)
''')

    def test_cube_terrain_requires_cells_and_mesh_only_mode_is_explicit(self):
        terrain=picking.node('Terrain',{'kind':'triangle_mesh','vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],
            'triangles':[[0,1,2]],'material':896})
        self.check('''
local p,position,normal,material=workspace:FindPartOnRay(ray)
assert(p==workspace.PickTest and material==Enum.Material.Rock)
vec(position,Vector3.new(0,0,-10))
local ok,err=pcall(function()workspace:FindPartOnRay(ray,nil,true)end)
assert(not ok and string.find(err,"requires decoded voxel_grid"))
assert(workspace:FindPartOnRay(ray,workspace.PickTest,true)==nil)
''',overlap.snapshot([terrain]))

    def test_decoded_mesh_queries_track_local_pose_and_size(self):
        mesh=picking.node('MeshPart',{'kind':'triangle_mesh','vertices':[[-2,-2,-10],[2,-2,-10],[0,2,-10]],'triangles':[[0,1,2]]})
        mesh['properties']['Material']=1088
        self.check('''
local p=workspace.PickTest
local item,position,normal,material=workspace:FindPartOnRay(ray)
assert(item==p and material==Enum.Material.Metal);vec(position,Vector3.new(0,0,-10))
p.CFrame=CFrame.new(0,0,-20)
item,position=workspace:FindPartOnRay(ray);assert(item==p);vec(position,Vector3.new(0,0,-20))
local r=Ray.new(Vector3.new(1.9,1.9,0),d)
assert(workspace:FindPartOnRay(r)==nil)
p.Size=Vector3.new(16,16,4);assert(workspace:FindPartOnRay(r)==p)
''',overlap.snapshot([mesh]))

    def test_missing_mesh_geometry_and_hit_material_do_not_become_misses(self):
        self.check('''
local ok,err=pcall(function()workspace:FindPartOnRay(ray)end)
assert(not ok and string.find(err,"collision geometry is unavailable"))
assert(workspace:FindPartOnRay(ray,workspace.PickTest)==nil)
''',overlap.snapshot([picking.node('MeshPart')]))
        node=picking.node('Part');node['properties']['Shape']=1
        self.check('''
local ok,err=pcall(function()workspace:FindPartOnRay(ray)end)
assert(not ok and string.find(err,"material is unavailable"))
''',overlap.snapshot([node]))

    def test_invalid_parameters_and_filter_capacity_leave_valid_calls_usable(self):
        self.check('''
local p=part(-10)
assert(workspace:FindPartOnRayWithIgnoreList(ray,table.create(4096,p))==nil)
assert(not pcall(function()workspace:FindPartOnRayWithIgnoreList(ray,table.create(4097,p))end))
for _,bad in {
 function()workspace:FindPartOnRay({Origin=o,Direction=d})end,
 function()workspace:FindPartOnRay(Ray.new(Vector3.new(math.huge,0,0),d))end,
 function()workspace:FindPartOnRay(Ray.new(o,Vector3.new(0/0,0,0)))end,
 function()workspace:FindPartOnRay(Ray.new(o,Vector3.zero).Unit)end,
 function()workspace:FindPartOnRay(ray,{p})end,
 function()workspace:FindPartOnRay(ray,nil,1)end,
 function()workspace:FindPartOnRay(ray,nil,false,"yes")end,
 function()workspace:FindPartOnRayWithWhitelist(ray,{p},1)end,
 function()workspace:FindPartOnRayWithWhitelist(ray,{p},false,true)end,
 function()workspace:FindPartOnRayWithIgnoreList(ray,nil)end,
 function()workspace:FindPartOnRayWithIgnoreList(ray,{p,1})end,
 function()workspace:FindPartOnRayWithWhitelist(ray)end,
 function()workspace.FindPartOnRay(p,ray)end,
 function()workspace:FindPartOnRay()end,
 function()workspace:FindPartOnRay(ray,nil,false,false,1)end,
} do assert(not pcall(bad))end
assert(workspace:FindPartOnRay(ray,nil,nil,nil)==p)
''')


class PersistentLegacyRaycastTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute

    def start(self,data=None):
        data=data or overlap.snapshot()
        self.sequence=data.get('sequence',0)+100
        self.host=PersistentLuauHost(data);self.addCleanup(self.host.close)

    def update(self,node,fields):
        self.sequence+=1
        reply=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':self.sequence,'kind':'set','id':node,'fields':fields}]}})
        self.assertTrue(reply['ok'],reply)

    def test_received_pose_material_and_group_changes_refresh_tuple(self):
        node=picking.node('Part');node['properties'].update(Shape=1,Material=256)
        self.start(overlap.snapshot([node]))
        self.execute('ray=Ray.new(Vector3.zero,Vector3.new(0,0,-100))')
        values=self.execute('return workspace:FindPartOnRay(ray)')['values']
        self.assertEqual(values[1],{'$vector3':[0,0,-8]})
        self.assertEqual(values[3],{'$enum':{'name':'Plastic','type':'Material','value':256}})
        self.update('pick:test',{'CFrame':{'$cframe':{'position':[0,0,-20],'rotation':[1,0,0,0,1,0,0,0,1]}},'Material':1088})
        values=self.execute('return workspace:FindPartOnRay(ray)')['values']
        self.assertEqual(values[1],{'$vector3':[0,0,-18]})
        self.assertEqual(values[3],{'$enum':{'name':'Metal','type':'Material','value':1088}})
        self.update('scope:workspace',{'CollisionGroupData':groups.encoded([(0,'Default',-3),(1,'Ghost',-2)])})
        self.update('pick:test',{'CollisionGroup':'Ghost'})
        values=self.execute('return workspace:FindPartOnRay(ray)')['values']
        self.assertEqual(values,[None,{'$vector3':[0,0,-100]},{'$vector3':[0,0,0]},{'$enum':{'name':'Air','type':'Material','value':1792}}])

    def test_bindable_call_transports_ray_hit_and_nil_tuple(self):
        self.start()
        self.execute(SETUP+'''
local p=part(-10);local f=Instance.new("BindableFunction")
f.OnInvoke=function(received,ignore)
 assert(typeof(received)=="Ray" and received==ray)
 return workspace:FindPartOnRay(received,ignore)
end
local hit=table.pack(f:Invoke(ray,nil));assert(hit.n==4 and hit[1]==p)
local miss=table.pack(f:Invoke(ray,p));assert(miss.n==4 and miss[1]==nil)
assert(miss[4]==Enum.Material.Air and miss[2]==d)
''')


if __name__=='__main__':unittest.main()
