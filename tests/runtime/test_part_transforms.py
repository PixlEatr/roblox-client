"""Part pose consistency in compiled source hosts and persistent world updates."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms


IDENTITY = [1,0,0,0,1,0,0,0,1]
def pose(position=(0,0,0), rotation=IDENTITY):
    return {'$cframe': {'position': list(position), 'rotation': list(rotation)}}


class PartTransformTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def snapshot(self, properties):
        data=copy.deepcopy(transforms.SNAPSHOT)
        data['nodes'].append({'id':'pose-part','class_name':'Part','name':'PosePart',
                              'parent':'scope:workspace','properties':properties})
        return data

    def host(self, properties=None):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(self.snapshot(properties or {'CFrame':pose()}))
        self.addCleanup(h.close)
        self.execute(h,'getgenv().p=workspace.PosePart')
        return h

    def execute(self,h,source):
        result=h.call({'op':'execute','source':transforms.HELPERS+source,'timeout_ms':1000})
        self.assertTrue(result['ok'],result)
        self.assertFalse(result['task_errors'],result)
        return result

    def update(self,h,fields,sequence=1,ok=True):
        result=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':7+sequence,'kind':'set','id':'pose-part','fields':fields}]},'timeout_ms':100})
        self.assertEqual(result['ok'],ok,result)
        if ok:self.assertFalse(result['task_errors'],result)
        return result

    def test_angle_setters_update_pose_in_their_rotation_order(self):
        self.run_script('''
local p=Instance.new("Part");p.Position=Vector3.new(7,8,9)
p.Orientation=Vector3.new(25,37,-18)
frame(p.CFrame,CFrame.new(7,8,9)*CFrame.fromOrientation(math.rad(25),math.rad(37),math.rad(-18)))
p.Rotation=Vector3.new(-12,43,28)
frame(p.CFrame,CFrame.new(7,8,9)*CFrame.Angles(math.rad(-12),math.rad(43),math.rad(28)))
local x,y,z=p.CFrame:ToOrientation();vec(p.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
''')

    def test_cframe_derives_both_angles_and_position_preserves_rotation(self):
        self.run_script('''
local p=Instance.new("Part")
p.CFrame=CFrame.new(4,5,6)*CFrame.Angles(.3,.6,-.2)
vec(p.Rotation,Vector3.new(math.deg(.3),math.deg(.6),math.deg(-.2)))
local x,y,z=p.CFrame:ToOrientation();vec(p.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
local old=p.CFrame.Rotation;p.Position=Vector3.new(-5,2,4)
frame(p.CFrame,CFrame.new(-5,2,4)*old)
''')

    def test_initial_snapshot_uses_cframe_over_stale_components(self):
        self.run_script('''local p=workspace.PosePart
vec(p.Position,Vector3.new(10,20,30));vec(p.Orientation,Vector3.new(0,90,0));vec(p.Rotation,Vector3.new(0,90,0))''',
            self.snapshot({'CFrame':pose((10,20,30),[0,0,1,0,1,0,-1,0,0]),
                           'Position':{'$vector3':[0,0,0]},'Orientation':{'$vector3':[1,2,3]},'Rotation':{'$vector3':[4,5,6]}}))

    def test_unknown_rotation_survives_translation_until_explicit_angle_write(self):
        results=self.run_script('''local p=workspace.PosePart
assert(not pcall(function()return p.Orientation end));assert(not pcall(function()return p.Rotation end))
assert(client.propertyOrigin(p,"Orientation")=="unavailable")
p.Position=Vector3.new(8,9,10)
assert(not pcall(function()return p.Orientation end))
p.Orientation=Vector3.new(0,90,0)
frame(p.CFrame,CFrame.new(8,9,10)*CFrame.Angles(0,math.pi/2,0))''',
            self.snapshot({'CFrame':{'$cframe':{'position':[1,2,3],'rotation_id':17}},'Orientation':{'$vector3':[0,0,0]}}))
        for result in results:
            frames=[m['value']['$cframe'] for m in result['mutations'] if m.get('property')=='CFrame']
            self.assertEqual(frames[0]['rotation_id'],17)
            self.assertNotIn('rotation',frames[0])

    def test_partial_snapshots_require_a_position_before_angles(self):
        self.run_script('''local p=workspace.PosePart
assert(not pcall(function()p.Rotation=Vector3.new(5,6,7)end))
p.Position=Vector3.new(2,3,4)
assert(not pcall(function()return p.CFrame end))
p.Rotation=Vector3.new(0,0,90)
frame(p.CFrame,CFrame.new(2,3,4)*CFrame.Angles(0,0,math.pi/2))''',self.snapshot({}))

    def test_bad_writes_leave_all_components_unchanged(self):
        self.run_script('''local p=Instance.new("Part");p.CFrame=CFrame.new(2,3,4)*CFrame.Angles(.2,.4,.6)
local old=p.CFrame
for _,f in {function()p.Position=Vector3.new(0/0,0,0)end,
 function()p.Orientation=Vector3.new(math.huge,0,0)end,
 function()p.Rotation="bad"end,function()p.CFrame=Vector3.zero end} do
 assert(not pcall(f));frame(p.CFrame,old);vec(p.Position,old.Position)
end''')

    def test_received_pose_overwrites_local_angles_and_emits_coherent_events(self):
        h=self.host()
        self.execute(h,'''p.Orientation=Vector3.new(20,30,40);p.Position=Vector3.new(5,6,7)
getgenv().events={};p.Changed:Connect(function(key)
table.insert(events,key);vec(p.Position,p.CFrame.Position)
local x,y,z=p.CFrame:ToOrientation();vec(p.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
end)''')
        self.update(h,{'CFrame':pose((10,20,30),[0,0,1,0,1,0,-1,0,0]),
                       'Position':{'$vector3':[-1,-1,-1]},'Orientation':{'$vector3':[5,5,5]}})
        self.execute(h,'vec(p.Position,Vector3.new(10,20,30));vec(p.Orientation,Vector3.new(0,90,0));assert(#events==4)')

    def test_replication_pv_priority_and_noop_events(self):
        h=self.host()
        self.execute(h,'getgenv().events={};p.Changed:Connect(function(key)table.insert(events,key)end)')
        pv=pose((4,5,6))
        self.update(h,{'ReplicationPV':pv,'CFrame':pose((99,99,99))})
        self.execute(h,'vec(p.Position,Vector3.new(4,5,6));assert(#events==3);table.clear(events)')
        self.update(h,{'ReplicationPV':pv},2)
        self.execute(h,'assert(#events==0);p.Position=p.Position;assert(#events==0)')

    def test_received_unknown_rotation_hides_stale_angles_and_recovers(self):
        h=self.host()
        self.execute(h,'p.Orientation=Vector3.new(5,10,15)')
        self.update(h,{'CFrame':{'$cframe':{'position':[3,4,5],'rotation_id':17}}})
        self.execute(h,'vec(p.Position,Vector3.new(3,4,5));assert(not pcall(function()return p.Orientation end))')
        self.update(h,{'Rotation':{'$vector3':[0,0,90]}},2)
        self.execute(h,'frame(p.CFrame,CFrame.new(3,4,5)*CFrame.Angles(0,0,math.pi/2))')

    def test_invalid_received_bundle_is_atomic_and_full_pose_can_repair_bad_snapshot(self):
        h=self.host()
        self.update(h,{'Position':{'$vector3':[9,9,9]},'Orientation':{'$vector3':[1,2]}},ok=False)
        self.execute(h,'frame(p.CFrame,CFrame.identity);vec(p.Position,Vector3.zero)')
        broken=self.host({'CFrame':{'$cframe':{'position':[1,2]}}})
        self.update(broken,{'CFrame':pose((7,8,9))})
        self.execute(broken,'frame(p.CFrame,CFrame.new(7,8,9))')

    def test_mutation_budget_failure_keeps_pose_unchanged(self):
        h=self.host()
        self.execute(h,'''getgenv().filler=Instance.new("Folder")
local i=0;while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()p.Orientation=Vector3.new(10,20,30)end))
frame(p.CFrame,CFrame.identity);vec(p.Orientation,Vector3.zero)''')

    def test_derived_provenance_tracks_the_source_pose(self):
        h=self.host({'CFrame':pose((1,2,3)), 'Position':{'$vector3':[99,99,99]}})
        self.execute(h,'''assert(client.propertyOrigin(p,"Position")=="derived_received")
assert(client.propertyOrigin(p,"Orientation")=="derived_received")
p.Orientation=Vector3.new(10,20,30)
assert(client.propertyOrigin(p,"CFrame")=="local")''')
        self.update(h,{'CFrame':pose((4,5,6))})
        self.execute(h,'''assert(client.propertyOrigin(p,"CFrame")=="received")
assert(client.propertyOrigin(p,"Orientation")=="derived_received")''')

    def test_angle_changes_invalidate_model_pivot_offsets(self):
        self.run_script('''local m=Instance.new("Model");m.WorldPivot=CFrame.identity
local p=Instance.new("Part",m);p.CFrame=CFrame.new(3,4,5)
m:PivotTo(CFrame.new(10,20,30));p.Orientation=Vector3.new(12,34,56)
local relative=m:GetPivot():ToObjectSpace(p.CFrame)
local target=CFrame.new(-8,7,6)*CFrame.Angles(.3,.2,.1)
m:PivotTo(target);frame(p.CFrame,target*relative)''')


if __name__=='__main__':unittest.main()
