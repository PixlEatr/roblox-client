"""Received collision masks exercised through real Luau APIs and spatial queries."""
import base64
import json
from pathlib import Path
import struct
import unittest

from persistent_runtime import PersistentLuauHost
import test_cframe_transforms as transforms
import test_mouse_picking as picking
import test_mouse_projection as projection
import test_world_raycast as raycast

REFERENCE=json.loads((Path(__file__).resolve().parents[1]/'fixtures/references/collision-groups-public.json').read_text())
PUBLIC={'$bytes_hex':base64.b64decode(REFERENCE['base64']).hex()}


def encoded(rows):
    data=bytearray([1,len(rows)])
    for id,name,mask in rows:
        name=name.encode('utf-8') if isinstance(name,str) else name
        data+=bytes([id,4])+struct.pack('<I',mask & 0xffffffff)+bytes([len(name)])+name
    return {'$bytes_hex':data.hex()}


def snapshot(value=PUBLIC, extra=None):
    data=raycast.snapshot(extra)
    workspace=next(n for n in data['nodes'] if n['id']=='scope:workspace')
    if value is not None: workspace['properties']['CollisionGroupData']=value
    return data


class CollisionGroupTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(raycast.SETUP+source,data or snapshot())

    def test_public_saved_place_reads_through_service_and_worldroot(self):
        self.check('''
local ps=game:GetService("PhysicsService")
assert(ps==game:GetService("PhysicsService"))
local expected={{"Default",-3},{"TCAvatar_CollisionGroup",-6},{"StudioSelectable",-3}}
for _,owner in {ps,workspace} do
 assert(owner:GetMaxCollisionGroups()==32)
 local rows=owner:GetRegisteredCollisionGroups();assert(#rows==3)
 for i,row in rows do
  assert(row.name==expected[i][1] and row.mask==expected[i][2] and row.id==nil)
  assert(owner:IsCollisionGroupRegistered(row.name))
 end
 rows[1].name="corrupted";rows[2]=nil
 assert(owner:GetRegisteredCollisionGroups()[1].name=="Default")
 assert(#owner:GetRegisteredCollisionGroups()==3)
 assert(not owner:IsCollisionGroupRegistered("absent"))
 assert(owner:CollisionGroupsAreCollidable("absent","TCAvatar_CollisionGroup"))
 assert(not owner:CollisionGroupsAreCollidable("Default","TCAvatar_CollisionGroup"))
 assert(owner:CollisionGroupsAreCollidable("Default","StudioSelectable"))
end
local rows=ps:GetCollisionGroups()
for i,row in rows do
 assert(row.id==i-1 and ps:GetCollisionGroupId(row.name)==row.id)
 assert(ps:GetCollisionGroupName(row.id)==row.name)
end
assert(ps:GetCollisionGroupName(31)=="")
assert(not pcall(function()ps:GetCollisionGroupId("absent")end))
for _,id in {-1,32,0.5,math.huge,0/0,"1"} do
 assert(not pcall(function()ps:GetCollisionGroupName(id)end))
end
''')

    def test_all_32_mask_bits_and_signed_enumeration(self):
        rows=[(i,'Default' if i==0 else 'Group'+str(i),1<<i) for i in range(32)]
        self.check('''
local groups=workspace:GetRegisteredCollisionGroups();assert(#groups==32)
for i,a in groups do
 for j,b in groups do
  assert(workspace:CollisionGroupsAreCollidable(a.name,b.name)==(i==j))
 end
end
assert(groups[32].mask==-2147483648)
local p=part(-10);p.CollisionGroup="Group31"
local params=RaycastParams.new();params.CollisionGroup="Group31"
assert(workspace:Raycast(o,d,params).Instance==p)
params.CollisionGroup="Default";assert(workspace:Raycast(o,d,params)==nil)
''',snapshot(encoded(rows)))

    def test_raycast_masks_are_symmetric_and_respect_group_changes(self):
        self.check('''
local nearPart=part(-10);nearPart.CollisionGroup="TCAvatar_CollisionGroup"
local far=part(-20);far.CollisionGroup="StudioSelectable"
local params=RaycastParams.new()
assert(workspace:Raycast(o,d,params).Instance==far)
params.CollisionGroup="TCAvatar_CollisionGroup"
assert(workspace:Raycast(o,d,params).Instance==nearPart)
params.CollisionGroup="Default";nearPart.CollisionGroup="Default"
assert(workspace:Raycast(o,d,params).Instance==nearPart)
nearPart.CollisionGroup="TCAvatar_CollisionGroup"
params.BruteForceAllSlow=true
assert(workspace:Raycast(o,d,params).Instance==nearPart)
params.ExcludeInstances={nearPart};assert(workspace:Raycast(o,d,params).Instance==far)
''')

    def test_asymmetric_data_requires_both_mask_directions(self):
        self.check('''
assert(not workspace:CollisionGroupsAreCollidable("Default","Other"))
assert(not workspace:CollisionGroupsAreCollidable("Other","Default"))
local p=part(-10);p.CollisionGroup="Other"
assert(workspace:Raycast(o,d)==nil)
''',snapshot(encoded([(0,'Default',-1),(1,'Other',-2)])))

    def test_unregistered_query_group_uses_default_mask_policy(self):
        self.check('''
local p=part(-10);p.CollisionGroup="missing"
local params=RaycastParams.new();params.CollisionGroup="missingToo"
assert(workspace:Raycast(o,d,params).Instance==p)
params.CollisionGroup="TCAvatar_CollisionGroup"
assert(workspace:Raycast(o,d,params)==nil)
assert(workspace:CollisionGroupsAreCollidable("missing","TCAvatar_CollisionGroup"))
''')

    def test_legacy_part_id_tracks_name_and_registered_id_writes(self):
        self.check('''
local p=part(-10);assert(p.CollisionGroupId==0)
p.CollisionGroup="StudioSelectable";assert(p.CollisionGroupId==2)
p.CollisionGroupId=1;assert(p.CollisionGroup=="TCAvatar_CollisionGroup")
assert(p.CollisionGroupId==1 and workspace:Raycast(o,d)==nil)
p.CollisionGroup="unregistered";assert(p.CollisionGroupId==-1)
p.CollisionGroupId=0;assert(p.CollisionGroup=="Default")
assert(workspace:Raycast(o,d).Instance==p)
for _,name in {"",string.rep("a",101),"a\\0b",string.char(255)} do
 assert(not pcall(function()p.CollisionGroup=name end))
 assert(p.CollisionGroup=="Default" and p.CollisionGroupId==0)
end
for _,id in {-1,32,1.5,0/0,math.huge,31,"1"} do
 assert(not pcall(function()p.CollisionGroupId=id end))
 assert(p.CollisionGroup=="Default")
end
''')

    def test_can_collide_with_uses_flags_and_current_root(self):
        self.check('''
local a=part(-10);local b=part(-20)
assert(a:CanCollideWith(b) and b:CanCollideWith(a))
b.CollisionGroup="TCAvatar_CollisionGroup";assert(not a:CanCollideWith(b))
a.CollisionGroup=b.CollisionGroup;assert(a:CanCollideWith(b))
a.CanCollide=false;assert(not a:CanCollideWith(b));a.CanCollide=true
b.CanCollide=false;assert(not a:CanCollideWith(b));b.CanCollide=true
b.Parent=Instance.new("WorldModel",workspace)
assert(not pcall(function()a:CanCollideWith(b)end))
b.Parent=workspace;assert(a:CanCollideWith(b))
assert(not pcall(function()a:CanCollideWith(workspace)end))
b:Destroy();assert(not pcall(function()a:CanCollideWith(b)end))
''')

    def test_legacy_membership_setter_and_readonly_definitions(self):
        results=self.check('''
local ps=game:GetService("PhysicsService");local p=part(-10)
ps:SetPartCollisionGroup(p,"TCAvatar_CollisionGroup")
assert(p.CollisionGroup=="TCAvatar_CollisionGroup")
assert(ps:CollisionGroupContainsPart("TCAvatar_CollisionGroup",p))
assert(not ps:CollisionGroupContainsPart("Default",p))
assert(not pcall(function()ps:SetPartCollisionGroup(p,"absent")end))
assert(p.CollisionGroup=="TCAvatar_CollisionGroup")
assert(not pcall(function()ps:SetPartCollisionGroup(workspace,"Default")end))
for _,owner in {ps,workspace,Instance.new("WorldModel")} do
 for _,method in {"RegisterCollisionGroup","UnregisterCollisionGroup","RenameCollisionGroup","CollisionGroupSetCollidable"} do
  local ok,err=pcall(function()owner[method](owner,"Default","Other",false)end)
  assert(not ok and string.find(err,"server"))
 end
 for _,key in {"CollisionGroups","CollisionGroupData"} do
  assert(not pcall(function()owner[key]=""end))
 end
end
assert(#workspace:GetRegisteredCollisionGroups()==3)
''')
        for result in results:
            # The setter must use the usual mutation path, not just alter a lookup cache.
            self.assertIn('TCAvatar_CollisionGroup',json.dumps(result.get('mutations',result)))

    def test_worldmodels_use_independent_matrices_and_workspace_opt_in(self):
        model={'id':'group:model','name':'GroupModel','class_name':'WorldModel','parent':'scope:workspace',
               'properties':{'CollisionGroupData':encoded([(0,'Default',-1),(1,'TCAvatar_CollisionGroup',-1)])}}
        self.check('''
local model=workspace.GroupModel;local p=part(-10,model)
p.CollisionGroup="TCAvatar_CollisionGroup"
assert(model:Raycast(o,d).Instance==p)
assert(workspace:Raycast(o,d)==nil)
model.UseWorkspaceCollisionGroups=true;assert(model:Raycast(o,d)==nil)
model.UseWorkspaceCollisionGroups=false;assert(model:Raycast(o,d).Instance==p)
local independent=Instance.new("WorldModel",workspace)
assert(#independent:GetRegisteredCollisionGroups()==1)
assert(not independent:IsCollisionGroupRegistered("TCAvatar_CollisionGroup"))
p.Parent=independent;assert(independent:Raycast(o,d).Instance==p)
p.Parent=workspace;assert(workspace:Raycast(o,d)==nil)
''',snapshot(extra=[model]))

    def test_incomplete_received_state_is_not_claimed_complete(self):
        self.check('''
assert(workspace:GetMaxCollisionGroups()==32)
assert(workspace:IsCollisionGroupRegistered("Default"))
assert(workspace:CollisionGroupsAreCollidable("Default","Default"))
local p=part(-10);assert(workspace:Raycast(o,d).Instance==p)
assert(not pcall(function()workspace:GetRegisteredCollisionGroups()end))
assert(not pcall(function()workspace:IsCollisionGroupRegistered("Other")end))
p.CollisionGroup="Other"
local ok,err=pcall(function()workspace:Raycast(o,d)end)
assert(not ok and string.find(err,"matrix is unavailable"))
''',snapshot(None))

    def test_empty_binary_definition_is_explicit_default_only(self):
        for value in ('',{'$bytes_hex':''}):
            with self.subTest(value=value):
                self.check('''
local groups=workspace:GetRegisteredCollisionGroups()
assert(#groups==1 and groups[1].name=="Default" and groups[1].mask==-1)
''',snapshot(value))

    def test_utf8_binary_with_zero_mask_blocks_default_rays(self):
        value=bytes.fromhex('01010004000000000744656661756c74').decode('utf-8')
        self.check('''
assert(workspace:GetRegisteredCollisionGroups()[1].mask==0)
assert(not workspace:CollisionGroupsAreCollidable("Default","Default"))
local a=part(-10);local b=part(-20)
assert(not a:CanCollideWith(b))
assert(workspace:Raycast(o,d)==nil)
''',snapshot(value))

    def test_legacy_text_from_world_or_physics_service(self):
        for service in (False,True):
            data=snapshot(None)
            owner=next(n for n in data['nodes'] if n['id']=='scope:workspace')
            if service:
                owner={'id':'group:physics','class_name':'PhysicsService','name':'PhysicsService','parent':None,'properties':{}}
                data['nodes'].append(owner);data['services']['PhysicsService']=owner['id']
            owner['properties']['CollisionGroups']='Default^0^-3\\Other^1^4294967294'
            with self.subTest(service=service):
                self.check('''
assert(#workspace:GetRegisteredCollisionGroups()==2)
assert(not workspace:CollisionGroupsAreCollidable("Default","Other"))
assert(game:GetService("PhysicsService"):GetCollisionGroupId("Other")==1)
''',data)

    def test_malformed_binary_never_falls_back_to_permissive_legacy_data(self):
        valid=bytes.fromhex(PUBLIC['$bytes_hex'])
        bad=[{'$bytes_hex':'x0'},{'$bytes_hex':'1'},123,{}, {'$bytes_hex':'','extra':True},
             {'$bytes_hex':(b'\x02'+valid[1:]).hex()}, {'$bytes_hex':'0100'}, {'$bytes_hex':'0121'},
             {'$bytes_hex':(valid[:3]+b'\x03'+valid[4:]).hex()},
             {'$bytes_hex':valid[:-1].hex()}, {'$bytes_hex':(valid+b'\x00').hex()},
             encoded([(0,'Default',-1),(0,'Other',-1)]),
             encoded([(0,'Default',-1),(1,'Default',-1)]),
             encoded([(1,'Other',-1)]),encoded([(0,'Other',-1)]),
             encoded([(0,'Default',-1),(32,'TooFar',-1)]),
             encoded([(0,'Default',-1),(1,b'\xff',-1)]),
             encoded([(0,'Default',-1),(1,'',-1)]),
             encoded([(0,'Default',-1),(1,'a\0b',-1)]),
             encoded([(0,'Default',-1),(1,'x'*101,-1)])]
        for i,value in enumerate(bad):
            with self.subTest(case=i):
                data=snapshot(value)
                next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroups']='Default^0^-1'
                self.check('''
assert(not pcall(function()workspace:GetRegisteredCollisionGroups()end))
local p=part(-10)
assert(not pcall(function()workspace:Raycast(o,d)end))
''',data)

    def test_malformed_legacy_records_are_rejected(self):
        for value in ('Default^0^-1\\','Default^0^-1^oops','Default^0^4294967296',
                      'Default^0^-2147483649','Default^0^ -1','Default^0^-1\\Other^32^-1'):
            with self.subTest(value=value):
                data=snapshot(None)
                next(n for n in data['nodes'] if n['id']=='scope:workspace')['properties']['CollisionGroups']=value
                self.check('assert(not pcall(function()workspace:GetRegisteredCollisionGroups()end))',data)


class PersistentCollisionGroupTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute
    update=projection.MouseProjectionTests.update
    move=projection.MouseProjectionTests.move

    def start(self):
        data=snapshot();data['input_capabilities']={'MouseEnabled':True}
        self.host=PersistentLuauHost(data);self.addCleanup(self.host.close)
        self.execute(raycast.SETUP+'''
mouse=game:GetService("Players").LocalPlayer:GetMouse()
workspace.CurrentCamera.CFrame=CFrame.identity;workspace.CurrentCamera.FieldOfView=90
nearPart=part(-10);nearPart.CollisionGroup="TCAvatar_CollisionGroup"
farPart=part(-20)
''')
        self.move(400,300)

    def test_received_matrix_delta_updates_mouse_ray_and_api_without_cursor_move(self):
        self.start()
        self.execute('assert(mouse.Target==farPart)')
        changed=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':10,'kind':'set','id':'scope:workspace','fields':{
                'CollisionGroupData':encoded([(0,'Default',-1),(1,'TCAvatar_CollisionGroup',-1)])}}]}})
        self.assertTrue(changed['ok'],changed)
        self.execute('''
assert(mouse.Target==nearPart)
assert(workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100)).Instance==nearPart)
assert(workspace:CollisionGroupsAreCollidable("Default","TCAvatar_CollisionGroup"))
assert(#workspace:GetRegisteredCollisionGroups()==2)
''')

    def test_set_part_collision_group_fires_standard_property_signal(self):
        self.start()
        self.execute('''
seen=0
nearPart:GetPropertyChangedSignal("CollisionGroup"):Connect(function()seen+=1 end)
game:GetService("PhysicsService"):SetPartCollisionGroup(nearPart,"Default")
''')
        self.assertEqual(self.execute('return seen,mouse.Target==nearPart')['values'],[1,True])

    def test_malformed_matrix_recovery(self):
        self.start()
        self.execute('assert(mouse.Target==farPart)')
        def update(sequence, node, fields):
            result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
                {'sequence':sequence,'kind':'set','id':node,'fields':fields}]}})
            self.assertTrue(result['ok'],result)
        # Corrupt definitions must not replace the last failure with an
        # invented collidable result; a later good update must recover.
        update(10,'scope:workspace',{'CollisionGroupData':{'$bytes_hex':'010100'}})
        self.execute('assert(not pcall(function()return mouse.Target end))')
        update(11,'scope:workspace',{'CollisionGroupData':PUBLIC})
        self.execute('assert(mouse.Target==farPart)')

    def test_received_private_group_property_updates_name_id_and_queries(self):
        n=picking.node('Part')
        n['properties'].update(Shape=1,Material=256,CollisionGroupReplicate='\x01TCAvatar_CollisionGroup')
        self.host=PersistentLuauHost(snapshot(extra=[n]));self.addCleanup(self.host.close)
        self.execute('''
p=workspace.PickTest
assert(p.CollisionGroup=="TCAvatar_CollisionGroup" and p.CollisionGroupId==1)
assert(workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100))==nil)
''')
        changed=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':10,'kind':'set','id':'pick:test','fields':{
                'CollisionGroupReplicate':{'$bytes_hex':b'\x02StudioSelectable'.hex()}}}]}})
        self.assertTrue(changed['ok'],changed)
        self.execute('''
assert(p.CollisionGroup=="StudioSelectable" and p.CollisionGroupId==2)
assert(workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100)).Instance==p)
''')


if __name__=='__main__':unittest.main()
