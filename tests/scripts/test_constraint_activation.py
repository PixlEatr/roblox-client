"""Reference attachments must be present in the same simulation world."""
import copy
import unittest

from physics_forces import collect_received_force_sources
from test_linear_velocity import world_with_constraint
from test_physics_force_frames import force_world, node, frame, IDENTITY


def reference_world():
    data=world_with_constraint(relative=1)
    source=data['nodes'][-1]
    source['properties']['Attachment1']={'$ref':'reference-attachment'}
    source['origins']['Attachment1']='received'
    data['nodes'] += [
        {'id':'reference','class_name':'Part','parent':'workspace',
         'properties':{'CFrame':frame(IDENTITY),'Anchored':True},
         'origins':{'CFrame':'received','Anchored':'received'}},
        {'id':'reference-attachment','class_name':'Attachment','parent':'reference',
         'properties':{'CFrame':frame(IDENTITY)},'origins':{'CFrame':'received'}}]
    return data


class ConstraintActivationTests(unittest.TestCase):
    def test_linear_reference_removed_from_world_stops_prediction(self):
        data=reference_world()
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)
        node(data,'reference')['parent']=None
        self.assertEqual(collect_received_force_sources(data,'root'),[])
        node(data,'reference')['parent']='workspace'
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)

    def test_nested_worldmodel_is_a_separate_constraint_world(self):
        for data in (reference_world(),force_world(1)):
            data['nodes'].append({'id':'other','class_name':'WorldModel','parent':'workspace'})
            node(data,'reference')['parent']='other'
            self.assertEqual(collect_received_force_sources(data,'root'),[])

    def test_vectorforce_reference_removed_from_world_stops_prediction(self):
        data=force_world(1);node(data,'reference')['parent']=None
        self.assertEqual(collect_received_force_sources(data,'root'),[])

    def test_actuator_itself_must_share_the_driven_world(self):
        data=world_with_constraint()
        data['nodes'].append({'id':'other','class_name':'WorldModel','parent':'workspace'})
        node(data,'constraint')['parent']='other'
        self.assertEqual(collect_received_force_sources(data,'root'),[])

    def test_common_received_workspace_preserves_valid_constraint(self):
        data=reference_world()
        data['nodes'] += [{'id':'workspace','class_name':'Workspace','parent':'game'},
                          {'id':'game','class_name':'DataModel','parent':None}]
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)


class NativeConstraintActivationTests(unittest.TestCase):
    def test_missing_received_relative_mode_is_not_guessed(self):
        import test_cframe_transforms as transforms
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        data['nodes'] += [
            {'id':'received-attachment','class_name':'Attachment','name':'ReceivedAttachment',
             'parent':root['id'],'properties':{},'origins':{},'server_owned':True},
            {'id':'received-constraint','class_name':'LinearVelocity','name':'ReceivedMover',
             'parent':root['id'],'properties':{'Enabled':True,'Attachment0':{'$ref':'received-attachment'},
                                            'Attachment1':None},
             'origins':dict.fromkeys(['Enabled','Attachment0','Attachment1'],'received'),'server_owned':True}]
        transforms.CFrameTransformTests.run_script(self,'''
local mover=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart.ReceivedMover
assert(not pcall(function()return mover.Active end))
mover.RelativeTo=Enum.ActuatorRelativeTo.World
assert(mover.Active)
''',data)

    def test_linear_reference_mode_checks_both_attachment_lifetimes(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local p=Instance.new("Part",workspace)
local q=Instance.new("Part",workspace)
local a=Instance.new("Attachment",p);local b=Instance.new("Attachment",q)
local mover=Instance.new("LinearVelocity",p);mover.Attachment0=a
assert(mover.Active)
mover.RelativeTo=Enum.ActuatorRelativeTo.Attachment1
assert(not mover.Active)
mover.Attachment1=b;assert(mover.Active)
assert(#p:GetConnectedParts(true)==0 and #q:GetConnectedParts(true)==0)
q.Parent=nil;assert(not mover.Active)
q.Parent=workspace;assert(mover.Active)
local other=Instance.new("WorldModel",workspace)
q.Parent=other;assert(not mover.Active)
q.Parent=workspace;mover.Enabled=false;assert(not mover.Active)
mover.Enabled=true;assert(mover.Active)
b:Destroy();assert(not mover.Active)
mover.RelativeTo=Enum.ActuatorRelativeTo.World;assert(mover.Active)
a:Destroy();assert(not mover.Active)
''')

    def test_vectorforce_reference_can_be_on_driven_part(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local p=Instance.new("Part",workspace)
local a=Instance.new("Attachment",p);local b=Instance.new("Attachment",p)
local force=Instance.new("VectorForce",p);force.Attachment0=a
force.RelativeTo=Enum.ActuatorRelativeTo.Attachment1
assert(not force.Active)
force.Attachment1=b;assert(force.Active)
assert(#p:GetConnectedParts(true)==0)
b.Parent=nil;assert(not force.Active)
b.Parent=p;assert(force.Active)
force.RelativeTo=Enum.ActuatorRelativeTo.World;b:Destroy();assert(force.Active)
''')

    def test_persistent_active_signal_tracks_reference_parenting(self):
        import test_joint_poses as poses
        host=poses.JointPoseTests.host(self)
        def execute(code):return poses.JointPoseTests.execute(self,host,code)
        execute('''
local p=Instance.new("Part",workspace);getgenv().reference=Instance.new("Part",workspace)
getgenv().mover=Instance.new("LinearVelocity",p)
mover.Attachment0=Instance.new("Attachment",p)
mover.Attachment1=Instance.new("Attachment",reference)
mover.RelativeTo=Enum.ActuatorRelativeTo.Attachment1
getgenv().changes=0
mover:GetPropertyChangedSignal("Active"):Connect(function()changes+=1 end)
assert(mover.Active)
''')
        execute('reference.Parent=nil;task.wait(.001);assert(not mover.Active and changes==1)')
        execute('reference.Parent=workspace;task.wait(.001);assert(mover.Active and changes==2)')
        execute('mover.Enabled=false;task.wait(.001);assert(not mover.Active and changes==3)')
        execute('reference.Parent=nil;task.wait(.001);assert(changes==3)')


if __name__=='__main__':unittest.main()
