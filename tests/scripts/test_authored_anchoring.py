"""Anchoring suspends a connected body without discarding predicted pose."""
import copy
import unittest
from test_body_velocity import world,bridge
from test_direct_velocity import velocity,native_world
from test_authored_forces import write
from test_physics_controller import intent
from test_joint_motion_membership import rig
from physics_controller import OwnCharacterPhysicsController
from physics_forces import predicted_assembly_mass


def pose(controller):
    return (controller.controller.position[0],controller.vertical_position,controller.controller.position[2])


class AuthoredAnchoringTests(unittest.TestCase):
    def test_root_freezes_gravity_and_walking_then_resumes_at_predicted_pose(self):
        data=world();original=copy.deepcopy(data);b=bridge(data,gravity=100)
        b.consume([intent(1)]);b.step(.1);c=b.controller
        before=pose(c);previous_velocity=c.vertical_velocity;seconds=c.simulated_seconds
        b.apply_mutations([write('root','Anchored',True)])
        for _ in range(5):self.assertIsNone(b.step(.1)['predicted_state'])
        self.assertEqual(pose(c),before);self.assertEqual(c.vertical_velocity,previous_velocity)
        self.assertEqual(c.simulated_seconds,seconds);self.assertTrue(c.anchored)
        b.apply_mutations([write('root','Anchored',False)])
        self.assertEqual(pose(c),before)
        b.step(.1);self.assertGreater(pose(c)[0],before[0]);self.assertLess(pose(c)[1],before[1])
        self.assertEqual(data,original)

    def test_anchored_limb_stops_walking_and_gravity_for_entire_body(self):
        data,root,limb,joint=rig();b=bridge(data,gravity=100)
        b.consume([intent(1)]);b.step(.1);c=b.controller;before=pose(c)
        b.apply_mutations([write('limb','Anchored',True)])
        self.assertTrue(c.anchored);self.assertIsNone(c.assembly_mass)
        self.assertIsNone(b.step(.2)['predicted_state']);self.assertEqual(pose(c),before)
        b.apply_mutations([write('limb','Anchored',False)])
        self.assertFalse(c.anchored);self.assertEqual(pose(c),before)
        b.step(.1);self.assertGreater(pose(c)[0],before[0])

    def test_breaking_anchor_connection_releases_body_without_teleport(self):
        data,root,limb,joint=rig(reference=True);b=bridge(data)
        b.consume([velocity(1,(10,3,0))]);b.step(.1);before=pose(b.controller)
        b.apply_mutations([write('limb','Anchored',True)])
        self.assertTrue(b.controller.anchored)
        b.apply_mutations([write('joint','Enabled',False)])
        self.assertFalse(b.controller.anchored);self.assertEqual(pose(b.controller),before)
        b.step(.1);self.assertGreater(pose(b.controller)[0],before[0])
        b.apply_mutations([write('joint','Enabled',True)])
        self.assertTrue(b.controller.anchored)

    def test_received_anchor_connection_also_blocks_all_physics(self):
        data,root,limb,joint=rig();limb['properties']['Anchored']=True
        c=bridge(data,gravity=100).controller
        self.assertTrue(c.anchored);self.assertIsNone(c.step(.1)['predicted_state'])
        self.assertEqual(predicted_assembly_mass(data,root['id']),(None,None))

    def test_ordered_anchor_impulse_and_unanchor(self):
        b=bridge(world())
        out=b.consume_batch([write('root','Anchored',True),write('root','Anchored',False)],
            [{**intent(1,'Impulse',(20,0,0),'root'),'mutation_cursor':0},
             {**intent(2,'Impulse',(20,0,0),'root'),'mutation_cursor':1},
             {**intent(3,'Impulse',(20,0,0),'root'),'mutation_cursor':2}])
        self.assertEqual([x['accepted'] for x in out],[True,False,True])
        self.assertEqual(b.controller._impulse_velocity,(20,0,0))

    def test_forces_do_not_accumulate_during_suspension(self):
        b=bridge(world())
        b.apply_mutations([{'kind':'create','node':{'id':'local:force','class_name':'BodyForce',
            'parent':'root','properties':{'Force':{'$vector3':[20,0,0]}}}}])
        b.step(.1);c=b.controller;v=c._impulse_velocity;before=pose(c)
        b.apply_mutations([write('root','Anchored',True)])
        for _ in range(10):b.step(.2)
        self.assertEqual(c._impulse_velocity,v);self.assertEqual(pose(c),before)
        b.apply_mutations([write('root','Anchored',False)])
        b.step(.1);self.assertAlmostEqual(c._impulse_velocity[0],v[0]+1)

    def test_received_revision_supersedes_local_anchor_without_position_reset(self):
        data=world();b=bridge(data);b.consume([velocity(1,(10,0,0))]);b.step(.1)
        before=pose(b.controller);b.apply_mutations([write('root','Anchored',True)])
        b.update(copy.deepcopy(data));self.assertTrue(b.controller.anchored)
        data['nodes'][-1]['property_revisions']={'Anchored':10};b.update(data)
        self.assertFalse(b.controller.anchored);self.assertEqual(pose(b.controller),before)
        b.apply_mutations([write('root','Anchored',True)],observed_revision=9)
        self.assertFalse(b.controller.anchored)

    def test_unknown_anchor_does_not_gain_authored_permission(self):
        data=world();data['nodes'][-1]['origins']['Anchored']='local'
        c=OwnCharacterPhysicsController(data)
        self.assertEqual(c.last_reason,'anchored_state_unconfirmed')
        self.assertIsNone(c.step(.1)['predicted_state'])
        b=bridge(world());b.apply_mutations([write('root','Anchored','invalid')])
        self.assertIsNone(b.step(.1)['predicted_state'])

    def test_replaced_character_does_not_reuse_suspended_pose(self):
        from test_physics_controller import snapshot
        b=bridge(world());b.consume([velocity(1,(10,0,0))]);b.step(.1)
        b.apply_mutations([write('root','Anchored',True)])
        b.update(snapshot((30,40,50),character='new-char',root='new-root'))
        self.assertEqual(pose(b.controller),(30,40,50))

    def test_native_luau_anchor_batches_control_prediction(self):
        import test_cframe_transforms as transforms
        data,root=native_world()
        for result in transforms.CFrameTransformTests.run_script(self,'''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root:ApplyImpulse(Vector3.new(20,0,0))
root.Anchored=true
root:ApplyImpulse(Vector3.new(20,0,0))
root.Anchored=false
root:ApplyImpulse(Vector3.new(20,0,0))
''',data):
            b=bridge(data);out=b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertEqual([x['accepted'] for x in out],[True,False,True])
            self.assertEqual(b.controller._impulse_velocity,(20,0,0))

    def test_persistent_runtime_suspends_and_resumes_without_reset(self):
        import os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,_=ready_world();original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        prefix='local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart\n'
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                self.assertTrue(runtime.execute(prefix+'root.AssemblyLinearVelocity=Vector3.new(10,20,0)')['ok'])
                b=runtime.movement_preview.bridge;b.step(.1)
                self.assertTrue(runtime.execute(prefix+'root.Anchored=true')['ok'])
                c=b.controller;before=pose(c)
                self.assertIsNone(b.step(.2)['predicted_state'])
                self.assertEqual(pose(c),before)
                self.assertTrue(runtime.execute(prefix+'root.Anchored=false')['ok'])
                self.assertEqual(pose(c),before)
                b.step(.1);self.assertGreater(pose(c)[0],before[0])
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
