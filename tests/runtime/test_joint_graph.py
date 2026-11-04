"""Compiled local connection queries and rigid pose editing, without sockets."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms
from test_part_transforms import pose

RIG='''
local function part(name,pos,parent)
 local p=Instance.new("Part",parent or workspace);p.Name=name;p.CFrame=CFrame.new(pos);return p
end
local function join(class,a,b,parent)
 local j=Instance.new(class,parent or a);j.Part0=a;j.Part1=b;return j
end
local a=part("A",Vector3.zero)
local b=part("B",Vector3.new(4,0,0))
local c=part("C",Vector3.new(8,0,0))
local ab=join("Weld",a,b);ab.C0=CFrame.new(4,0,0)
local bc=join("Motor6D",b,c);bc.C0=CFrame.new(4,0,0)
local function contains(list,value)for _,v in list do if v==value then return true end end;return false end
'''


class JointGraphTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def host(self,data=None):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(copy.deepcopy(data or transforms.SNAPSHOT))
        self.addCleanup(h.close);return h

    def execute(self,h,source):
        r=h.call({'op':'execute','source':transforms.HELPERS+source,'timeout_ms':1000})
        self.assertTrue(r['ok'],r);self.assertFalse(r['task_errors'],r);return r

    def test_direct_and_recursive_queries_are_symmetric_and_exclude_self(self):
        self.run_script(RIG+'''
assert(ab.Active and bc.Active)
local direct=a:GetConnectedParts();assert(#direct==1 and direct[1]==b)
local all=a:GetConnectedParts(true);assert(#all==2 and contains(all,b) and contains(all,c))
local reverse=c:GetConnectedParts(true);assert(#reverse==2 and contains(reverse,a) and contains(reverse,b))
assert(#b:GetJoints()==2 and contains(b:GetJoints(),ab) and contains(b:GetJoints(),bc))
local copy=a:GetConnectedParts(true);ab.Enabled=false
assert(#a:GetConnectedParts(true)==0 and #copy==2 and not ab.Active)
assert(#b:GetConnectedParts(true)==1)
assert(not pcall(function()a:GetConnectedParts(1)end))
local query=a.GetJoints;assert(not pcall(query,Instance.new("Folder")))
assert(not pcall(function()ab.Active=true end))''')

    def test_flexible_constraints_are_joints_but_not_rigid_edges(self):
        self.run_script(RIG+'''
ab.Enabled=false;bc.Enabled=false
local aa=Instance.new("Attachment",a);local cc=Instance.new("Attachment",c)
local spring=Instance.new("SpringConstraint",workspace);spring.Attachment0=aa;spring.Attachment1=cc
assert(spring.Active and #a:GetJoints()==1 and a:GetJoints()[1]==spring)
assert(#a:GetConnectedParts(true)==0)
local old=c.CFrame;a.CFrame=CFrame.new(5,6,7);frame(c.CFrame,old)
spring.Enabled=false;assert(not spring.Active and #a:GetJoints()==0)''')

    def test_single_attachment_force_registration_does_not_make_rigid_connection(self):
        self.run_script('''local p=Instance.new("Part",workspace)
local a=Instance.new("Attachment",p);local f=Instance.new("VectorForce",a);f.Attachment0=a
assert(f.Active and #p:GetJoints()==1 and p:GetJoints()[1]==f)
assert(#p:GetConnectedParts(true)==0)
f.Attachment0=nil;assert(not f.Active and #p:GetJoints()==0)''')

    def test_container_scope_parenting_and_destroy_rebuild_connections(self):
        self.run_script(RIG+'''
ab.Parent=nil;assert(not ab.Active and #a:GetJoints()==0)
ab.Parent=a;assert(ab.Active)
local other=Instance.new("WorldModel",workspace)
c.Parent=other;assert(not bc.Active and #b:GetConnectedParts(true)==1)
b.Parent=other;bc.Parent=b;assert(bc.Active and not ab.Active)
assert(#b:GetConnectedParts(true)==1 and b:GetConnectedParts()[1]==c)
bc:Destroy();assert(#b:GetConnectedParts(true)==0 and #c:GetJoints()==0)
''')

    def test_duplicate_cycles_and_anchored_islands_are_deterministic(self):
        self.run_script(RIG+'''
local ca=join("WeldConstraint",c,a)
assert(ab.Active and bc.Active and not ca.Active)
assert(#a:GetConnectedParts(true)==2)
ab.Enabled=false;assert(bc.Active and ca.Active)
a.Anchored=true;b.Anchored=true
assert(bc.Active and not ca.Active and #a:GetConnectedParts(true)==0)
b.Anchored=false;assert(ca.Active and #a:GetConnectedParts(true)==2)
''')

    def test_cframe_moves_rigid_cluster_and_attachment_world_pose(self):
        self.run_script(RIG+'''
local attachment=Instance.new("Attachment",c);attachment.CFrame=CFrame.new(1,2,3)
local oldA,oldB,oldC=a.CFrame,b.CFrame,c.CFrame
local target=CFrame.new(10,20,30)*CFrame.Angles(.3,.5,.7)
a.CFrame=target
frame(a.CFrame,target);frame(b.CFrame,target*oldA:Inverse()*oldB);frame(c.CFrame,target*oldA:Inverse()*oldC)
frame(attachment.WorldCFrame,c.CFrame*attachment.CFrame)
local delta=CFrame.new(-3,8,2)*CFrame.Angles(-.4,.2,.1)
local expectedA,expectedB=delta*a.CFrame,delta*b.CFrame;c.CFrame=delta*c.CFrame
frame(a.CFrame,expectedA);frame(b.CFrame,expectedB)
''')

    def test_position_and_angles_rebase_matching_joint_offsets_without_dragging_parts(self):
        self.run_script(RIG+'''
local beforeB,beforeC=b.CFrame,c.CFrame
a.Position=Vector3.new(2,3,4)
frame(b.CFrame,beforeB);frame(c.CFrame,beforeC)
frame(a.CFrame*ab.C0,b.CFrame*ab.C1)
b.Orientation=Vector3.new(20,30,40)
frame(a.CFrame*ab.C0,b.CFrame*ab.C1)
frame(b.CFrame*bc.C0*bc.Transform,c.CFrame*bc.C1)
frame(c.CFrame,beforeC)
b.Rotation=Vector3.new(-15,25,35)
frame(a.CFrame*ab.C0,b.CFrame*ab.C1)
frame(b.CFrame*bc.C0*bc.Transform,c.CFrame*bc.C1)
''')

    def test_weldconstraint_repositioning_preserves_the_new_offset(self):
        self.run_script(RIG+'''
ab:Destroy();bc:Destroy();local w=join("WeldConstraint",a,b)
a.Position=Vector3.new(2,3,4);vec(b.Position,Vector3.new(4,0,0))
local relative=a.CFrame:ToObjectSpace(b.CFrame)
a.CFrame=CFrame.new(10,20,30)*CFrame.Angles(.2,.4,.6)
frame(b.CFrame,a.CFrame*relative);vec(c.Position,Vector3.new(8,0,0))
''')

    def test_joint_and_attachment_endpoints_enforce_their_classes(self):
        self.run_script('''local w=Instance.new("Weld");local f=Instance.new("Folder")
assert(not pcall(function()w.Part0=f end));assert(w.Part0==nil)
local c=Instance.new("HingeConstraint")
assert(not pcall(function()c.Attachment0=Instance.new("Part")end));assert(c.Attachment0==nil)
local a=Instance.new("Attachment");c.Attachment0=a;assert(c.Attachment0==a)
''')

    def received_rig(self,missing_endpoint=False,unknown_pose=False):
        data=copy.deepcopy(transforms.SNAPSHOT)
        props={'Enabled':True,'Part0':{'$ref':'ja'},'C0':pose((4,0,0)),'C1':pose()}
        if not missing_endpoint:props['Part1']={'$ref':'jb'}
        data['nodes'] += [
            {'id':'ja','class_name':'Part','name':'JA','parent':'scope:workspace','properties':{'CFrame':pose(),'Anchored':False}},
            {'id':'jb','class_name':'Part','name':'JB','parent':'scope:workspace','properties':{'CFrame':{'$cframe':{'position':[4,0,0],'rotation_id':17}} if unknown_pose else pose((4,0,0)),'Anchored':False}},
            {'id':'joint','class_name':'Weld','name':'Joint','parent':'ja','properties':props},
        ]
        return data

    def test_missing_endpoints_keep_connection_queries_unavailable(self):
        self.run_script('''local a=workspace.JA;local j=a.Joint
assert(not pcall(function()return j.Active end));assert(client.propertyOrigin(j,"Active")=="unavailable")
assert(not pcall(function()return a:GetConnectedParts(true)end))
assert(not pcall(function()a.CFrame=CFrame.new(20,0,0)end));frame(a.CFrame,CFrame.identity)
j.Enabled=false;assert(not j.Active and #a:GetConnectedParts(true)==0)
''',self.received_rig(missing_endpoint=True))

    def test_unknown_connected_pose_fails_before_moving_any_part(self):
        results=self.run_script('''local a=workspace.JA
assert(a.Joint.Active);assert(not pcall(function()a.CFrame=CFrame.new(20,30,40)end))
frame(a.CFrame,CFrame.identity);vec(workspace.JB.Position,Vector3.new(4,0,0))''',self.received_rig(unknown_pose=True))
        for r in results:self.assertFalse(any(m.get('id') in {'ja','jb'} for m in r['mutations']))

    def test_received_endpoint_and_enabled_changes_refresh_active_events(self):
        h=self.host(self.received_rig())
        self.execute(h,'''getgenv().a=workspace.JA;getgenv().j=a.Joint;getgenv().changes=0
j:GetPropertyChangedSignal("Active"):Connect(function()changes+=1 end)
assert(j.Active)''')
        def update(fields,seq):
            r=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
                {'sequence':transforms.SNAPSHOT['sequence']+seq,'kind':'set','id':'joint','fields':fields}]},'timeout_ms':100})
            self.assertTrue(r['ok'],r);self.assertFalse(r['task_errors'],r)
        update({'Enabled':False},1)
        self.execute(h,'assert(not j.Active and changes==1 and #a:GetJoints()==0)')
        update({'Enabled':True,'Part1':None},2)
        self.execute(h,'assert(not j.Active and changes==1)')
        update({'Part1':{'$ref':'jb'}},3)
        self.execute(h,'assert(j.Active and changes==2 and #a:GetJoints()==1)')

    def test_callbacks_observe_complete_cluster_and_budget_failure_is_atomic(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().a=a;getgenv().b=b;getgenv().c=c
getgenv().calls=0;a:GetPropertyChangedSignal("CFrame"):Connect(function()
 calls+=1;vec(b.Position,a.Position+Vector3.new(4,0,0));vec(c.Position,a.Position+Vector3.new(8,0,0))end)
a.CFrame=CFrame.new(20,30,40)''')
        self.execute(h,'''assert(calls==1)
local filler=Instance.new("Folder");local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()a.CFrame=CFrame.new(100,100,100)end))
vec(a.Position,Vector3.new(20,30,40));vec(b.Position,Vector3.new(24,30,40));vec(c.Position,Vector3.new(28,30,40))''')

    def test_clone_connection_queries_use_cloned_endpoints(self):
        self.run_script('''local m=Instance.new("Model",workspace)
local a,b=Instance.new("Part",m),Instance.new("Part",m);a.Name="A";b.Name="B"
local j=Instance.new("Weld",a);j.Part0=a;j.Part1=b
local copied=m:Clone();copied.Parent=workspace
assert(copied.A:GetConnectedParts()[1]==copied.B)
copied.A.CFrame=CFrame.new(50,0,0)
vec(copied.B.Position,Vector3.new(50,0,0));vec(a.Position,Vector3.zero);vec(b.Position,Vector3.zero)''')


if __name__=='__main__':unittest.main()
