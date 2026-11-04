"""Assembly roots, immediate joint frames, and deferred motor pose batches."""
import copy
import os
import time
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms
from test_part_transforms import pose

RIG='''
local function part(name,x)
 local p=Instance.new("Part",workspace);p.Name=name;p.CFrame=CFrame.new(x,0,0);return p
end
local a,b,c=part("A",0),part("B",4),part("C",8)
a.RootPriority=100
local ab=Instance.new("Motor6D",a);ab.Part0=a;ab.Part1=b;ab.C0=CFrame.new(4,0,0)
local bc=Instance.new("Motor6D",b);bc.Part0=b;bc.Part1=c;bc.C0=CFrame.new(4,0,0)
'''


class JointPoseTests(unittest.TestCase):
    def run_script(self,source,snapshot=None):
        results=transforms.CFrameTransformTests.run_script(self,source,snapshot)
        for r in results:self.assertFalse(r.get('joint_pose_errors'),r)
        return results

    def host(self,snapshot=None):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(copy.deepcopy(snapshot or transforms.SNAPSHOT))
        self.addCleanup(h.close);return h

    def execute(self,h,source,pose_errors=False):
        r=h.call({'op':'execute','source':transforms.HELPERS+source,'timeout_ms':1000})
        self.assertTrue(r['ok'],r);self.assertFalse(r['task_errors'],r)
        job_id=r.get('job_id');deadline=time.monotonic()+3
        while r.get('completed') is False:
            self.assertLess(time.monotonic(),deadline,'persistent test main job did not complete')
            time.sleep(.002)
            update=h.call({'op':'poll','timeout_ms':1000})
            self.assertTrue(update['ok'],update);self.assertFalse(update.get('task_errors'),update)
            for key in ('mutations','movement_intents','joint_pose_errors'):
                r.setdefault(key,[]).extend(update.get(key,[]))
            completed=next((item for item in update.get('completed_jobs',[]) if item.get('job_id')==job_id),None)
            if completed is not None:
                self.assertTrue(completed['ok'],completed)
                r.update(completed)
        self.assertEqual(bool(r.get('joint_pose_errors')),pose_errors,r)
        return r

    def test_root_getter_scope_and_readonly_property(self):
        self.run_script('''local p=Instance.new("Part")
assert(p.AssemblyRootPart==nil and p:GetRootPart()==nil)
p.Parent=workspace;assert(p.AssemblyRootPart==p and p:GetRootPart()==p)
assert(not pcall(function()p.AssemblyRootPart=p end))
local method=p.GetRootPart;assert(not pcall(method,Instance.new("Folder")))
p:Destroy();assert(p.AssemblyRootPart==nil)''')

    def test_root_selection_priority_massless_and_anchoring(self):
        self.run_script(RIG+'''
assert(a:GetRootPart()==a and b.AssemblyRootPart==a and c.AssemblyRootPart==a)
c.RootPriority=127;assert(a.AssemblyRootPart==c)
c.Massless=true;assert(c.AssemblyRootPart==a)
c.Anchored=true;assert(a.AssemblyRootPart==c)
a.Anchored=true;assert(a.AssemblyRootPart==a and c.AssemblyRootPart==c)
''')

    def test_legacy_root_score_and_query_independent_ties(self):
        self.run_script('''local a=Instance.new("Part",workspace);local b=Instance.new("Seat",workspace)
a.Size=Vector3.one;b.Size=Vector3.one
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
a.Name="HumanoidRootPart";assert(a:GetRootPart()==b)
a.Size=Vector3.new(3,3,3);assert(b:GetRootPart()==a)
a.Name="Part";a.Size=Vector3.one;b.RootPriority=-1;assert(a:GetRootPart()==a)
b.RootPriority=0;b.Massless=true;assert(a:GetRootPart()==a)
a.Massless=true;assert(a:GetRootPart()==b)
local c=Instance.new("Part",workspace);local d=Instance.new("Part",workspace)
local x=Instance.new("WeldConstraint",c);x.Part0=c;x.Part1=d
assert(c:GetRootPart()==d:GetRootPart() and c:GetRootPart()==c)''')

    def test_c0_immediately_moves_the_child_branch_and_keeps_root_fixed(self):
        self.run_script(RIG+'''
local root=a.CFrame
ab.C0=CFrame.new(6,2,3)*CFrame.Angles(.2,.3,.4)
frame(a.CFrame,root)
frame(b.CFrame,a.CFrame*ab.C0*ab.Transform*ab.C1:Inverse())
frame(c.CFrame,b.CFrame*bc.C0*bc.Transform*bc.C1:Inverse())
''')

    def test_c1_and_reversed_root_move_the_correct_side(self):
        self.run_script(RIG+'''
c.RootPriority=127;local root=c.CFrame;local oldB=b.CFrame
ab.C1=CFrame.new(1,2,3)*CFrame.Angles(.2,.4,.6)
frame(c.CFrame,root);frame(b.CFrame,oldB)
frame(a.CFrame,b.CFrame*ab.C1*(ab.C0*ab.Transform):Inverse())
bc.C0=CFrame.new(10,0,0)*CFrame.Angles(.1,.3,.2)
frame(c.CFrame,root)
frame(b.CFrame,c.CFrame*bc.C1*(bc.C0*bc.Transform):Inverse())
frame(a.CFrame,b.CFrame*ab.C1*(ab.C0*ab.Transform):Inverse())
''')

    def test_unmodified_weldconstraint_branches_keep_their_relative_pose(self):
        self.run_script(RIG+'''
local accessory=part("Accessory",20);accessory.Massless=true
local w=Instance.new("WeldConstraint",c);w.Part0=c;w.Part1=accessory
local relative=c.CFrame:ToObjectSpace(accessory.CFrame)
ab.C0=CFrame.new(6,3,2)*CFrame.Angles(.5,.2,.4)
frame(accessory.CFrame,c.CFrame*relative)
''')

    def test_inactive_joint_edits_do_not_move_parts(self):
        self.run_script(RIG+'''
ab.Enabled=false;local aa,bb,cc=a.CFrame,b.CFrame,c.CFrame
ab.C0=CFrame.new(100,0,0)
frame(a.CFrame,aa);frame(b.CFrame,bb);frame(c.CFrame,cc)
assert(ab.C0.X==100)
''')

    def test_motor_targets_are_deferred_and_batched_in_parent_order(self):
        self.run_script(RIG+'''
local bb,cc=b.CFrame,c.CFrame
bc.Transform=CFrame.new(0,2,0)*CFrame.Angles(.2,.3,.4)
ab.Transform=CFrame.Angles(.3,.4,.5)
frame(b.CFrame,bb);frame(c.CFrame,cc)
task.wait(.001)
frame(a.CFrame,CFrame.identity)
frame(b.CFrame,a.CFrame*ab.C0*ab.Transform*ab.C1:Inverse())
frame(c.CFrame,b.CFrame*bc.C0*bc.Transform*bc.C1:Inverse())
''')

    def test_immediate_offsets_use_applied_transform_until_the_batch(self):
        self.run_script(RIG+'''
ab.Transform=CFrame.new(0,2,0)
ab.C0=CFrame.new(6,0,0)
vec(b.Position,Vector3.new(6,0,0))
task.wait(.001);vec(b.Position,Vector3.new(6,2,0));vec(c.Position,Vector3.new(10,2,0))
''')

    def test_retargeting_motor_does_not_accumulate_translation_drift(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().a=a;getgenv().b=b;getgenv().c=c;getgenv().ab=ab;getgenv().bc=bc''')
        for index in range(6):
            self.execute(h,f'''ab.Transform=CFrame.new({index},2,3)*CFrame.Angles(.2,.3,.4)
bc.Transform=CFrame.Angles(.4,.2,.1)''')
            self.execute(h,'''frame(a.CFrame,CFrame.identity)
frame(b.CFrame,a.CFrame*ab.C0*ab.Transform*ab.C1:Inverse())
frame(c.CFrame,b.CFrame*bc.C0*bc.Transform*bc.C1:Inverse())''')

    def test_motor_batch_callbacks_see_all_parts_and_coalesce_targets(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().a=a;getgenv().b=b;getgenv().c=c;getgenv().ab=ab;getgenv().bc=bc
getgenv().calls=0;b:GetPropertyChangedSignal("CFrame"):Connect(function()
 calls+=1;frame(c.CFrame,b.CFrame*bc.C0*bc.Transform*bc.C1:Inverse())end)
ab.Transform=CFrame.new(0,1,0);ab.Transform=CFrame.new(0,3,0)
bc.Transform=CFrame.new(0,0,2)''')
        self.execute(h,'assert(calls==1);vec(b.Position,Vector3.new(4,3,0));vec(c.Position,Vector3.new(8,3,2))')

    def partial_snapshot(self,missing_root=False,missing_offset=False):
        data=copy.deepcopy(transforms.SNAPSHOT)
        part={'CFrame':pose(),'Anchored':False,'Massless':False,'RootPriority':100,'Size':{'$vector3':[2,2,2]}}
        other=copy.deepcopy(part);other['CFrame']=pose((4,0,0));other['RootPriority']=0
        if missing_root:other.pop('Massless')
        joint={'Enabled':True,'Part0':{'$ref':'pa'},'Part1':{'$ref':'pb'},'C0':pose((4,0,0)),'C1':pose(),'Transform':pose()}
        if missing_offset:joint.pop('C1')
        data['nodes'] += [
            {'id':'pa','class_name':'Part','name':'PA','parent':'scope:workspace','properties':part},
            {'id':'pb','class_name':'Part','name':'PB','parent':'scope:workspace','properties':other},
            {'id':'pm','class_name':'Motor6D','name':'Motor','parent':'pa','properties':joint},
        ]
        return data

    def test_unknown_root_metadata_blocks_pose_without_partial_writes(self):
        results=self.run_script('''local a,b=workspace.PA,workspace.PB
assert(not pcall(function()return a.AssemblyRootPart end))
assert(client.propertyOrigin(a,"AssemblyRootPart")=="unavailable")
assert(not pcall(function()a.Motor.C0=CFrame.new(10,0,0)end))
frame(a.Motor.C0,CFrame.new(4,0,0));vec(b.Position,Vector3.new(4,0,0))''',self.partial_snapshot(missing_root=True))
        for r in results:self.assertFalse(any(m.get('id') in {'pa','pb','pm'} for m in r['mutations']))

    def test_deferred_failure_reports_error_and_can_be_repaired_and_retried(self):
        h=self.host(self.partial_snapshot(missing_offset=True))
        failed=self.execute(h,'getgenv().a=workspace.PA;getgenv().b=workspace.PB;a.Motor.Transform=CFrame.new(0,3,0)',pose_errors=True)
        self.assertEqual(failed['joint_pose_errors'][0]['operation'],'motor_transform_batch')
        self.execute(h,'vec(a.Position,Vector3.zero);vec(b.Position,Vector3.new(4,0,0))')
        result=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':transforms.SNAPSHOT['sequence']+1,'kind':'set','id':'pm','fields':{'C1':pose()}}]}})
        self.assertTrue(result['ok'],result)
        self.execute(h,'a.Motor.Transform=a.Motor.Transform')
        self.execute(h,'vec(b.Position,Vector3.new(4,3,0))')

    def test_mutation_budget_failure_keeps_immediate_offset_and_parts_unchanged(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().a=a;getgenv().b=b;getgenv().c=c;getgenv().ab=ab
local filler=Instance.new("Folder");local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()ab.C0=CFrame.new(100,0,0)end))
frame(ab.C0,CFrame.new(4,0,0));vec(a.Position,Vector3.zero);vec(b.Position,Vector3.new(4,0,0));vec(c.Position,Vector3.new(8,0,0))''')

    def test_failed_motor_group_does_not_partially_apply_other_queued_assemblies(self):
        h=self.host(self.partial_snapshot(missing_offset=True))
        self.execute(h,RIG+'''getgenv().b=b;getgenv().ab=ab
ab.Transform=CFrame.new(0,5,0)
workspace.PA.Motor.Transform=CFrame.new(0,3,0)''',pose_errors=True)
        self.execute(h,'vec(b.Position,Vector3.new(4,0,0));vec(workspace.PB.Position,Vector3.new(4,0,0));ab.Transform=ab.Transform')
        self.execute(h,'vec(b.Position,Vector3.new(4,5,0));vec(workspace.PB.Position,Vector3.new(4,0,0))')

    def test_root_change_notifications_follow_graph_and_priority_updates(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().a=a;getgenv().b=b;getgenv().c=c
getgenv().calls=0;b:GetPropertyChangedSignal("AssemblyRootPart"):Connect(function()calls+=1 end)
c.RootPriority=127''')
        self.execute(h,'assert(calls==1 and b.AssemblyRootPart==c);c.RootPriority=127')
        self.execute(h,'assert(calls==1);c.Parent=nil')
        self.execute(h,'assert(calls==2 and b.AssemblyRootPart==a)')

    def test_destroying_pending_motor_does_not_move_its_former_parts(self):
        h=self.host()
        self.execute(h,RIG+'''getgenv().b=b;getgenv().c=c
ab.Transform=CFrame.new(0,100,0);ab:Destroy()''')
        self.execute(h,'vec(b.Position,Vector3.new(4,0,0));vec(c.Position,Vector3.new(8,0,0))')


if __name__=='__main__':unittest.main()
