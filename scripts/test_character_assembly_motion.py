"""Received assembly members participate in own-character motion."""
import copy
import unittest
from types import SimpleNamespace
from test_direct_velocity import native_world,velocity
from test_body_velocity import world,bridge
from test_physics_controller import intent
from physics_forces import received_character_motion_parts
from persistent_runtime import PersistentClientScriptRuntime


def add_member(data,root,identifier='limb',parent=None):
    p={'AssemblyRootPart':{'$ref':root['id']},'Anchored':False,
       'AssemblyLinearVelocity':{'$vector3':[0,0,0]},'AssemblyAngularVelocity':{'$vector3':[0,0,0]}}
    part={'id':identifier,'name':identifier,'class_name':'Part','parent':parent or root['parent'],
          'properties':p,'origins':dict.fromkeys(p,'received')}
    data['nodes'].append(part);return part


class AssemblyMotionTests(unittest.TestCase):
    def test_limb_velocity_and_impulse_share_root_body(self):
        data=world();root=data['nodes'][-1];add_member(data,root)
        b=bridge(data);out=b.consume([velocity(1,(2,5,0),'limb'),intent(2,'Impulse',(20,20,0),'limb')])
        self.assertTrue(all(o['accepted'] for o in out),out)
        b.controller.step(.1);self.assertEqual(b.controller.linear_velocity,(12,15,0))
        self.assertEqual([o['instance_id'] for o in out],['limb','limb'])

    def test_nested_accessory_is_supported(self):
        data=world();root=data['nodes'][-1]
        data['nodes'].append({'id':'hat','class_name':'Accessory','parent':'char'})
        add_member(data,root,parent='hat')
        self.assertEqual(received_character_motion_parts(data,'root','char'),{'root','limb'})

    def test_missing_or_local_reference_is_not_membership(self):
        for origin in (None,'local','class_default','derived_received'):
            data=world();member=add_member(data,data['nodes'][-1]);member['origins']['AssemblyRootPart']=origin
            b=bridge(data);self.assertFalse(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])

    def test_detached_destroyed_anchored_and_other_world_members_rejected(self):
        for variant in ('detach','destroy','anchor','other','world','cycle'):
            data=world();member=add_member(data,data['nodes'][-1])
            if variant=='detach':member['parent']=None
            elif variant=='destroy':member['destroyed']=True
            elif variant=='anchor':member['properties']['Anchored']=True
            elif variant=='other':member['properties']['AssemblyRootPart']={'$ref':'other'}
            elif variant=='world':
                data['nodes'].append({'id':'world','class_name':'WorldModel','parent':'char'});member['parent']='world'
            else:member['parent']='limb'
            b=bridge(data);self.assertFalse(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'],variant)

    def test_receipt_refresh_revokes_previous_membership(self):
        data=world();member=add_member(data,data['nodes'][-1]);b=bridge(data)
        self.assertTrue(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])
        member['properties']['AssemblyRootPart']={'$ref':'other'};b.update(data)
        self.assertFalse(b.consume([velocity(2,(9,9,9),'limb')])[0]['accepted'])

    def test_member_cannot_receive_humanoid_methods(self):
        data=world();add_member(data,data['nodes'][-1]);b=bridge(data)
        for sequence,kind in enumerate(('Move','MoveTo','Jump','ChangeState','SetStateEnabled'),1):
            self.assertFalse(b.consume([intent(sequence,kind,instance_id='limb')])[0]['accepted'])

    def test_native_call_time_membership_overrides_stale_snapshot(self):
        data=world();add_member(data,data['nodes'][-1]);b=bridge(data)
        for sequence,root_id in enumerate(('',None,'old-root'),1):
            item=velocity(sequence,(1,2,3),'limb');item['assembly_root_id']=root_id
            self.assertFalse(b.consume([item])[0]['accepted'])
        self.assertEqual(b.controller.accepted,0)

    def test_membership_does_not_mutate_received_nodes(self):
        data=world();add_member(data,data['nodes'][-1]);original=copy.deepcopy(data)
        b=bridge(data);b.consume([velocity(1,(1,2,3),'limb')]);b.controller.step(.1)
        self.assertEqual(data,original)


class NativeAssemblyMotionTests(unittest.TestCase):
    def test_luau_members_share_immediate_values_and_keep_original_receiver(self):
        import test_cframe_transforms as transforms
        data,root=native_world();add_member(data,root);add_member(data,root,'otherLimb')
        source='''
local char=game:GetService("Players").LocalPlayer.Character
char.limb.AssemblyLinearVelocity=Vector3.new(3,40,5)
vec(char.HumanoidRootPart.AssemblyLinearVelocity,Vector3.new(3,40,5))
vec(char.otherLimb.AssemblyLinearVelocity,Vector3.new(3,40,5))
vec(char.otherLimb:GetVelocityAtPosition(Vector3.zero),Vector3.new(3,40,5))
char.otherLimb:ApplyImpulse(Vector3.new(4,20,0))
'''
        for result in transforms.CFrameTransformTests.run_script(self,source,data):
            self.assertEqual([i['instance_id'] for i in result['movement_intents']],['limb','otherLimb'])
            self.assertEqual([i['assembly_root_id'] for i in result['movement_intents']],[root['id']]*2)
            b=bridge(data);b.apply_mutations(result['mutations']);b.consume(result['movement_intents']);b.controller.step(.1)
            self.assertEqual(b.controller.linear_velocity,(5,50,5))

    def test_detach_before_call_does_not_push_old_root(self):
        import test_cframe_transforms as transforms
        data,root=native_world();add_member(data,root)
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
local limb=char.limb;limb.Parent=nil
limb.AssemblyLinearVelocity=Vector3.new(3,40,5)
limb:ApplyImpulse(Vector3.new(4,20,0))
vec(char.HumanoidRootPart.AssemblyLinearVelocity,Vector3.zero)
''',data):
            b=bridge(data);out=b.consume(result['movement_intents'])
            self.assertTrue(all(not o['accepted'] for o in out),out)
            self.assertEqual(b.controller.vertical_velocity,0)

    def test_persistent_feedback_signals_and_detach_stop_propagation(self):
        import test_joint_poses as poses
        data,root=native_world();add_member(data,root)
        host=poses.JointPoseTests.host(self,data)
        execute=lambda source:poses.JointPoseTests.execute(self,host,source)
        result=execute('''
getgenv().char=game:GetService("Players").LocalPlayer.Character
getgenv().limb=char.limb
limb.AssemblyLinearVelocity=Vector3.new(0,50,0)
getgenv().changes=0
limb:GetPropertyChangedSignal("AssemblyLinearVelocity"):Connect(function()changes+=1 end)
''')
        b=bridge(data,gravity=100);b.consume(result['movement_intents']);b.controller.step(.1)
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=host;runtime._physics_feedback_token=None;runtime.background_budget_ms=1000
        runtime.movement_preview=SimpleNamespace(bridge=b,report=lambda:{'controller':b.controller.report()})
        runtime._publish_physics_feedback()
        r=execute('task.wait(.001);near(limb.AssemblyLinearVelocity.Y,40);assert(changes>=1)')
        self.assertEqual(r['movement_intents'],[])
        execute('limb.Parent=nil')
        b.controller.step(.1);runtime._publish_physics_feedback()
        execute('near(limb.AssemblyLinearVelocity.Y,40);near(char.HumanoidRootPart.AssemblyLinearVelocity.Y,30)')

    def test_anchoring_before_call_rejects_stale_controller_motion(self):
        import test_cframe_transforms as transforms
        data,root=native_world();add_member(data,root)
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
char.HumanoidRootPart.Anchored=true
char.limb:ApplyImpulse(Vector3.new(4,20,0))
char.HumanoidRootPart.AssemblyLinearVelocity=Vector3.new(3,40,5)
''',data):
            b=bridge(data);out=b.consume(result['movement_intents'])
            self.assertTrue(all(not o['accepted'] for o in out),out)

    def test_invalid_member_write_leaves_every_member_unchanged(self):
        import test_cframe_transforms as transforms
        data,root=native_world();add_member(data,root);add_member(data,root,'otherLimb')
        transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
char.limb.AssemblyLinearVelocity=Vector3.new(1,2,3)
assert(not pcall(function()char.otherLimb.AssemblyLinearVelocity=Vector3.new(math.huge,0,0)end))
for _,part in {char.limb,char.otherLimb,char.HumanoidRootPart} do
 vec(part.AssemblyLinearVelocity,Vector3.new(1,2,3))
end
''',data)


if __name__=='__main__':unittest.main()
