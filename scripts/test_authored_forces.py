"""Native force mutation lifecycle through the own-character physics bridge."""
import copy
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_ground import GroundGeometry
from physics_script_bridge import PhysicsScriptBridge
from test_physics_force_frames import force_world,node,frame,Y90


def bridge(data):
    b=PhysicsScriptBridge(lambda state:OwnCharacterPhysicsController(
        state,geometry=GroundGeometry((),()),gravity=0.))
    b.update(data);return b


def write(identifier,key,value):
    return {'kind':'set','id':identifier,'property':key,'value':value}


class AuthoredForceBridgeTests(unittest.TestCase):
    def test_received_force_edits_are_local_and_newer_receipts_supersede_them(self):
        data=force_world(2);original=copy.deepcopy(data);b=bridge(data)
        b.apply_mutations([write('force','Force',{'$vector3':[16,0,0]})])
        self.assertEqual(b.controller._force_acceleration(),(8,0,0))
        self.assertEqual(data,original)
        changed=copy.deepcopy(data);node(changed,'humanoid')['properties']['WalkSpeed']=20
        b.update(changed);self.assertEqual(b.controller._force_acceleration(),(8,0,0))
        node(changed,'force')['property_revisions']={'Force':5}
        b.update(changed);self.assertEqual(b.controller._force_acceleration(),(4,0,0))
        self.assertEqual(b.apply_mutations([write('force','Force',{'$vector3':[100,0,0]})],observed_revision=4),0)
        self.assertEqual(b.controller._force_acceleration(),(4,0,0))

    def test_attachment_edits_replace_stale_absolute_frames(self):
        data=force_world();a=node(data,'a0')
        a['properties']['WorldCFrame']=frame([1,0,0,0,1,0,0,0,1]);a['origins']['WorldCFrame']='received'
        b=bridge(data);b.apply_mutations([write('a0','CFrame',frame(Y90))])
        self.assertEqual(b.controller._force_acceleration(),(0,0,-4))
        self.assertIn('WorldCFrame',a['properties'])

    def test_disable_reparent_destroy_and_character_reset_clear_force(self):
        b=bridge(force_world(2))
        b.apply_mutations([write('force','Enabled',False)])
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.apply_mutations([write('force','Enabled',True),write('force','Parent',None)])
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.apply_mutations([write('force','Parent',{'$ref':'root'})])
        self.assertEqual(b.controller._force_acceleration(),(4,0,0))
        b.apply_mutations([{'kind':'destroy','ids':['force']}])
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.update(force_world(2));self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.clear();b.update(force_world(2));self.assertEqual(b.controller._force_acceleration(),(4,0,0))

    def test_created_bodyforce_lifecycle_and_id_collision(self):
        data=force_world(2);node(data,'force')['properties']['Enabled']=False;b=bridge(data)
        create={'kind':'create','node':{'id':'local:1','class_name':'BodyForce',
                'parent':None,'properties':{'Force':{'$vector3':[8,0,0]}}}}
        self.assertEqual(b.apply_mutations([create]),1)
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.apply_mutations([write('local:1','Parent',{'$ref':'root'})])
        self.assertEqual(b.controller._force_acceleration(),(4,0,0))
        b.update(copy.deepcopy(data));self.assertEqual(b.controller._force_acceleration(),(4,0,0))
        b.apply_mutations([write('local:1','force',{'$vector3':[0,4,0]})])
        self.assertEqual(b.controller._force_acceleration(),(0,2,0))
        self.assertEqual(b.apply_mutations([create]),0)
        b.apply_mutations([{'kind':'destroy','ids':['local:1']}])
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))

    def test_invalid_local_force_does_not_retain_previous_force(self):
        b=bridge(force_world(2))
        b.apply_mutations([write('force','Force',{'$vector3':[float('nan'),0,0]})])
        self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.apply_mutations([write('force','Force',{'$vector3':[2,0,0]})])
        self.assertEqual(b.controller._force_acceleration(),(1,0,0))

    def test_local_force_can_be_reparented_after_root_replacement(self):
        data=force_world(2);node(data,'force')['properties']['Enabled']=False;b=bridge(data)
        b.apply_mutations([{'kind':'create','node':{'id':'local:1','class_name':'BodyForce',
            'parent':'root','properties':{'Force':{'$vector3':[8,0,0]}}}}])
        changed=copy.deepcopy(data);node(changed,'root')['id']='root2'
        b.update(changed);self.assertEqual(b.controller._force_acceleration(),(0,0,0))
        b.apply_mutations([write('local:1','Parent',{'$ref':'root2'})])
        self.assertEqual(b.controller._force_acceleration(),(4,0,0))


class NativeForceBridgeTests(unittest.TestCase):
    def test_persistent_luau_enable_edit_and_destroy_update_same_controller(self):
        import test_cframe_transforms as transforms
        import test_joint_poses as poses
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        h=poses.JointPoseTests.host(self,data)
        def execute(source):return poses.JointPoseTests.execute(self,h,source)
        result=execute('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local attachment=Instance.new("Attachment",root)
getgenv().force=Instance.new("VectorForce",root)
force.Attachment0=attachment;force.ApplyAtCenterOfMass=true;force.Force=Vector3.new(8,0,0)
''')
        b=bridge(data);b.apply_mutations(result['mutations'])
        self.assertEqual(b.controller._force_acceleration(),(4,0,0))
        for source,expected in [('force.Enabled=false',(0,0,0)),
                                ('force.Enabled=true;force.Force=Vector3.new(0,6,0)',(0,3,0)),
                                ('force:Destroy()',(0,0,0))]:
            result=execute(source);b.apply_mutations(result['mutations'])
            self.assertEqual(b.controller._force_acceleration(),expected)
        self.assertGreater(b.report()['authored_forces']['applied_writes'],0)

    def test_real_luau_creation_enum_changes_and_force_reach_prediction(self):
        import test_cframe_transforms as transforms
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        results=transforms.CFrameTransformTests.run_script(self,'''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local attachment=Instance.new("Attachment",root)
local force=Instance.new("VectorForce",root)
assert(force.RelativeTo==Enum.ActuatorRelativeTo.Attachment0)
force.Attachment0=attachment;force.ApplyAtCenterOfMass=true
force.RelativeTo=Enum.ActuatorRelativeTo.World
assert(force.RelativeTo==Enum.ActuatorRelativeTo.World)
assert(not pcall(function()force.RelativeTo=Enum.PartType.Block end))
force.Force=Vector3.new(8,0,0)
''',data)
        for result in results:
            b=bridge(data);self.assertGreater(b.apply_mutations(result['mutations']),0)
            self.assertEqual(b.controller._force_acceleration(),(4,0,0))
            b.controller.step(.1)
            self.assertAlmostEqual(b.controller.linear_velocity[0],.4)
            local_ids=list(b._force_state.local_nodes)
            b.apply_mutations([{'kind':'destroy','ids':local_ids}])
            self.assertEqual(b.controller._force_acceleration(),(0,0,0))

    def test_bodyforce_legacy_alias_is_coherent_in_real_luau(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local force=Instance.new("BodyForce")
local changes=0;force:GetPropertyChangedSignal("force"):Connect(function()changes+=1 end)
force.force=Vector3.new(1,2,3);vec(force.Force,force.force)
force.Force=Vector3.new(4,5,6);vec(force.force,Vector3.new(4,5,6))
task.wait(.001);assert(changes==2)
''')


if __name__=='__main__':unittest.main()
