"""Script-authored topology, per-part mass, and ordered native batches."""
import copy
import unittest
from test_body_velocity import world,bridge
from test_character_assembly_motion import add_member
from test_direct_velocity import native_world,velocity
from test_physics_controller import intent
from test_authored_forces import write


def isolated(data=None,root=None):
    if data is None:data=world();root=data['nodes'][-1]
    limb=add_member(data,root)
    root['properties'].update(Mass=2,Massless=False,RootPriority=100)
    root.setdefault('origins',{}).update(Mass='received',Massless='received',RootPriority='received')
    limb['properties'].update(Mass=3,Massless=False,RootPriority=0,AssemblyRootPart={'$ref':'limb'})
    limb['origins'].update(Mass='received',Massless='received',RootPriority='received')
    return data,root,limb


def weld(root='root'):
    return {'kind':'create','node':{'id':'local:weld','class_name':'WeldConstraint','parent':root,
        'properties':{'Part0':{'$ref':root},'Part1':{'$ref':'limb'},'Enabled':True}}}


def impulse(sequence=1,cursor=1,root='root'):
    return {**intent(sequence,'Impulse',(10,0,0),'limb'),'assembly_root_id':root,'mutation_cursor':cursor}


class AuthoredJointMotionTests(unittest.TestCase):
    def test_created_joint_connects_member_and_recomputes_mass(self):
        data,root,limb=isolated();original=copy.deepcopy(data);b=bridge(data)
        out=b.consume_batch([weld()],[impulse()])
        self.assertTrue(out[0]['accepted'],out)
        self.assertEqual(b.controller.assembly_mass,5)
        self.assertEqual(b.controller.assembly_mass_source,'authored_joint_assembly_parts')
        b.controller.step(.1);self.assertEqual(b.controller.linear_velocity,(2,0,0))
        self.assertEqual(data,original)

    def test_impulse_before_disable_uses_connected_mass_and_retains_velocity(self):
        data,root,limb=isolated();b=bridge(data)
        out=b.consume_batch([weld(),write('local:weld','Enabled',False)],[impulse(cursor=1)])
        self.assertTrue(out[0]['accepted']);self.assertEqual(b.controller.assembly_mass,2)
        b.controller.step(.1);self.assertEqual(b.controller.linear_velocity,(2,0,0))

    def test_disable_before_impulse_rejects_disconnected_member(self):
        data,root,limb=isolated();b=bridge(data)
        out=b.consume_batch([weld(),write('local:weld','Enabled',False)],[impulse(cursor=2)])
        self.assertFalse(out[0]['accepted']);self.assertEqual(b.controller.accepted,0)

    def test_reenable_destroy_and_detach_update_supported_force_sources(self):
        data,root,limb=isolated();b=bridge(data)
        force={'kind':'create','node':{'id':'local:force','class_name':'BodyForce','parent':'limb',
                                    'properties':{'Force':{'$vector3':[10,0,0]}}}}
        b.apply_mutations([weld(),force]);self.assertEqual(len(b.controller._force_sources),1)
        b.apply_mutations([write('local:weld','Enabled',False)]);self.assertEqual(b.controller._force_sources,())
        b.apply_mutations([write('local:weld','Enabled',True)]);self.assertEqual(len(b.controller._force_sources),1)
        b.apply_mutations([write('local:weld','Parent',None)]);self.assertEqual(b.controller._force_sources,())
        b.apply_mutations([write('local:weld','Parent',{'$ref':'root'})]);self.assertEqual(len(b.controller._force_sources),1)
        b.apply_mutations([{'kind':'destroy','ids':['local:weld']}]);self.assertEqual(b.controller._force_sources,())

    def test_unknown_new_assembly_mass_does_not_reuse_old_mass(self):
        data,root,limb=isolated();limb['properties'].pop('Mass');b=bridge(data)
        out=b.consume_batch([weld()],[impulse()])
        self.assertFalse(out[0]['accepted']);self.assertIsNone(b.controller.assembly_mass)
        self.assertTrue(b.consume([velocity(2,(3,4,5),'limb')])[0]['accepted'])

    def test_massless_member_does_not_add_mass(self):
        data,root,limb=isolated();limb['properties']['Massless']=True;b=bridge(data)
        b.consume_batch([weld()],[impulse()]);b.controller.step(.1)
        self.assertEqual(b.controller.assembly_mass,2)
        self.assertEqual(b.controller.linear_velocity,(5,0,0))

    def test_new_receipt_supersedes_local_joint_edit(self):
        from test_joint_motion_membership import rig
        data,root,limb,joint=rig();b=bridge(data)
        b.apply_mutations([write('joint','Enabled',False)])
        self.assertFalse(b.consume([velocity(1,(1,2,3),'limb')])[0]['accepted'])
        joint['property_revisions']={'Enabled':1};b.update(data)
        self.assertTrue(b.consume([velocity(2,(1,2,3),'limb')])[0]['accepted'])

    def test_malformed_cursor_batch_is_rejected_before_mutation(self):
        for records in ([impulse(cursor=2)],[impulse(cursor=-1)],
                        [impulse(cursor=1),impulse(2,cursor=0)],
                        [impulse(cursor=1),velocity(2,(1,2,3),'limb')]):
            data,root,limb=isolated();b=bridge(data)
            with self.assertRaises(ValueError):b.consume_batch([weld()],records)
            self.assertEqual(b._force_state.local_nodes,{})
            self.assertEqual(b.controller.accepted,0)

    def test_legacy_batch_and_per_prefix_environment_refresh(self):
        data,root,limb=isolated();b=bridge(data);calls=[]
        out=b.consume_batch([weld()],[velocity(1,(1,2,3),'limb')],after_mutations=lambda:calls.append(1))
        self.assertTrue(out[0]['accepted']);self.assertEqual(calls,[1])
        with self.assertRaises(ValueError):b.consume_batch([], [velocity(1,(0,0,0),'limb')])


class NativeAuthoredJointMotionTests(unittest.TestCase):
    def test_luau_create_impulse_then_disable_preserves_statement_order(self):
        import test_cframe_transforms as transforms
        data,root=native_world();isolated(data,root)
        for tail,expected in [('limb:ApplyImpulse(Vector3.new(10,0,0));weld.Enabled=false',2),
                              ('weld.Enabled=false;limb:ApplyImpulse(Vector3.new(10,0,0))',0)]:
            source='''
local char=game:GetService("Players").LocalPlayer.Character
local root,limb=char.HumanoidRootPart,char.limb
local weld=Instance.new("WeldConstraint",root);weld.Part0=root;weld.Part1=limb
'''+tail
            for result in transforms.CFrameTransformTests.run_script(self,source,data):
                b=bridge(data);b.consume_batch(result['mutations'],result['movement_intents'])
                b.controller.step(.1);self.assertAlmostEqual(b.controller.linear_velocity[0],expected)
                self.assertEqual(b.controller.assembly_mass,2)

    def test_luau_joint_creation_velocity_and_member_force(self):
        import test_cframe_transforms as transforms
        data,root=native_world();isolated(data,root)
        source='''
local char=game:GetService("Players").LocalPlayer.Character
local root,limb=char.HumanoidRootPart,char.limb
local weld=Instance.new("WeldConstraint",root);weld.Part0=root;weld.Part1=limb
limb.AssemblyLinearVelocity=Vector3.new(3,4,5)
vec(root.AssemblyLinearVelocity,Vector3.new(3,4,5))
local force=Instance.new("BodyForce",limb);force.Force=Vector3.new(10,0,0)
'''
        for result in transforms.CFrameTransformTests.run_script(self,source,data):
            b=bridge(data);out=b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertTrue(out[0]['accepted'],out)
            self.assertEqual(b.controller.assembly_mass,5)
            b.controller.step(.1)
            self.assertAlmostEqual(b.controller.linear_velocity[0],3.2)

    def test_all_native_movement_kinds_have_monotone_cursors(self):
        import test_cframe_transforms as transforms
        data,root=native_world()
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
local h=char:FindFirstChildOfClass("Humanoid")
h.WalkSpeed=10;h:Move(Vector3.xAxis)
h.WalkSpeed=12;h:MoveTo(Vector3.new(10,0,0))
h:SetStateEnabled(Enum.HumanoidStateType.Jumping,true)
h:ChangeState(Enum.HumanoidStateType.Freefall)
h.Jump=true
char.HumanoidRootPart:ApplyImpulse(Vector3.zero)
''',data):
            cursors=[item['mutation_cursor'] for item in result['movement_intents']]
            self.assertEqual(len(cursors),6);self.assertEqual(cursors,sorted(cursors))
            self.assertTrue(all(0<=value<=len(result['mutations']) for value in cursors))



class PersistentAuthoredJointMotionTests(unittest.TestCase):
    def test_real_runtime_routes_native_prefixes_into_prediction(self):
        import os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        for source,accepted in [('limb:ApplyImpulse(Vector3.new(10,0,0));weld.Enabled=false',1),
                                ('weld.Enabled=false;limb:ApplyImpulse(Vector3.new(10,0,0))',0)]:
            world,root=ready_world();limb=(1,91)
            index=len(world.schema['properties'])
            world.schema['properties'].append({'name':'AssemblyRootPart','type':28})
            world.schema['classes'][3]['properties'].append(index)
            world.instances[root]['properties'].update(Mass=2,Massless=False,AssemblyMass=2,RootPriority=100,AssemblyRootPart=root)
            world.touch(root,['Mass','Massless','AssemblyMass','RootPriority','AssemblyRootPart'])
            world.instances[limb]={'class':3,'parent':(1,3),'properties':{'Name':'limb','Anchored':False,
                'Mass':3,'Massless':False,'RootPriority':0,'AssemblyRootPart':limb,'size':[1,1,1],
                'CFrame':{'position':[0,4,0],'rotation_id':2}}}
            world.touch(limb,created=True);original=copy.deepcopy(world.instances)
            host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
            with mock.patch.object(persistent_runtime,'RUST_HOST',host):
                runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:world,123)
                try:
                    result=runtime.execute('''
local char=game:GetService("Players").LocalPlayer.Character
local root,limb=char.HumanoidRootPart,char.limb
local weld=Instance.new("WeldConstraint",root);weld.Part0=root;weld.Part1=limb
'''+source+'\nreturn true')
                    self.assertTrue(result['ok'],result)
                    c=runtime.movement_preview.bridge.controller
                    self.assertEqual(c.accepted,accepted)
                    self.assertAlmostEqual(c._impulse_velocity[0],2*accepted)
                    self.assertEqual(c.assembly_mass,2)
                    self.assertEqual(world.instances,original)
                finally:runtime.close()

if __name__=='__main__':unittest.main()
