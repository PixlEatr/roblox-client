"""Joint lifecycle and received poses in compiled local worlds, without sockets."""
import copy
import unittest
import test_cframe_transforms as transforms
import test_joint_poses as poses
from test_part_transforms import pose


class JointActivationTests(unittest.TestCase):
    run_script = poses.JointPoseTests.run_script
    host = poses.JointPoseTests.host
    execute = poses.JointPoseTests.execute
    partial_snapshot = poses.JointPoseTests.partial_snapshot

    def update(self, host, actions, errors=False):
        start = getattr(self, 'sequence', transforms.SNAPSHOT['sequence'])
        for index, action in enumerate(actions, start + 1):
            action['sequence'] = index
        self.sequence = start + len(actions)
        result = host.call({'op':'update','delta':{'version':1,'delta':True,'actions':actions},'timeout_ms':1000})
        self.assertTrue(result['ok'], result)
        self.assertFalse(result.get('task_errors'), result)
        self.assertEqual(bool(result.get('joint_pose_errors')), errors, result)
        return result

    def test_connecting_preconfigured_joint_settles_rooted_branch(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Anchored=true;a.CFrame=CFrame.new(10,0,0)
local b=Instance.new("Part",workspace);b.CFrame=CFrame.new(100,0,0)
local j=Instance.new("Motor6D");j.C0=CFrame.new(3,2,0);j.C1=CFrame.new(1,0,0)
j.Transform=CFrame.new(0,0,5);j.Part0=a;j.Part1=b;j.Parent=a
task.wait(.001)
vec(a.Position,Vector3.new(10,0,0));vec(b.Position,Vector3.new(12,2,5))
''')

    def test_activation_without_transform_write_and_reenable(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Anchored=true
local b=Instance.new("Part",workspace);b.CFrame=CFrame.new(100,0,0)
local j=Instance.new("Weld",a);j.Enabled=false;j.C0=CFrame.new(4,0,0);j.Part0=a;j.Part1=b
j.Enabled=true;task.wait(.001);vec(b.Position,Vector3.new(4,0,0))
j.Enabled=false;b.CFrame=CFrame.new(80,0,0);j.Enabled=true
task.wait(.001);vec(b.Position,Vector3.new(4,0,0))
''')

    def test_parenting_whole_model_and_rewiring_endpoint(self):
        self.run_script('''
local m=Instance.new("Model");local a=Instance.new("Part",m);a.Anchored=true
local b=Instance.new("Part",m);b.CFrame=CFrame.new(100,0,0)
local j=Instance.new("Weld",a);j.C0=CFrame.new(3,0,0);j.Part0=a;j.Part1=b
m.Parent=workspace;task.wait(.001);vec(b.Position,Vector3.new(3,0,0))
local c=Instance.new("Part",workspace);c.CFrame=CFrame.new(90,0,0)
j.Part1=c;task.wait(.001);vec(c.Position,Vector3.new(3,0,0));vec(b.Position,Vector3.new(3,0,0))
m.Parent=nil;a.CFrame=CFrame.new(20,0,0);m.Parent=workspace
task.wait(.001);vec(c.Position,Vector3.new(23,0,0))
''')

    def test_removing_conflicting_joint_activates_the_remaining_joint(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Anchored=true
local b=Instance.new("Part",workspace)
local x=Instance.new("Weld",a);x.C0=CFrame.new(3,0,0);x.Part0=a;x.Part1=b
local y=Instance.new("Weld",a);y.C0=CFrame.new(9,0,0);y.Part0=a;y.Part1=b
task.wait(.001);assert(x.Active and not y.Active);vec(b.Position,Vector3.new(3,0,0))
x:Destroy();task.wait(.001);assert(y.Active);vec(b.Position,Vector3.new(9,0,0))
''')

    def test_weldconstraint_activation_keeps_observed_offset(self):
        self.run_script('''
local a=Instance.new("Part",workspace);a.Anchored=true
local b=Instance.new("Part",workspace);b.CFrame=CFrame.new(100,4,8)*CFrame.Angles(.2,.3,.4)
local before=b.CFrame;local j=Instance.new("WeldConstraint",a);j.Part0=a;j.Part1=b
task.wait(.001);frame(b.CFrame,before)
''')

    def test_received_offsets_pose_rig_without_outbound_echo(self):
        h=self.host(self.partial_snapshot())
        self.execute(h,'getgenv().calls=0;workspace.PB:GetPropertyChangedSignal("CFrame"):Connect(function()calls+=1 end)')
        result=self.update(h,[{'kind':'set','id':'pm','fields':{'C0':pose((9,2,3)), 'C1':pose((1,0,0))}}])
        self.assertFalse(any(m.get('id') in {'pa','pb','pm'} for m in result['mutations']),result)
        self.execute(h,'''vec(workspace.PA.Position,Vector3.zero);vec(workspace.PB.Position,Vector3.new(8,2,3))
assert(calls==1);assert(client.propertyOrigin(workspace.PB,"CFrame")=="derived_received")''')

    def test_received_transform_replaces_authored_target(self):
        h=self.host(self.partial_snapshot())
        self.execute(h,'workspace.PA.Motor.Transform=CFrame.new(0,2,0)')
        self.update(h,[{'kind':'set','id':'pm','fields':{'Transform':pose((0,5,0))}}])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(4,5,0));workspace.PA.Motor.C0=CFrame.new(6,0,0)')
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(6,5,0))')

    def test_received_authoritative_part_pose_wins_regardless_of_action_order(self):
        for reverse in [False, True]:
            with self.subTest(reverse=reverse):
                h=self.host(self.partial_snapshot())
                actions=[{'kind':'set','id':'pm','fields':{'C0':pose((20,0,0))}},
                         {'kind':'set','id':'pb','fields':{'CFrame':pose((70,2,3))}}]
                self.update(h,actions[::-1] if reverse else actions)
                self.execute(h,'''vec(workspace.PB.Position,Vector3.new(70,2,3))
assert(client.propertyOrigin(workspace.PB,"CFrame")=="received")''')
                self.update(h,[{'kind':'set','id':'pm','fields':{'C0':pose((21,0,0))}}])
                self.execute(h,'vec(workspace.PB.Position,Vector3.new(21,0,0))')

    def test_received_creation_order_and_missing_child_pose(self):
        data=self.partial_snapshot()
        nodes=copy.deepcopy(data['nodes'][-3:]);nodes[1]['properties'].pop('CFrame')
        h=self.host()
        self.update(h,[{'kind':'create','id':node['id'],'node':node} for node in reversed(nodes)])
        self.execute(h,'''vec(workspace.PB.Position,Vector3.new(4,0,0))
assert(client.propertyOrigin(workspace.PB,"CFrame")=="derived_received")''')

    def test_received_creation_keeps_explicit_child_pose(self):
        data=self.partial_snapshot();nodes=copy.deepcopy(data['nodes'][-3:])
        nodes[1]['properties']['CFrame']=pose((40,3,2))
        h=self.host()
        self.update(h,[{'kind':'create','id':node['id'],'node':node} for node in reversed(nodes)])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(40,3,2))')

    def test_failed_received_batch_is_atomic_and_retryable(self):
        h=self.host(self.partial_snapshot())
        failed=self.update(h,[{'kind':'set','id':'pm','fields':{'Transform':pose((0,4,0)), 'C1':{'$cframe':{'position':[0,0,0]}}}}],errors=True)
        self.assertEqual(len(failed['joint_pose_errors']),1)
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(4,0,0))')
        self.update(h,[{'kind':'set','id':'pm','fields':{'C1':pose()}}])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(4,4,0))')

    def test_received_removal_drops_pending_pose(self):
        h=self.host(self.partial_snapshot())
        self.update(h,[{'kind':'set','id':'pm','fields':{'Transform':pose((0,100,0))}},
                       {'kind':'remove','id':'pm'}])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(4,0,0))')

    def chain_snapshot(self):
        data=self.partial_snapshot()
        child=copy.deepcopy(data['nodes'][-2]);child.update(id='pc',name='PC')
        child['properties']['CFrame']=pose((8,0,0))
        joint=copy.deepcopy(data['nodes'][-1]);joint.update(id='pn',name='Second',parent='pb')
        joint['properties'].update(Part0={'$ref':'pb'},Part1={'$ref':'pc'})
        data['nodes'] += [child,joint]
        return data

    def test_received_multiple_offsets_solve_in_root_order_before_callbacks(self):
        h=self.host(self.chain_snapshot())
        self.execute(h,'''workspace.PB:GetPropertyChangedSignal("CFrame"):Connect(function()
vec(workspace.PC.Position,Vector3.new(12,2,5)) end)''')
        self.update(h,[{'kind':'set','id':'pn','fields':{'C0':pose((6,0,5))}},
                       {'kind':'set','id':'pm','fields':{'C0':pose((6,2,0))}}])
        self.execute(h,'''vec(workspace.PB.Position,Vector3.new(6,2,0));vec(workspace.PC.Position,Vector3.new(12,2,5))''')

    def test_authoritative_middle_part_still_allows_child_joint_solving(self):
        h=self.host(self.chain_snapshot())
        self.update(h,[{'kind':'set','id':'pm','fields':{'C0':pose((30,0,0))}},
                       {'kind':'set','id':'pn','fields':{'C0':pose((6,0,0))}},
                       {'kind':'set','id':'pb','fields':{'CFrame':pose((40,0,0))}}])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(40,0,0));vec(workspace.PC.Position,Vector3.new(46,0,0))')

    def test_bad_child_joint_cannot_partially_apply_parent_joint(self):
        h=self.host(self.chain_snapshot())
        self.update(h,[{'kind':'set','id':'pm','fields':{'C0':pose((30,0,0))}},
                       {'kind':'set','id':'pn','fields':{'C0':{'$cframe':{'position':[6,0,0]}}}}],errors=True)
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(4,0,0));vec(workspace.PC.Position,Vector3.new(8,0,0))')
        # Retry every affected target after repairing the invalid child offset.
        self.update(h,[{'kind':'set','id':'pm','fields':{'C0':pose((30,0,0))}},
                       {'kind':'set','id':'pn','fields':{'C0':pose((6,0,0))}}])
        self.execute(h,'vec(workspace.PB.Position,Vector3.new(30,0,0));vec(workspace.PC.Position,Vector3.new(36,0,0))')

    def test_initial_snapshot_keeps_authoritative_part_poses(self):
        data=self.partial_snapshot();data['nodes'][-2]['properties']['CFrame']=pose((40,0,0))
        self.run_script('task.wait(.001);vec(workspace.PB.Position,Vector3.new(40,0,0))',data)


if __name__=='__main__':unittest.main()
