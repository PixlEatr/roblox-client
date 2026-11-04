"""BulkMoveTo transactions, event modes, and integration with current-world state."""
import unittest

from persistent_runtime import PersistentLuauHost
import test_cframe_transforms as transforms
import test_joint_graph as joints
import test_mouse_picking as picking
import test_world_overlap as overlap


SETUP=overlap.SETUP


def many_parts(count):
    nodes=[]
    for i in range(1,count+1):
        node=picking.node('Part');node.update(id='bulk:'+str(i),name='Bulk'+str(i))
        node['properties'].update(CFrame={'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}},Shape=1,Material=256)
        nodes.append(node)
    return overlap.snapshot(nodes)


class BulkMoveTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(SETUP+source,data or overlap.snapshot())

    def test_enum_metadata_and_empty_batch(self):
        self.check('''
local mode=Enum.BulkMoveMode
assert(typeof(mode)=="Enum" and typeof(mode.FireAllEvents)=="EnumItem")
assert(mode.FireAllEvents.Value==0 and mode.FireCFrameChanged.Value==1)
assert(mode.FireAllEvents.EnumType==mode and tostring(mode.FireCFrameChanged)=="Enum.BulkMoveMode.FireCFrameChanged")
assert(#mode:GetEnumItems()==2)
assert(mode:FromName("FireAllEvents")==mode.FireAllEvents)
assert(mode:FromValue(1)==mode.FireCFrameChanged)
assert(not pcall(function()mode.FireAllEvents.Value=1 end))
assert(select('#',workspace:BulkMoveTo({},{}))==0)
assert(select('#',workspace:BulkMoveTo({},{},mode.FireCFrameChanged))==0)
''')

    def test_moves_arrays_and_keeps_all_transform_fields_coherent(self):
        results=self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(3,0,0))
local targets={CFrame.new(10,20,30)*CFrame.Angles(.2,.3,.4),CFrame.new(-4,5,6)*CFrame.Angles(-.4,.3,.2)}
local parts={a,b};workspace:BulkMoveTo(parts,targets)
for i,p in parts do
 frame(p.CFrame,targets[i]);vec(p.Position,targets[i].Position)
 local x,y,z=targets[i]:ToOrientation();vec(p.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
 x,y,z=targets[i]:ToEulerAnglesXYZ();vec(p.Rotation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
end
assert(parts[1]==a and targets[1].Position==Vector3.new(10,20,30))
''')
        for result in results:
            moved=[m for m in result['mutations'] if m.get('property')=='Position' and m.get('value')=={'$vector3':[10,20,30]}]
            self.assertEqual(len(moved),1)

    def test_explicit_single_part_repairs_missing_received_pose(self):
        node=picking.node('Part');node['properties'].pop('CFrame')
        self.check('''
local p=workspace.PickTest;workspace:BulkMoveTo({p},{CFrame.new(8,9,10)})
vec(p.Position,Vector3.new(8,9,10));frame(p.CFrame,CFrame.new(8,9,10))
''',overlap.snapshot([node]))

    def test_duplicates_and_conflicting_connected_targets_use_last_request(self):
        self.check('''
local p=part(Vector3.zero)
workspace:BulkMoveTo({p,p},{CFrame.new(1,0,0),CFrame.new(2,0,0)})
vec(p.Position,Vector3.new(2,0,0))
''')
        self.run_script(joints.RIG+'''
workspace:BulkMoveTo({a,b},{CFrame.new(10,0,0),CFrame.new(30,0,0)})
vec(a.Position,Vector3.new(26,0,0));vec(b.Position,Vector3.new(30,0,0));vec(c.Position,Vector3.new(34,0,0))
''',overlap.snapshot())

    def test_welded_parts_and_attachment_follow_one_rigid_delta(self):
        self.run_script(joints.RIG+'''
local at=Instance.new("Attachment",c);at.CFrame=CFrame.new(1,2,3)
local target=CFrame.new(10,20,30)*CFrame.Angles(.3,.8,-.2)
workspace:BulkMoveTo({a},{target},Enum.BulkMoveMode.FireCFrameChanged)
frame(a.CFrame,target);frame(b.CFrame,target*CFrame.new(4,0,0));frame(c.CFrame,target*CFrame.new(8,0,0))
frame(at.WorldCFrame,c.CFrame*at.CFrame)
assert(ab.Part0==a and ab.Part1==b and bc.Part0==b and bc.Part1==c)
''',overlap.snapshot())

    def test_multiple_coherent_targets_do_not_apply_assembly_delta_twice(self):
        self.run_script(joints.RIG+'''
local delta=CFrame.new(10,20,30)*CFrame.Angles(.4,.5,.6)
local targets={delta*a.CFrame,delta*b.CFrame,delta*c.CFrame}
workspace:BulkMoveTo({a,b,c},targets)
frame(a.CFrame,targets[1]);frame(b.CFrame,targets[2]);frame(c.CFrame,targets[3])
''',overlap.snapshot())

    def test_disabled_and_flexible_joints_do_not_move_other_parts(self):
        self.run_script(joints.RIG+'''
ab.Enabled=false;bc.Enabled=false
local aa=Instance.new("Attachment",a);local cc=Instance.new("Attachment",c)
local spring=Instance.new("SpringConstraint",workspace);spring.Attachment0=aa;spring.Attachment1=cc
local beforeB,beforeC=b.CFrame,c.CFrame
workspace:BulkMoveTo({a},{CFrame.new(10,0,0)})
frame(b.CFrame,beforeB);frame(c.CFrame,beforeC)
''',overlap.snapshot())

    def test_detached_parts_and_worldmodel_receiver(self):
        self.check('''
local model=Instance.new("WorldModel");local a=part(Vector3.zero,nil,model)
local b=part(Vector3.zero);b.Parent=nil
model:BulkMoveTo({a,b},{CFrame.new(1,2,3),CFrame.new(4,5,6)})
vec(a.Position,Vector3.new(1,2,3));vec(b.Position,Vector3.new(4,5,6))
assert(a.Parent==model and b.Parent==nil)
model:Destroy();assert(not pcall(function()model:BulkMoveTo({},{})end))
''')

    def test_queries_see_moved_geometry_immediately(self):
        self.check('''
local p=part(Vector3.new(20,0,0));p.Material=Enum.Material.Metal
assert(#box()==0 and workspace:Raycast(Vector3.zero,Vector3.new(0,0,-20))==nil)
workspace:BulkMoveTo({p},{CFrame.new(0,0,-5)})
assert(box(CFrame.new(0,0,-5),Vector3.one)[1]==p)
assert(sphere(Vector3.new(0,0,-5),1)[1]==p)
assert(workspace:Raycast(Vector3.zero,Vector3.new(0,0,-20)).Instance==p)
assert(workspace:FindPartOnRay(Ray.new(Vector3.zero,Vector3.new(0,0,-20)))==p)
''')

    def test_entire_batch_validation_precedes_any_mutation(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(3,0,0));local oldA,oldB=a.CFrame,b.CFrame
local target=CFrame.new(100,100,100);local folder=Instance.new("Folder")
local dead=Instance.new("Part");dead:Destroy()
for _,bad in {
 function()workspace:BulkMoveTo({a,folder},{target,target})end,
 function()workspace:BulkMoveTo({a,dead},{target,target})end,
 function()workspace:BulkMoveTo({a,b},{target,Vector3.zero})end,
 function()workspace:BulkMoveTo({a,b},{target,CFrame.new(math.huge,0,0)})end,
 function()workspace:BulkMoveTo({a,b},{target,CFrame.new(0,0,0,0/0,0,0,0,1,0,0,0,1)})end,
 function()workspace:BulkMoveTo({a,b},{target})end,
 function()workspace:BulkMoveTo({a},{target,target})end,
 function()workspace:BulkMoveTo({[1]=a,[3]=b},{target,target,target})end,
 function()workspace:BulkMoveTo({a},{[1]=target,extra=target})end,
 function()workspace:BulkMoveTo({a},{target},Enum.NormalId.Top)end,
 function()workspace:BulkMoveTo({a},{target},0)end,
 function()workspace:BulkMoveTo({a},{target},"FireAllEvents")end,
 function()workspace:BulkMoveTo(a,{target})end,
 function()workspace:BulkMoveTo({a},target)end,
 function()workspace.BulkMoveTo(a,{a},{target})end,
 function()workspace:BulkMoveTo({a})end,
 function()workspace:BulkMoveTo({a},{target},nil,1)end,
} do assert(not pcall(bad));frame(a.CFrame,oldA);frame(b.CFrame,oldB)end
workspace:BulkMoveTo({a,b},{target,target});frame(a.CFrame,target);frame(b.CFrame,target)
''')

    def test_array_metatables_are_not_executed_and_capacity_is_bounded(self):
        self.check('''
local p=part(Vector3.zero)
local meta={__index=function()error("must not run")end,__len=function()error("must not run")end}
workspace:BulkMoveTo(setmetatable({p},meta),setmetatable({CFrame.new(3,4,5)},meta))
vec(p.Position,Vector3.new(3,4,5))
workspace:BulkMoveTo(table.create(4096,p),table.create(4096,p.CFrame))
assert(not pcall(function()workspace:BulkMoveTo(table.create(4097,p),table.create(4097,p.CFrame))end))
vec(p.Position,Vector3.new(3,4,5))
''')

    def test_output_budget_failure_does_not_move_a_prefix_of_the_batch(self):
        results=self.check('''
local parts,frames={},{}
for i=1,600 do parts[i]=workspace["Bulk"..i];frames[i]=CFrame.new(i,20,30)end
local ok,err=pcall(function()workspace:BulkMoveTo(parts,frames)end)
assert(not ok and string.find(err,"mutation output limit"))
for _,p in parts do frame(p.CFrame,CFrame.identity)end
''',many_parts(600))
        for result in results:
            self.assertFalse(any(m.get('id','').startswith('bulk:') for m in result['mutations']))

    def test_unknown_connected_pose_rejects_even_an_earlier_valid_component(self):
        data=joints.JointGraphTests().received_rig(unknown_pose=True)
        results=self.check('''
local p=part(Vector3.new(1,2,3));local before=p.CFrame
assert(workspace.JA.Joint.Active)
assert(not pcall(function()
 workspace:BulkMoveTo({p,workspace.JA},{CFrame.new(100,0,0),CFrame.new(200,0,0)})
end))
frame(p.CFrame,before);frame(workspace.JA.CFrame,CFrame.identity)
vec(workspace.JB.Position,Vector3.new(4,0,0))
''',data)
        for result in results:
            self.assertFalse(any(m.get('id') in {'ja','jb'} for m in result['mutations']))

    def test_budget_already_used_by_script_preserves_last_successful_pose(self):
        self.check('''
local a=part(Vector3.zero);local b=part(Vector3.new(4,0,0))
workspace:BulkMoveTo({a,b},{CFrame.new(10,20,30),CFrame.new(40,50,60)})
local filler=Instance.new("Folder");local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()
 workspace:BulkMoveTo({a,b},{CFrame.new(100,200,300),CFrame.new(400,500,600)})
end))
vec(a.Position,Vector3.new(10,20,30));vec(b.Position,Vector3.new(40,50,60))
''')

    def test_independent_assemblies_keep_their_respective_rigid_offsets(self):
        self.run_script(joints.RIG+'''
local d=part("D",Vector3.new(30,0,0));local e=part("E",Vector3.new(33,0,0))
local de=join("Weld",d,e);de.C0=CFrame.new(3,0,0)
local first=CFrame.new(10,20,30)*CFrame.Angles(.2,.5,.7)
local second=CFrame.new(-10,40,5)*CFrame.Angles(-.3,.1,.8)
workspace:BulkMoveTo({a,d},{first,second})
frame(a.CFrame,first);frame(b.CFrame,first*CFrame.new(4,0,0));frame(c.CFrame,first*CFrame.new(8,0,0))
frame(d.CFrame,second);frame(e.CFrame,second*CFrame.new(3,0,0))
''',overlap.snapshot())


class PersistentBulkMoveTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute

    def start(self,data=None):
        data=data or overlap.snapshot()
        self.sequence=data.get('sequence',0)+100
        self.host=PersistentLuauHost(data);self.addCleanup(self.host.close)

    def setup_events(self):
        self.start()
        self.execute('''
a=Instance.new("Part",workspace);b=Instance.new("Part",workspace)
events={};massEvents=0;observed=0
local function watch(p)
 p.Changed:Connect(function(key)
  assert(a.Position==Vector3.new(10,20,30) and b.Position==Vector3.new(-4,5,6))
  if key=="AssemblyCenterOfMass" then
   massEvents+=1
  else
   table.insert(events,key)
  end
 end)
 for _,key in {"CFrame","Position","Orientation","Rotation"} do
  p:GetPropertyChangedSignal(key):Connect(function()observed+=1 end)
 end
end
watch(a);watch(b)
targets={CFrame.new(10,20,30)*CFrame.Angles(.2,.3,.4),CFrame.new(-4,5,6)*CFrame.Angles(-.4,.3,.2)}
''')

    def test_default_event_mode_sees_whole_committed_batch(self):
        self.setup_events()
        self.execute('workspace:BulkMoveTo({a,b},targets)')
        self.assertEqual(self.execute('return #events,observed')['values'],[8,8])
        self.assertEqual(self.execute('return massEvents')['values'],[2])
        self.assertEqual(self.execute('local counts={};for _,key in events do counts[key]=(counts[key] or 0)+1 end;return counts.CFrame,counts.Position,counts.Orientation,counts.Rotation')['values'],[2,2,2,2])

    def test_cframe_only_mode_updates_all_values_and_suppresses_derived_events(self):
        self.setup_events()
        self.execute('workspace:BulkMoveTo({a,b},targets,Enum.BulkMoveMode.FireCFrameChanged)')
        self.assertEqual(self.execute('return #events,observed,events[1],events[2]')['values'],[2,2,'CFrame','CFrame'])
        self.assertEqual(self.execute('return massEvents')['values'],[2])
        self.execute('assert(a.Orientation.Magnitude>0 and a.Rotation.Magnitude>0 and b.Orientation.Magnitude>0)')
        self.execute('workspace:BulkMoveTo({a,b},targets,Enum.BulkMoveMode.FireCFrameChanged)')
        self.assertEqual(self.execute('return #events,observed')['values'],[2,2])
        self.assertEqual(self.execute('return massEvents')['values'],[2])

    def test_invalid_batch_emits_no_events(self):
        self.setup_events()
        self.execute('assert(not pcall(function()workspace:BulkMoveTo({a,b},{targets[1],Vector3.zero})end))')
        self.assertEqual(self.execute('return #events,observed,a.Position==Vector3.zero,b.Position==Vector3.zero')['values'],[0,0,True,True])

    def test_attachment_world_signal_observes_batch_even_with_cframe_only_mode(self):
        self.start()
        self.execute('''
local a=Instance.new("Part",workspace);local b=Instance.new("Part",workspace)
local at=Instance.new("Attachment",a);at.Position=Vector3.new(1,2,3)
attachmentChanged=0
at:GetPropertyChangedSignal("WorldPosition"):Connect(function()
 assert(at.WorldPosition==Vector3.new(11,22,33) and b.Position==Vector3.new(40,50,60))
 attachmentChanged+=1
end)
workspace:BulkMoveTo({a,b},{CFrame.new(10,20,30),CFrame.new(40,50,60)},Enum.BulkMoveMode.FireCFrameChanged)
''')
        self.assertEqual(self.execute('return attachmentChanged')['values'],[1])

    def test_new_receipt_supersedes_local_bulk_pose_and_preserves_received_storage(self):
        self.start(many_parts(1))
        self.execute('workspace:BulkMoveTo({workspace.Bulk1},{CFrame.new(10,20,30)})')
        self.assertEqual(self.execute('return workspace.Bulk1.Position')['values'],[{'$vector3':[10,20,30]}])
        self.sequence+=1
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':self.sequence,'kind':'set','id':'bulk:1','fields':{
                'CFrame':{'$cframe':{'position':[2,4,6],'rotation':[1,0,0,0,1,0,0,0,1]}}}}]}})
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.execute('return workspace.Bulk1.Position,client.propertyOrigin(workspace.Bulk1,"CFrame")')['values'],[{'$vector3':[2,4,6]},'received'])


if __name__=='__main__':unittest.main()
