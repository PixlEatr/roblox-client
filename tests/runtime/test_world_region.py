"""Legacy region-query contracts and explicit adapter policies in native hosts."""
import unittest

from persistent_runtime import PersistentLuauHost
import test_cframe_transforms as transforms
import test_collision_groups as groups
import test_mouse_picking as picking
import test_world_overlap as overlap


SETUP=overlap.SETUP+'''
local region=Region3.new(Vector3.new(-5,-5,-5),Vector3.new(5,5,5))
'''


def many_parts(count):
    nodes=[]
    for i in range(count):
        node=picking.node('Part')
        node.update(id='region:'+str(i),name='RegionPart'+str(i))
        node['properties']['CFrame']['$cframe']['position']=[0,0,0]
        nodes.append(node)
    return overlap.snapshot(nodes)


class WorldRegionTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(SETUP+source,data or overlap.snapshot())

    def test_default_20_explicit_limit_unlimited_and_no_obsolete_100_cap(self):
        self.check('''
assert(#workspace:FindPartsInRegion3(region)==20)
assert(#workspace:findPartsInRegion3(region)==20)
assert(#workspace:FindPartsInRegion3(region,nil,7)==7)
assert(#workspace:FindPartsInRegion3(region,nil,120)==120)
assert(#workspace:FindPartsInRegion3(region,nil,math.huge)==130)
assert(#workspace:FindPartsInRegion3(region,nil,2147483647)==130)
assert(#workspace:FindPartsInRegion3(region,nil,0)==0)
assert(#workspace:FindPartsInRegion3WithIgnoreList(region,{})==20)
assert(#workspace:FindPartsInRegion3WithWhiteList(region,{workspace})==20)
assert(not workspace:IsRegion3Empty(region))
''',many_parts(130))

    def test_single_ignore_excludes_self_and_descendants(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local a=part(Vector3.zero,nil,folder)
local b=part(Vector3.new(1,0,0),nil,a);local c=part(Vector3.new(2,0,0))
local list=workspace:FindPartsInRegion3(region,folder)
assert(#list==1 and list[1]==c)
list=workspace:FindPartsInRegion3(region,a)
assert(#list==1 and list[1]==c)
assert(workspace:IsRegion3Empty(region,workspace))
assert(not workspace:IsRegion3Empty(region,folder))
c:Destroy();assert(workspace:IsRegion3Empty(region,folder))
assert(#workspace:FindPartsInRegion3(region,folder)==0)
''')

    def test_list_filters_and_empty_whitelist(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local a=part(Vector3.zero,nil,folder)
local b=part(Vector3.new(2,0,0));local c=part(Vector3.new(3,0,0))
local results=workspace:FindPartsInRegion3WithIgnoreList(region,{folder,b})
assert(#results==1 and results[1]==c)
results=workspace:FindPartsInRegion3WithWhiteList(region,{folder,b})
assert(#results==2 and contains(results,a) and contains(results,b))
assert(#workspace:FindPartsInRegion3WithWhiteList(region,{})==0)
assert(#workspace:FindPartsInRegion3WithIgnoreList(region,{})==3)
assert(workspace:IsRegion3EmptyWithIgnoreList(region,{folder,b,c}))
assert(not workspace:IsRegion3EmptyWithIgnoreList(region,{folder,b}))
''')

    def test_legacy_lists_stop_at_first_nil_and_do_not_consult_metatables(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(2,0,0))
local list={[1]=a,[3]=b,[4]="invalid but past first nil",extra=b}
local results=workspace:FindPartsInRegion3WithIgnoreList(region,list)
assert(#results==1 and results[1]==b)
results=workspace:FindPartsInRegion3WithWhiteList(region,list)
assert(#results==1 and results[1]==a)
assert(not workspace:IsRegion3EmptyWithIgnoreList(region,list))
list=setmetatable({},{__index=function()error("must not run")end,__len=function()error("must not run")end})
assert(#workspace:FindPartsInRegion3WithIgnoreList(region,list)==2)
assert(#workspace:FindPartsInRegion3WithWhiteList(region,list)==0)
list[2]=a;assert(#workspace:FindPartsInRegion3WithWhiteList(region,list)==0)
''')

    def test_empty_queries_use_documented_four_stud_grid(self):
        self.check('''
local small=Region3.new(Vector3.new(.1,.1,.1),Vector3.new(.3,.3,.3))
local p=part(Vector3.new(3,3,3),Vector3.new(.2,.2,.2))
assert(#workspace:FindPartsInRegion3(small)==0)
assert(not workspace:IsRegion3Empty(small))
assert(not workspace:IsRegion3EmptyWithIgnoreList(small,{}))
assert(workspace:IsRegion3Empty(small,p))
assert(workspace:IsRegion3EmptyWithIgnoreList(small,{p}))
p.Position=Vector3.new(5,5,5);assert(workspace:IsRegion3Empty(small))
local negative=Region3.new(Vector3.new(-.3,-.3,-.3),Vector3.new(-.1,-.1,-.1))
p.Position=Vector3.new(-3,-3,-3)
assert(not workspace:IsRegion3Empty(negative))
assert(workspace:IsRegion3Empty(small))
''')

    def test_grid_aligned_empty_region_does_not_grow_an_extra_cell(self):
        self.check('''
local aligned=Region3.new(Vector3.zero,Vector3.new(4,4,4))
local p=part(Vector3.new(5,2,2),Vector3.new(.2,.2,.2))
assert(workspace:IsRegion3Empty(aligned))
p.Position=Vector3.new(3.9,2,2);assert(not workspace:IsRegion3Empty(aligned))
''')

    def test_legacy_axis_aligned_part_bounds_differ_from_modern_oriented_bounds(self):
        self.check('''
local p=part(Vector3.zero,Vector3.new(8,.2,.2));p.CFrame=CFrame.Angles(0,0,math.pi/4)
local corner=Region3.new(Vector3.new(1.9,-2.1,-.1),Vector3.new(2.1,-1.9,.1))
assert(workspace:FindPartsInRegion3(corner)[1]==p)
assert(not workspace:IsRegion3Empty(corner))
assert(#workspace:GetPartBoundsInBox(corner.CFrame,corner.Size)==0)
''')

    def test_mesh_bounds_do_not_require_decoded_geometry(self):
        node=picking.node('MeshPart')
        self.check('''
local r=Region3.new(Vector3.new(-3,-3,-13),Vector3.new(3,3,-7))
assert(workspace:FindPartsInRegion3(r)[1]==workspace.PickTest)
assert(not workspace:IsRegion3Empty(r))
assert(workspace:IsRegion3Empty(r,workspace.PickTest))
''',overlap.snapshot([node]))

    def test_limit_counts_results_after_exclusion(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(1,0,0))
local c=part(Vector3.new(2,0,0));local d=part(Vector3.new(3,0,0))
local results=workspace:FindPartsInRegion3WithIgnoreList(region,{a,b},1)
assert(#results==1 and (results[1]==c or results[1]==d))
assert(#workspace:FindPartsInRegion3WithWhiteList(region,{c,d},1)==1)
''')

    def test_worldmodel_scopes_character_parts_and_detached_queries(self):
        self.check('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.CFrame=CFrame.identity
assert(workspace:FindPartsInRegion3(region)[1]==root)
assert(workspace:IsRegion3Empty(region,root.Parent))
root.CFrame=CFrame.new(1000,1000,1000)
local model=Instance.new("WorldModel",workspace);local p=part(Vector3.zero,nil,model)
assert(workspace:IsRegion3Empty(region))
assert(#workspace:FindPartsInRegion3(region)==0)
assert(model:FindPartsInRegion3(region)[1]==p and not model:IsRegion3Empty(region))
model.Parent=nil;assert(model:findPartsInRegion3(region)[1]==p)
assert(model:IsRegion3EmptyWithIgnoreList(region,{p}))
p.Parent=workspace;assert(workspace:FindPartsInRegion3(region)[1]==p)
assert(model:IsRegion3Empty(region))
model:Destroy();assert(not pcall(function()model:IsRegion3Empty(region)end))
''')

    def test_flags_and_default_collision_group_mask(self):
        data=overlap.snapshot()
        next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroupData']=groups.PUBLIC
        self.check('''
local p=part(Vector3.zero)
p.CanCollide=false;assert(#workspace:FindPartsInRegion3(region)==1)
p.CanQuery=false;assert(workspace:IsRegion3Empty(region))
p.CanCollide=true;assert(not workspace:IsRegion3Empty(region))
p.CollisionGroup="TCAvatar_CollisionGroup"
assert(workspace:IsRegion3Empty(region) and #workspace:FindPartsInRegion3(region)==0)
p.CollisionGroup="Default";assert(workspace:FindPartsInRegion3(region)[1]==p)
''',data)

    def test_local_mutations_results_identity_and_destroy(self):
        self.check('''
local p=part(Vector3.zero);local results=workspace:FindPartsInRegion3(region)
results[1]=nil;assert(workspace:FindPartsInRegion3(region)[1]==p)
results=workspace:FindPartsInRegion3(region);p.Name="RegionPartRenamed"
assert(results[1]==workspace.RegionPartRenamed)
p.Position=Vector3.new(20,0,0);assert(workspace:IsRegion3Empty(region))
p.Size=Vector3.new(50,2,2);assert(workspace:FindPartsInRegion3(region)[1]==p)
p:Destroy();assert(workspace:IsRegion3Empty(region) and #results==1)
''')

    def test_native_region_read_survives_global_replacement(self):
        self.check('''
local p=part(Vector3.zero)
Region3={new=function()error("must not be used")end}
CFrame={new=function()error("must not be used")end}
assert(workspace:FindPartsInRegion3(region)[1]==p)
assert(not workspace:IsRegion3Empty(region))
''')

    def test_large_finite_region_bounds_avoid_size_float_overflow(self):
        self.check('''
local p=part(Vector3.zero)
local large=Region3.new(Vector3.new(-3e38,-3e38,-3e38),Vector3.new(3e38,3e38,3e38))
local results=workspace:FindPartsInRegion3WithWhiteList(large,{p})
assert(#results==1 and results[1]==p)
assert(not workspace:IsRegion3Empty(large))
''')

    def test_filter_capacity_and_invalid_arguments_leave_queries_usable(self):
        self.check('''
local p=part(Vector3.zero)
assert(#workspace:FindPartsInRegion3WithIgnoreList(region,table.create(4096,p))==0)
assert(not pcall(function()workspace:FindPartsInRegion3WithIgnoreList(region,table.create(4097,p))end))
for _,bad in {
 function()workspace:FindPartsInRegion3(Vector3.zero)end,
 function()workspace:FindPartsInRegion3(Region3int16.new())end,
 function()workspace:FindPartsInRegion3(Region3.new(Vector3.one,Vector3.zero))end,
 function()workspace:FindPartsInRegion3(region,{p})end,
 function()workspace:FindPartsInRegion3WithIgnoreList(region,nil)end,
 function()workspace:FindPartsInRegion3WithWhiteList(region)end,
 function()workspace:IsRegion3EmptyWithIgnoreList(region,p)end,
 function()workspace:FindPartsInRegion3WithIgnoreList(region,{p,1})end,
 function()workspace:FindPartsInRegion3WithWhiteList(region,{1})end,
 function()workspace:IsRegion3Empty(region,nil,2)end,
 function()workspace:FindPartsInRegion3(region,nil,2,3)end,
 function()workspace.FindPartsInRegion3(p,region)end,
 function()workspace:IsRegion3Empty()end,
} do assert(not pcall(bad))end
for _,limit in {-1,.5,-math.huge,0/0,"2",true,2147483648} do
 assert(not pcall(function()workspace:FindPartsInRegion3(region,nil,limit)end))
end
assert(workspace:FindPartsInRegion3(region)[1]==p)
''')

    def test_unknown_bounds_error_and_terrain_omission(self):
        mesh=picking.node('MeshPart');mesh['properties'].pop('Size')
        self.check('''
local ok,err=pcall(function()workspace:IsRegion3Empty(region)end)
assert(not ok and string.find(err,"Size is unavailable"))
assert(workspace:IsRegion3Empty(region,workspace.PickTest))
''',overlap.snapshot([mesh]))
        self.check('assert(workspace:IsRegion3Empty(region) and #workspace:FindPartsInRegion3(region)==0)',
                   overlap.snapshot([picking.node('Terrain')]))


class PersistentRegionTests(unittest.TestCase):
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

    def test_received_pose_and_query_flags_change_next_result(self):
        self.start(overlap.snapshot([picking.node('MeshPart')]))
        self.execute('region=Region3.new(Vector3.new(-1,-1,-1),Vector3.new(1,1,1))')
        source='return #workspace:FindPartsInRegion3(region),workspace:IsRegion3Empty(region)'
        self.assertEqual(self.execute(source)['values'],[0,True])
        self.update('pick:test',{'CFrame':{'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}}})
        self.assertEqual(self.execute(source)['values'],[1,False])
        self.update('pick:test',{'CanQuery':False,'CanCollide':False})
        self.assertEqual(self.execute(source)['values'],[0,True])
        self.update('pick:test',{'CanQuery':True})
        self.assertEqual(self.execute(source)['values'],[1,False])

    def test_region_and_results_survive_bindable_function(self):
        self.start()
        self.execute(SETUP+'''
local p=part(Vector3.zero);local query=Instance.new("BindableFunction")
query.OnInvoke=function(received)
 assert(typeof(received)=="Region3" and received==region)
 return workspace:FindPartsInRegion3(received),workspace:IsRegion3Empty(received)
end
local results,empty=query:Invoke(region)
assert(not empty and #results==1 and results[1]==p)
''')


if __name__=='__main__':unittest.main()
