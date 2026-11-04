"""Bounding queries in both native hosts; reference geometry is not Studio parity."""
import itertools
import math
import random
import unittest

from persistent_runtime import PersistentLuauHost
import test_cframe_transforms as transforms
import test_collision_groups as groups
import test_mouse_picking as picking
import test_world_raycast as raycast


def snapshot(extra=None):
    data = raycast.snapshot(extra)
    root = next(n for n in data['nodes'] if n['id'] == 'scope:root-1')
    root['properties']['CFrame'] = {'$cframe': {
        'position': [1000, 1000, 1000], 'rotation': [1, 0, 0, 0, 1, 0, 0, 0, 1]}}
    return data


SETUP = '''
local function part(position,size,parent,kind)
 local p=Instance.new(kind or "Part",parent or workspace)
 p.Anchored=true;p.Size=size or Vector3.new(2,2,2);p.CFrame=CFrame.new(position)
 return p
end
local function contains(list,p) return table.find(list,p)~=nil end
local function box(cf,size,params) return workspace:GetPartBoundsInBox(cf or CFrame.identity,size or Vector3.new(10,10,10),params) end
local function sphere(center,radius,params) return workspace:GetPartBoundsInRadius(center or Vector3.zero,radius or 5,params) end
'''


def rotation(angles):
    x, y, z = angles
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return ((cy*cz, -cy*sz, sy),
            (cx*sz+sx*sy*cz, cx*cz-sx*sy*sz, -sx*cy),
            (sx*sz-cx*sy*cz, sx*cz+cx*sy*sz, cx*cy))


def reference_intersection(a, b):
    """Clip each box's edges into the other box, including containment.

    Uses vertices and segment/plane clipping, independently of the native SAT.
    Cases stay away from numerical boundaries; this is a geometry oracle only.
    """
    signs = list(itertools.product((-1, 1), repeat=3))

    def edges_hit(first, second):
        p, half, matrix = first
        q, extent, other = second
        vertices = []
        for sign in signs:
            world = [p[i]+sum(matrix[i][j]*half[j]*sign[j] for j in range(3)) for i in range(3)]
            vertices.append([sum(other[j][i]*(world[j]-q[j]) for j in range(3)) for i in range(3)])
        for i, v in enumerate(vertices):
            if all(abs(v[k]) <= extent[k] for k in range(3)):
                return True
            for j in range(i+1, len(vertices)):
                if sum(x != y for x, y in zip(signs[i], signs[j])) != 1:
                    continue
                low, high = 0., 1.
                for axis in range(3):
                    delta = vertices[j][axis]-v[axis]
                    if abs(delta) < 1e-14:
                        if abs(v[axis]) > extent[axis]:
                            low, high = 1., 0.
                            break
                    else:
                        ends = sorted(((-extent[axis]-v[axis])/delta, (extent[axis]-v[axis])/delta))
                        low, high = max(low, ends[0]), min(high, ends[1])
                if low <= high:
                    return True
        return False

    return edges_hit(a, b) or edges_hit(b, a)


class WorldOverlapTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def check(self, source, data=None):
        return self.run_script(SETUP+source, data or snapshot())

    def test_defaults_mutable_identity_and_separate_raycast_type(self):
        self.check('''
local p=OverlapParams.new();local same=p
assert(typeof(p)=="OverlapParams" and p~=OverlapParams.new())
assert(p.MaxParts==0 and p.Tolerance==0 and p.CollisionGroup=="Default")
assert(not p.RespectCanCollide and not p.BruteForceAllSlow)
assert(p.IncludeInstances==nil and p.ExcludeInstances==nil)
assert(p.FilterType==Enum.RaycastFilterType.Exclude and #p.FilterDescendantsInstances==0)
p.MaxParts=3;p.Tolerance=.02;assert(same.MaxParts==3 and same.Tolerance==.02)
assert(not pcall(function()OverlapParams.new=1 end))
assert(not pcall(function()box(nil,nil,RaycastParams.new())end))
assert(not pcall(function()workspace:Raycast(Vector3.zero,Vector3.xAxis,p)end))
''')

    def test_box_and_radius_return_fresh_instance_arrays(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(3,0,0));local outside=part(Vector3.new(20,0,0))
for _,list in {box(),sphere()} do
 assert(#list==2 and contains(list,a) and contains(list,b) and not contains(list,outside))
 list[1]=nil
end
assert(#box()==2 and #sphere()==2)
local old=box();a.Name="OverlapPartRenamed";assert(contains(old,workspace.OverlapPartRenamed))
a:Destroy();assert(#box()==1 and box()[1]==b and #old==2)
''')

    def test_rotated_boxes_do_not_use_world_axis_aligned_bounds(self):
        self.check('''
local p=part(Vector3.zero,Vector3.new(8,.2,.2));p.CFrame=CFrame.Angles(0,0,math.pi/4)
assert(#box(CFrame.new(2,-2,0),Vector3.new(.2,.2,.2))==0)
assert(box(CFrame.new(2,2,0),Vector3.new(.2,.2,.2))[1]==p)
local q=CFrame.Angles(0,0,math.pi/4)
p.CFrame=q*CFrame.new(0,.3,0)
assert(#box(q,Vector3.new(8,.2,.2))==0)
p.CFrame=q*CFrame.new(0,.15,0)
assert(box(q,Vector3.new(8,.2,.2))[1]==p)
''')

    def test_sphere_uses_distance_to_obb_not_center_or_axis_bounds(self):
        self.check('''
local p=part(Vector3.zero,Vector3.new(4,2,2))
assert(#sphere(Vector3.new(2.8,1.8,0),1)==0)
assert(sphere(Vector3.new(2.7,1.7,0),1)[1]==p)
local cf=CFrame.new(20,30,-40)*CFrame.Angles(.3,.7,-.4);p.CFrame=cf
assert(#sphere(cf*Vector3.new(2.8,1.8,0),1)==0)
assert(sphere(cf*Vector3.new(2.7,1.7,0),1)[1]==p)
assert(sphere(cf.Position,0)[1]==p)
assert(#sphere(cf*Vector3.new(0,1.1,0),0)==0)
''')

    def test_bounds_include_empty_primitive_and_mesh_corners(self):
        mesh=picking.node('MeshPart')
        self.check('''
local mesh=workspace.PickTest
assert(box(CFrame.new(1.9,1.9,-10),Vector3.new(.1,.1,.1))[1]==mesh)
mesh.Parent=nil
for _,kind in {"Part","WedgePart","CornerWedgePart"} do
 local p=part(Vector3.zero,Vector3.new(4,4,4),nil,kind)
 if kind=="Part" then p.Shape=Enum.PartType.Ball end
 assert(box(CFrame.new(1.9,1.9,1.9),Vector3.new(.1,.1,.1))[1]==p)
 assert(sphere(Vector3.new(1.9,1.9,1.9),0)[1]==p)
 p:Destroy()
end
''',snapshot([mesh]))

    def test_tolerance_clamps_and_expands_query_bounds(self):
        self.check('''
local p=part(Vector3.new(2.04,0,0));local options=OverlapParams.new()
assert(#box(CFrame.identity,Vector3.new(2,2,2),options)==0 and #sphere(Vector3.zero,1,options)==0)
options.Tolerance=.03
assert(#box(CFrame.identity,Vector3.new(2,2,2),options)==0 and #sphere(Vector3.zero,1,options)==0)
options.Tolerance=1;near(options.Tolerance,.05)
assert(box(CFrame.identity,Vector3.new(2,2,2),options)[1]==p and sphere(Vector3.zero,1,options)[1]==p)
options.Tolerance=-2;assert(options.Tolerance==0 and #sphere(Vector3.zero,1,options)==0)
''')

    def test_touching_and_degenerate_query_policy(self):
        self.check('''
local p=part(Vector3.new(2,0,0))
assert(box(CFrame.identity,Vector3.new(2,2,2))[1]==p)
assert(sphere(Vector3.zero,1)[1]==p)
assert(box(CFrame.new(1,0,0),Vector3.zero)[1]==p)
assert(sphere(Vector3.new(1,0,0),0)[1]==p)
assert(#box(CFrame.new(.999,0,0),Vector3.zero)==0)
''')

    def test_filter_precedence_descendants_and_nil_empty(self):
        self.check('''
local folder=Instance.new("Folder",workspace);local a=part(Vector3.zero,nil,folder)
local b=part(Vector3.new(2,0,0),nil,folder);local c=part(Vector3.new(-2,0,0))
local p=OverlapParams.new();p.IncludeInstances={folder};p.ExcludeInstances={a}
assert(#box(nil,nil,p)==1 and box(nil,nil,p)[1]==b)
p.ExcludeInstances={folder};assert(#sphere(nil,nil,p)==0)
p.ExcludeInstances=nil;p.IncludeInstances={};assert(#box(nil,nil,p)==0)
p.IncludeInstances=nil;assert(#box(nil,nil,p)==3)
p.FilterDescendantsInstances={folder};assert(box(nil,nil,p)[1]==c)
p.FilterType=Enum.RaycastFilterType.Include;assert(#sphere(nil,nil,p)==2)
p:AddToFilter(c);assert(#sphere(nil,nil,p)==3)
p.ExcludeInstances={c};assert(#box(nil,nil,p)==2)
p.IncludeInstances={a};assert(#box(nil,nil,p)==1 and box(nil,nil,p)[1]==a)
''')

    def test_filters_copy_and_failed_edits_are_atomic(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(2,0,0));local p=OverlapParams.new()
local list={a};p.FilterDescendantsInstances=list;list[1]=b
local copy=p.FilterDescendantsInstances;copy[1]=b
assert(box(nil,nil,p)[1]==b)
for _,bad in {{a,12},{oops=a},{[2]=a},"bad"} do
 assert(not pcall(function()p.FilterDescendantsInstances=bad end))
 assert(p.FilterDescendantsInstances[1]==a)
end
assert(not pcall(function()p:AddToFilter({b,12})end))
assert(#p.FilterDescendantsInstances==1)
p:AddToFilter({b});assert(#box(nil,nil,p)==0)
p.FilterDescendantsInstances=table.create(4096,a)
assert(not pcall(function()p:AddToFilter(b)end))
assert(not pcall(function()p.FilterDescendantsInstances=table.create(4097,a)end))
assert(#p.FilterDescendantsInstances==4096)
''')

    def test_maxparts_applies_after_filters_and_zero_is_unlimited(self):
        self.check('''
local excluded=part(Vector3.zero);local parts={}
for i=1,8 do parts[i]=part(Vector3.new(i/10,0,0))end
local p=OverlapParams.new();p.ExcludeInstances={excluded};p.MaxParts=3
for _,result in {box(nil,nil,p),sphere(nil,nil,p)} do
 assert(#result==3)
 for _,item in result do assert(contains(parts,item))end
end
p.MaxParts=0;assert(#box(nil,nil,p)==8)
p.MaxParts=100;assert(#sphere(nil,nil,p)==8)
''')

    def test_query_flags_and_brute_force_still_respect_instance_filters(self):
        self.check('''
local a=part(Vector3.zero);local p=OverlapParams.new()
a.CanCollide=false;assert(#box(nil,nil,p)==1)
p.RespectCanCollide=true;assert(#box(nil,nil,p)==0)
p.RespectCanCollide=false;a.CanQuery=false;assert(#sphere(nil,nil,p)==0)
a.CanCollide=true;assert(#sphere(nil,nil,p)==1)
a.CanCollide=false;p.BruteForceAllSlow=true;assert(#sphere(nil,nil,p)==1)
p.ExcludeInstances={a};assert(#box(nil,nil,p)==0)
''')

    def test_collision_group_masks_and_brute_force(self):
        data=snapshot()
        next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroupData']=groups.PUBLIC
        self.check('''
local a=part(Vector3.zero);a.CollisionGroup="TCAvatar_CollisionGroup"
local b=part(Vector3.new(2,0,0));local p=OverlapParams.new()
assert(#box(nil,nil,p)==1 and box(nil,nil,p)[1]==b)
p.CollisionGroup="TCAvatar_CollisionGroup"
assert(#sphere(nil,nil,p)==1 and sphere(nil,nil,p)[1]==a)
p.BruteForceAllSlow=true;assert(#box(nil,nil,p)==2)
p.ExcludeInstances={a};assert(#box(nil,nil,p)==1 and box(nil,nil,p)[1]==b)
''',data)

    def test_worldmodel_boundaries_reparenting_and_character_inclusion(self):
        self.check('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.CFrame=CFrame.identity;assert(contains(box(),root))
root.CFrame=CFrame.new(1000,1000,1000)
local model=Instance.new("WorldModel",workspace);local nested=Instance.new("WorldModel",model)
local inside=part(Vector3.zero,nil,model);local inner=part(Vector3.zero,nil,nested)
local outer=part(Vector3.zero)
assert(#box()==1 and box()[1]==outer)
assert(model:GetPartBoundsInRadius(Vector3.zero,5)[1]==inside)
assert(#model:GetPartBoundsInBox(CFrame.identity,Vector3.new(10,10,10))==1)
assert(nested:GetPartBoundsInRadius(Vector3.zero,5)[1]==inner)
inside.Parent=workspace;assert(#box()==2 and #model:GetPartBoundsInRadius(Vector3.zero,5)==0)
model.Parent=nil;assert(nested:GetPartBoundsInRadius(Vector3.zero,5)[1]==inner)
model:Destroy();assert(not pcall(function()model:GetPartBoundsInRadius(Vector3.zero,5)end))
''')

    def test_worldmodel_collision_matrix_and_workspace_opt_in(self):
        model={'id':'group:model','name':'GroupModel','class_name':'WorldModel','parent':'scope:workspace',
               'properties':{'CollisionGroupData':groups.encoded([(0,'Default',-1),(1,'TCAvatar_CollisionGroup',-1)])}}
        data=snapshot([model])
        next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroupData']=groups.PUBLIC
        self.check('''
local model=workspace.GroupModel;local p=part(Vector3.zero,nil,model)
p.CollisionGroup="TCAvatar_CollisionGroup"
assert(model:GetPartBoundsInRadius(Vector3.zero,5)[1]==p)
model.UseWorkspaceCollisionGroups=true
assert(#model:GetPartBoundsInRadius(Vector3.zero,5)==0)
model.UseWorkspaceCollisionGroups=false
assert(model:GetPartBoundsInBox(CFrame.identity,Vector3.one)[1]==p)
''',data)

    def test_missing_group_definitions_error_unless_brute_force(self):
        self.check('''
local p=part(Vector3.zero);p.CollisionGroup="MissingGroup"
local ok,err=pcall(function()box()end)
assert(not ok and string.find(err,"matrix is unavailable"))
local options=OverlapParams.new();options.BruteForceAllSlow=true
assert(box(nil,nil,options)[1]==p)
options.ExcludeInstances={p};assert(#sphere(nil,nil,options)==0)
''')

    def test_local_size_pose_changes_and_rigid_transform_invariance(self):
        self.check('''
local cf=CFrame.new(2,3,4)*CFrame.Angles(.3,.8,-.4)
local p=part(Vector3.zero);p.CFrame=cf
local params=OverlapParams.new();params.IncludeInstances={p}
local q=cf*CFrame.new(1.8,0,0);local size=Vector3.new(1,1,1)
assert(#box(q,size,params)==0)
p.Size=Vector3.new(4,2,2);assert(#box(q,size,params)==1)
for _,transform in {CFrame.new(100,-200,50)*CFrame.Angles(.2,-.9,1.7),CFrame.Angles(0,math.pi,0)} do
 p.CFrame=transform*cf
 assert(box(transform*q,size,params)[1]==p)
 assert(#box(transform*(cf*CFrame.new(4,0,0)),size,params)==0)
end
p.Position=Vector3.new(500,0,0);assert(#box(q,size,params)==0)
''')

    def test_missing_bounds_errors_can_be_avoided_with_filters(self):
        mesh=picking.node('MeshPart');mesh['properties'].pop('Size')
        self.check('''
local ok,err=pcall(function()box()end);assert(not ok and string.find(err,"Size is unavailable"))
local p=OverlapParams.new();p.ExcludeInstances={workspace.PickTest}
assert(#box(nil,nil,p)==0 and #sphere(nil,nil,p)==0)
''',snapshot([mesh]))

    def test_terrain_is_not_returned_as_one_enormous_bounding_part(self):
        self.check('assert(#box()==0 and #sphere()==0)',snapshot([picking.node('Terrain')]))

    def test_invalid_parameters_leave_previous_settings_and_queries_usable(self):
        self.check('''
local a=part(Vector3.zero);local p=OverlapParams.new();p.MaxParts=2
for _,bad in {-1,.5,math.huge,0/0,"2"} do
 assert(not pcall(function()p.MaxParts=bad end));assert(p.MaxParts==2)
end
for _,bad in {
 function()p.Tolerance=math.huge end,function()p.Tolerance=".02"end,
 function()p.RespectCanCollide=1 end,function()p.FilterType=Enum.NormalId.Top end,
 function()p.CollisionGroup={}end,function()p.IgnoreWater=true end,
 function()OverlapParams.new(1)end,function()p:NoSuchMethod()end,
 function()workspace.GetPartBoundsInBox(a,CFrame.identity,Vector3.one)end,
 function()workspace:GetPartBoundsInBox(Vector3.zero,Vector3.one)end,
 function()box(CFrame.identity,Vector3.new(-1,1,1))end,
 function()box(CFrame.identity,Vector3.new(math.huge,1,1))end,
 function()box(CFrame.new(math.huge,0,0),Vector3.one)end,
 function()box(CFrame.new(0,0,0,2,0,0,0,1,0,0,0,1),Vector3.one)end,
 function()sphere(Vector3.new(0/0,0,0),1)end,function()sphere(Vector3.zero,-1)end,
 function()sphere(Vector3.zero,math.huge)end,function()sphere(Vector3.zero,"1")end,
 function()workspace:GetPartBoundsInRadius(Vector3.zero,1,nil,1)end,
 function()workspace:GetPartBoundsInRadius(Vector3.zero)end,
 } do assert(not pcall(bad))end
assert(#box(nil,nil,p)==1 and #sphere(nil,nil,p)==1)
''')

    def test_boxes_match_independent_edge_clipping_reference(self):
        rng=random.Random(15521)
        cases=[]
        for _ in range(64):
            def shape():
                return (tuple(rng.uniform(-3,3) for _ in range(3)),
                        tuple(rng.uniform(.15,2.5) for _ in range(3)),
                        rotation(tuple(rng.uniform(-math.pi,math.pi) for _ in range(3))))
            a,b=shape(),shape();cases.append((a,b,reference_intersection(a,b)))
        self.assertTrue(any(hit for _,_,hit in cases))
        self.assertTrue(any(not hit for _,_,hit in cases))
        def vec(values):return 'Vector3.new('+','.join(format(x,'.12g') for x in values)+')'
        def cf(shape):return 'CFrame.new('+','.join(format(x,'.12g') for x in (*shape[0],*sum(shape[2],())))+')'
        source=['local p=part(Vector3.zero);local params=OverlapParams.new();params.IncludeInstances={p}']
        for i,(a,b,hit) in enumerate(cases):
            source += [f'p.CFrame={cf(b)};p.Size={vec([2*x for x in b[1]])}',
                       f'assert((#box({cf(a)},{vec([2*x for x in a[1]])},params)>0)=={str(hit).lower()},"geometry case {i}")']
        self.check('\n'.join(source))


class PersistentOverlapTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute

    def start(self,data=None):
        data=data or snapshot()
        self.sequence=data.get('sequence',0)+100
        self.host=PersistentLuauHost(data);self.addCleanup(self.host.close)

    def update(self,node,fields):
        self.sequence+=1
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':self.sequence,'kind':'set','id':node,'fields':fields}]}})
        self.assertTrue(result['ok'],result)

    def test_received_pose_size_and_parent_changes_refresh_results(self):
        self.start(snapshot([picking.node('MeshPart')]))
        self.assertEqual(self.execute('return #workspace:GetPartBoundsInRadius(Vector3.zero,1)')['values'],[0])
        self.update('pick:test',{'CFrame':{'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}}})
        self.assertEqual(self.execute('return #workspace:GetPartBoundsInRadius(Vector3.zero,1)')['values'],[1])
        self.update('pick:test',{'Size':{'$vector3':[.2,.2,.2]}})
        self.assertEqual(self.execute('return #workspace:GetPartBoundsInRadius(Vector3.new(1,0,0),.5)')['values'],[0])
        self.update('pick:test',{'Parent':{'$ref':'scope:character-1'}})
        self.assertEqual(self.execute('local p=OverlapParams.new();p.ExcludeInstances={game:GetService("Players").LocalPlayer.Character};return #workspace:GetPartBoundsInRadius(Vector3.zero,5,p)')['values'],[0])

    def test_received_collision_matrix_changes_next_query(self):
        data=snapshot([picking.node('Part')])
        node=next(n for n in data['nodes'] if n['id']=='pick:test')
        node['properties']['CollisionGroup']='Ghost'
        ws=next(n for n in data['nodes'] if n['id']=='scope:workspace')
        ws['properties']['CollisionGroupData']=groups.encoded([(0,'Default',-1),(1,'Ghost',-1)])
        self.start(data)
        source='return #workspace:GetPartBoundsInRadius(Vector3.new(0,0,-10),1)'
        self.assertEqual(self.execute(source)['values'],[1])
        self.update('scope:workspace',{'CollisionGroupData':groups.encoded([(0,'Default',-3),(1,'Ghost',-2)])})
        self.assertEqual(self.execute(source)['values'],[0])

    def test_params_and_result_instances_survive_bindable_delivery(self):
        self.start()
        self.execute(SETUP+'''
local a=part(Vector3.zero);local params=OverlapParams.new();local event=Instance.new("BindableEvent")
delivered=false
event.Event:Connect(function(received,results)
 assert(typeof(received)=="OverlapParams" and received==params and results[1]==a)
 received.ExcludeInstances={a};assert(#box(nil,nil,params)==0);delivered=true
end)
event:Fire(params,box(nil,nil,params))
local f=Instance.new("BindableFunction")
f.OnInvoke=function(p)assert(p==params);return p end
assert(f:Invoke(params)==params)
''')
        self.assertEqual(self.execute('return delivered')['values'],[True])


if __name__=='__main__':unittest.main()
