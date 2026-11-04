"""Rigid received topology drives character motion without AssemblyRootPart."""
import copy
import unittest
from test_body_velocity import world,bridge
from test_direct_velocity import velocity,native_world
from test_character_assembly_motion import add_member
from physics_forces import received_character_motion_parts,collect_received_force_sources

IDENTITY={'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}}


def rig(data=None,root=None,reference=False):
    if data is None:data=world();root=data['nodes'][-1]
    limb=add_member(data,root)
    if not reference:
        limb['properties'].pop('AssemblyRootPart');limb['origins'].pop('AssemblyRootPart')
    limb['properties'].update(CFrame=copy.deepcopy(IDENTITY),Size={'$vector3':[1,1,1]})
    limb['origins'].update(CFrame='received',Size='received')
    properties={'Part0':{'$ref':root['id']},'Part1':{'$ref':'limb'},'Enabled':True,
                'C0':copy.deepcopy(IDENTITY),'C1':copy.deepcopy(IDENTITY),'Transform':copy.deepcopy(IDENTITY)}
    joint={'id':'joint','name':'RootJoint','class_name':'Motor6D','parent':root['id'],
           'properties':properties,'origins':dict.fromkeys(properties,'received')}
    data['nodes'].append(joint)
    return data,root,limb,joint


class JointMotionMembershipTests(unittest.TestCase):
    def test_motor_and_weld_classes_establish_membership_without_root_property(self):
        for kind in ('Motor6D','Motor','Weld','Snap','ManualWeld','WeldConstraint'):
            data,root,limb,joint=rig();joint['class_name']=kind
            self.assertEqual(received_character_motion_parts(data,root['id'],root['parent']),{root['id'],'limb'},kind)
            b=bridge(data);self.assertTrue(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])
            b.controller.step(.1);self.assertEqual(b.controller.linear_velocity,(1,2,3))

    def test_disabled_destroyed_detached_and_incomplete_joint_cannot_use_stale_reference(self):
        for variant in ('disabled','destroyed','detached','unknown_enabled','missing_endpoint','cross_world'):
            data,root,limb,joint=rig(reference=True)
            if variant=='disabled':joint['properties']['Enabled']=False
            elif variant=='destroyed':joint['destroyed']=True
            elif variant=='detached':joint['parent']=None
            elif variant=='unknown_enabled':joint['origins'].pop('Enabled')
            elif variant=='missing_endpoint':joint['properties']['Part0']={'$ref':'missing'}
            else:
                data['nodes'].append({'id':'world','class_name':'WorldModel','parent':root['parent']});joint['parent']='world'
            self.assertNotIn('limb',received_character_motion_parts(data,root['id'],root['parent']),variant)

    def test_received_disable_and_reenable_refresh_controller(self):
        data,root,limb,joint=rig();b=bridge(data)
        self.assertTrue(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])
        joint['properties']['Enabled']=False;b.update(data)
        self.assertFalse(b.consume([velocity(2,(1,2,3),'limb')])[0]['accepted'])
        joint['properties']['Enabled']=True;b.update(data)
        self.assertTrue(b.consume([velocity(3,(1,2,3),'limb')])[0]['accepted'])

    def test_removed_joint_cannot_restore_stale_reference_or_force(self):
        data,root,limb,joint=rig(reference=True)
        data['nodes'].append({'id':'force','class_name':'BodyForce','parent':'limb',
            'properties':{'Force':{'$vector3':[20,0,0]}},'origins':{'Force':'received'}})
        b=bridge(data);self.assertTrue(b.controller._force_sources)
        data['nodes'].remove(joint);b.update(data)
        self.assertFalse(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])
        self.assertEqual(b.controller._force_sources,())
        data['nodes'].append(joint);b.update(data)
        self.assertTrue(b.consume([velocity(2,(1,2,3),'limb')])[0]['accepted'])
        self.assertTrue(b.controller._force_sources)

    def test_chain_and_redundant_connections_are_one_component(self):
        data,root,limb,joint=rig();other=add_member(data,root,'hand')
        other['properties'].pop('AssemblyRootPart');other['origins'].pop('AssemblyRootPart')
        second=copy.deepcopy(joint);second.update(id='second',parent='limb')
        second['properties'].update(Part0={'$ref':'limb'},Part1={'$ref':'hand'})
        data['nodes'].append(second);data['nodes'].append({**copy.deepcopy(second),'id':'redundant'})
        self.assertEqual(received_character_motion_parts(data,root['id'],root['parent']),{root['id'],'limb','hand'})

    def test_anchored_member_blocks_entire_connected_body(self):
        data,root,limb,joint=rig();limb['properties']['Anchored']=True
        self.assertEqual(received_character_motion_parts(data,root['id'],root['parent']),set())
        b=bridge(data);self.assertFalse(b.consume([velocity(1,(1,2,3),root['id'])])[0]['accepted'])

    def test_flexible_constraint_and_authored_endpoint_do_not_prove_membership(self):
        for kind in ('BallSocketConstraint','SpringConstraint','RopeConstraint'):
            data,root,limb,joint=rig();joint['class_name']=kind
            self.assertNotIn('limb',received_character_motion_parts(data,root['id'],root['parent']))
        data,root,limb,joint=rig();joint['origins']['Part1']='local'
        self.assertNotIn('limb',received_character_motion_parts(data,root['id'],root['parent']))

    def test_unknown_anchor_and_conflicting_root_are_not_inferred(self):
        data,root,limb,joint=rig();limb['properties'].pop('Anchored')
        self.assertNotIn('limb',received_character_motion_parts(data,root['id'],root['parent']))
        data,root,limb,joint=rig(reference=True)
        limb['properties']['AssemblyRootPart']={'$ref':'another-root'}
        self.assertNotIn('limb',received_character_motion_parts(data,root['id'],root['parent']))

    def test_unknown_branch_does_not_erase_known_connected_limb(self):
        data,root,limb,joint=rig()
        unknown=copy.deepcopy(joint);unknown['id']='unknown';unknown['properties']['Part1']={'$ref':'missing'}
        data['nodes'].append(unknown)
        self.assertIn('limb',received_character_motion_parts(data,root['id'],root['parent']))

    def test_force_on_joint_member_reaches_controller_and_stops_after_disable(self):
        data,root,limb,joint=rig();data['nodes'].append({'id':'force','class_name':'BodyForce','parent':'limb',
            'properties':{'Force':{'$vector3':[20,0,0]}},'origins':{'Force':'received'}})
        b=bridge(data);self.assertEqual(len(collect_received_force_sources(data,root['id'])),1)
        b.controller.step(.1);self.assertAlmostEqual(b.controller.linear_velocity[0],1)
        joint['properties']['Enabled']=False;b.update(data)
        self.assertEqual(b.controller._force_sources,())
        b.controller.step(.1);self.assertAlmostEqual(b.controller.linear_velocity[0],1)


class NativeJointMotionMembershipTests(unittest.TestCase):
    def test_unknown_branch_does_not_erase_known_limb(self):
        import test_cframe_transforms as transforms
        data,root=native_world();_,_,_,joint=rig(data,root)
        unknown=copy.deepcopy(joint);unknown.update(id='unknown',name='UnknownJoint')
        unknown['properties']['Part1']={'$ref':'missing'};data['nodes'].append(unknown)
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
char.limb.AssemblyLinearVelocity=Vector3.new(1,2,3)
vec(char.HumanoidRootPart.AssemblyLinearVelocity,Vector3.new(1,2,3))
''',data):
            self.assertEqual(result['movement_intents'][0]['assembly_root_id'],root['id'])

    def test_actual_luau_joint_member_velocity_impulse_and_shared_readback(self):
        import test_cframe_transforms as transforms
        data,root=native_world();rig(data,root)
        source='''
local char=game:GetService("Players").LocalPlayer.Character
char.limb.AssemblyLinearVelocity=Vector3.new(3,40,5)
vec(char.HumanoidRootPart.AssemblyLinearVelocity,Vector3.new(3,40,5))
char.limb:ApplyImpulse(Vector3.new(4,20,0))
'''
        for result in transforms.CFrameTransformTests.run_script(self,source,data):
            self.assertEqual([i['assembly_root_id'] for i in result['movement_intents']],[root['id']]*2)
            b=bridge(data);b.consume(result['movement_intents']);b.controller.step(.1)
            self.assertEqual(b.controller.linear_velocity,(5,50,5))

    def test_disable_or_destroy_before_call_revokes_stale_explicit_reference(self):
        import test_cframe_transforms as transforms
        for action in ('joint.Enabled=false','joint:Destroy()','joint.Parent=nil'):
            data,root=native_world();rig(data,root,reference=True)
            source='''
local char=game:GetService("Players").LocalPlayer.Character
local joint=char.HumanoidRootPart.RootJoint
'''+action+'''
char.limb.AssemblyLinearVelocity=Vector3.new(3,40,5)
char.limb:ApplyImpulse(Vector3.new(4,20,0))
vec(char.HumanoidRootPart.AssemblyLinearVelocity,Vector3.zero)
'''
            for result in transforms.CFrameTransformTests.run_script(self,source,data):
                b=bridge(data);out=b.consume(result['movement_intents'])
                self.assertTrue(all(not o['accepted'] for o in out),out)

    def test_persistent_feedback_reaches_joint_members(self):
        from types import SimpleNamespace
        from persistent_runtime import PersistentClientScriptRuntime
        import test_joint_poses as poses
        data,root=native_world();rig(data,root)
        host=poses.JointPoseTests.host(self,data);execute=lambda code:poses.JointPoseTests.execute(self,host,code)
        result=execute('''getgenv().char=game:GetService("Players").LocalPlayer.Character
char.limb.AssemblyLinearVelocity=Vector3.new(0,50,0)''')
        b=bridge(data,gravity=100);b.consume(result['movement_intents']);b.controller.step(.1)
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=host;runtime._physics_feedback_token=None;runtime.background_budget_ms=1000
        runtime.movement_preview=SimpleNamespace(bridge=b,report=lambda:{'controller':b.controller.report()})
        runtime._publish_physics_feedback()
        execute('near(char.limb.AssemblyLinearVelocity.Y,40)')
        execute('char.HumanoidRootPart.RootJoint.Enabled=false')
        b.controller.step(.1);runtime._publish_physics_feedback()
        execute('near(char.limb.AssemblyLinearVelocity.Y,40);near(char.HumanoidRootPart.AssemblyLinearVelocity.Y,30)')

    def test_received_joint_removal_revokes_stale_reference(self):
        import test_joint_poses as poses
        import test_joint_activation as activation
        data,root=native_world();rig(data,root,reference=True)
        host=poses.JointPoseTests.host(self,data)
        execute=lambda code:poses.JointPoseTests.execute(self,host,code)
        execute('''getgenv().char=game:GetService("Players").LocalPlayer.Character
char.HumanoidRootPart.AssemblyLinearVelocity=Vector3.zero''')
        activation.JointActivationTests.update(self,host,[{'kind':'remove','id':'joint'}])
        result=execute('char.limb:ApplyImpulse(Vector3.new(4,20,0))')
        self.assertEqual(result['movement_intents'][0]['assembly_root_id'],'')
        b=bridge(data);self.assertFalse(b.consume(result['movement_intents'])[0]['accepted'])


if __name__=='__main__':unittest.main()
