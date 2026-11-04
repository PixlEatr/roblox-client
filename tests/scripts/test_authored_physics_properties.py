"""Authored Humanoid controls reach local prediction without changing receipts."""
import copy
import math
import unittest
from unittest import mock
from pathlib import Path
import os
import time

from physics_controller import OwnCharacterPhysicsController
from physics_script_bridge import PhysicsScriptBridge
from physics_runtime_prediction import RuntimePhysicsPrediction
from physics_ground import GroundGeometry, AABB
from test_physics_controller import snapshot, intent


def mutation(name,value,receiver='humanoid'):
    return {'kind':'set','id':receiver,'property':name,'value':value}


def bridge_for(state=None):
    b=PhysicsScriptBridge(lambda s:OwnCharacterPhysicsController(s,gravity=0.))
    b.update(state or snapshot());return b


class AuthoredPropertyTests(unittest.TestCase):
    def test_speed_updates_while_moving_and_zero_can_resume_without_new_move(self):
        b=bridge_for();b.consume([intent(1)])
        b.apply_mutations([mutation('WalkSpeed',8)])
        self.assertAlmostEqual(b.step(.1)['predicted_state']['position'][0],.8)
        b.apply_mutations([mutation('WalkSpeed',0)])
        self.assertAlmostEqual(b.step(.1)['predicted_state']['position'][0],.8)
        b.apply_mutations([mutation('WalkSpeed',20)])
        self.assertAlmostEqual(b.step(.1)['predicted_state']['position'][0],2.8)

    def test_unrelated_receipts_keep_override_same_value_new_revision_clears_it(self):
        state=snapshot();state['nodes'][2]['property_revisions']={'WalkSpeed':5}
        b=bridge_for(state);b.apply_mutations([mutation('WalkSpeed',8)])
        state['nodes'][2]['properties']['Health']=90
        state['nodes'][2]['property_revisions']['Health']=6;b.update(state)
        self.assertEqual(b.controller.controller.walk_speed,8)
        state['nodes'][2]['property_revisions']['WalkSpeed']=7;b.update(state)
        self.assertEqual(b.controller.controller.walk_speed,10)
        self.assertEqual(b.report()['authored_properties']['active'],0)

    def test_stale_worker_result_cannot_restore_override_after_newer_receipt(self):
        state=snapshot();state['nodes'][2]['property_revisions']={'WalkSpeed':5,'Health':4}
        b=bridge_for(state);b.apply_mutations([mutation('WalkSpeed',8)],observed_revision=5)
        state['nodes'][2]['property_revisions']['WalkSpeed']=9;b.update(state)
        b.apply_mutations([mutation('WalkSpeed',4),mutation('Health',80)],observed_revision=8)
        self.assertEqual(b.controller.controller.walk_speed,10)
        self.assertEqual(b.controller.jump_settings['Health'],80)
        self.assertEqual(b.report()['authored_properties']['stale_writes_ignored'],1)
        b.apply_mutations([mutation('WalkSpeed',12)],observed_revision=9)
        self.assertEqual(b.controller.controller.walk_speed,12)

    def test_invalid_worker_revision_does_not_apply_any_properties(self):
        b=bridge_for()
        for revision in (True,-1,.5,float('nan'),float('inf'),'1'):
            with self.assertRaises(ValueError):
                b.apply_mutations([mutation('WalkSpeed',8)],observed_revision=revision)
        self.assertEqual(b.controller.controller.walk_speed,10)

    def test_only_changed_property_override_is_cleared(self):
        state=snapshot();b=bridge_for(state)
        b.apply_mutations([mutation('WalkSpeed',8),mutation('AutoRotate',False)])
        state['nodes'][2]['properties']['WalkSpeed']=20;b.update(state)
        self.assertEqual(b.controller.controller.walk_speed,20)
        self.assertFalse(b.controller.auto_rotate)

    def test_snapshot_and_authored_values_are_detached(self):
        state=snapshot();before=copy.deepcopy(state);b=bridge_for(state)
        records=[mutation('WalkSpeed',8)];b.apply_mutations(records)
        records[0]['value']=60
        self.assertEqual(b.controller.controller.walk_speed,8)
        self.assertEqual(state,before)
        self.assertEqual(b.snapshot,before)
        self.assertEqual(b.report()['network_packets_sent'],0)

    def test_foreign_and_unmodeled_properties_do_not_change_controlled_avatar(self):
        b=bridge_for()
        b.apply_mutations([mutation('WalkSpeed',80,'other'),mutation('Gravity',0),
                           {'kind':'create','node':{'class_name':'Humanoid'}},
                           mutation('WalkSpeed',32,'player')])
        self.assertEqual(b.controller.controller.walk_speed,10)
        self.assertEqual(b.report()['authored_properties']['active'],0)

    def test_disconnect_and_character_replacement_drop_local_overrides(self):
        b=bridge_for();b.apply_mutations([mutation('WalkSpeed',8)])
        b.clear();b.update(snapshot());self.assertEqual(b.controller.controller.walk_speed,10)
        b.apply_mutations([mutation('WalkSpeed',8)])
        b.update(snapshot(character='new-character'))
        self.assertEqual(b.controller.controller.walk_speed,10)

    def test_invalid_speed_stops_old_speed_instead_of_silently_retaining_it(self):
        b=bridge_for();b.consume([intent(1)]);b.step(.1)
        b.apply_mutations([mutation('WalkSpeed',1000)])
        state=b.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0],1)
        self.assertEqual(state['linear_velocity'][0],0)
        self.assertEqual(b.controller.last_reason,'invalid_initial_pose_or_speed')

    def test_autorotate_false_preserves_received_heading_and_can_be_enabled(self):
        state=snapshot();state['nodes'][2]['properties']['AutoRotate']=False
        state['nodes'][3]['properties']['CFrame']={'$cframe':{
            'position':[0,0,0],'rotation':[0,0,1,0,1,0,-1,0,0]}}
        b=bridge_for(state);b.consume([intent(1)])
        result=b.step(.1)['predicted_state']
        self.assertAlmostEqual(result['yaw'],math.pi/2)
        self.assertEqual(result['angular_velocity'],[0,0,0])
        self.assertEqual(result['yaw_origin'],'received_cframe')
        b.apply_mutations([mutation('AutoRotate',True)])
        result=b.step(.1)['predicted_state'];self.assertAlmostEqual(result['yaw'],-math.pi/2)

    def test_unknown_heading_is_labeled_and_not_invented_as_received(self):
        state=snapshot();state['nodes'][2]['properties']['AutoRotate']=False
        b=bridge_for(state);b.consume([intent(1)])
        result=b.step(.1)['predicted_state']
        self.assertEqual(result['yaw'],0)
        self.assertEqual(result['yaw_origin'],'local_default_heading')

    def test_death_stops_horizontal_motion_while_gravity_continues(self):
        b=PhysicsScriptBridge();b.update(snapshot((0,10,0)));b.consume([intent(1)])
        b.step(.1);b.apply_mutations([mutation('Health',0)])
        result=b.step(.1)['predicted_state']
        self.assertAlmostEqual(result['position'][0],1.)
        self.assertLess(result['position'][1],9.019)
        self.assertEqual(result['humanoid_state']['name'],'Dead')
        self.assertEqual(result['humanoid_state']['value'],15)
        outcomes=b.consume([intent(2),intent(3,'MoveTo',vector=(10,0,0))])
        self.assertTrue(all(not o['accepted'] for o in outcomes))
        self.assertTrue(all(o['reason']=='humanoid_not_alive' for o in outcomes))


def ready_world():
    from test_world_snapshot import fixture
    world,root=fixture()
    world.schema['enums']=[{'name':'PartType'}]
    for spec in [{'name':'CanCollide','type':9},{'name':'size','type':22},{'name':'shape','type':7,'enum':0}]:
        index=len(world.schema['properties']);world.schema['properties'].append(spec)
        world.schema['classes'][3]['properties'].append(index)
    world.instances[root]['properties'].update(Anchored=False,size=[2,2,1],
        CFrame={'position':[0,3,0],'rotation_id':2})
    world.touch(root,['Anchored','size','CFrame'])
    world.instances[(1,5)]['properties'].update(Health=100,MaxHealth=100,WalkSpeed=16)
    world.touch((1,5),['Health','MaxHealth','WalkSpeed'])
    floor=(1,90)
    world.instances[floor]={'class':3,'parent':(1,6),'properties':{
        'CanCollide':True,'Anchored':True,'size':[200,2,200],'shape':1,
        'CFrame':{'position':[0,-1,0],'rotation_id':2}}}
    world.touch(floor,created=True)
    return world,root


class NativeAuthoredPropertyTests(unittest.TestCase):
    def test_explicit_controls_without_received_defaults_in_both_workers(self):
        import test_cframe_transforms as transforms
        state = copy.deepcopy(transforms.SNAPSHOT)
        humanoid = next(n for n in state['nodes'] if n['class_name'] == 'Humanoid')
        humanoid['properties'].pop('WalkSpeed', None)
        humanoid['properties'].pop('AutoRotate', None)
        results = transforms.CFrameTransformTests.run_script(self, '''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
h.WalkSpeed=8;h.AutoRotate=false
assert(h.WalkSpeed==8 and h.AutoRotate==false)
assert(client.propertyOrigin(h,"WalkSpeed")=="local")
assert(client.propertyOrigin(h,"AutoRotate")=="local")
''', state)
        for result in results:
            controls = {m['property']: m['value'] for m in result['mutations']
                        if m.get('kind') == 'set' and m.get('id') == humanoid['id']}
            self.assertEqual(controls['WalkSpeed'], 8)
            self.assertIs(controls['AutoRotate'], False)

    def test_control_writes_still_validate_types_in_both_workers(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self, '''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
h.WalkSpeed=8;h.AutoRotate=false
for _,v in {true, "fast", {}} do
 assert(not pcall(function()h.WalkSpeed=v end))
end
for _,v in {1, "false", {}} do
 assert(not pcall(function()h.AutoRotate=v end))
end
assert(h.WalkSpeed==8 and h.AutoRotate==false)
''')


class PersistentAuthoredPropertyTests(unittest.TestCase):
    def setUp(self):
        import persistent_runtime
        self.world,self.root=ready_world()
        default=persistent_runtime.RUST_HOST
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(default)))
        self.host_patch=mock.patch.object(persistent_runtime,'RUST_HOST',host);self.host_patch.start()
        self.addCleanup(self.host_patch.stop)
        self.runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:self.world,123)
        self.addCleanup(self.runtime.close);self.now=time.monotonic()

    def execute_at(self,elapsed,source):
        with mock.patch('persistent_runtime.time.monotonic',return_value=self.now+elapsed):
            result=self.runtime.execute('local h=game:GetService("Players").LocalPlayer.Character.Humanoid\n'+source)
        return result

    def test_actual_luau_speed_stop_resume_and_rotation(self):
        before=copy.deepcopy(self.world.instances)
        r=self.execute_at(0,'h.WalkSpeed=8;h.AutoRotate=false;h:Move(Vector3.xAxis);return true')
        self.assertTrue(r['ok'],r)
        predictor=self.runtime.movement_preview
        r=predictor.advance(self.world,123,self.now+.1)
        self.assertAlmostEqual(r['predicted_position'][0],.8,places=5)
        self.assertAlmostEqual(r['controller']['predicted_state']['yaw'],0.)
        r=self.execute_at(.2,'h.WalkSpeed=0;return true');self.assertTrue(r['ok'],r)
        x=r['local_movement']['predicted_position'][0]
        stopped=predictor.advance(self.world,123,self.now+.3)
        self.assertAlmostEqual(stopped['predicted_position'][0],x,places=5)
        r=self.execute_at(.4,'h.WalkSpeed=20;h.AutoRotate=true;return true');self.assertTrue(r['ok'],r)
        r=predictor.advance(self.world,123,self.now+.5)
        self.assertAlmostEqual(r['predicted_position'][0],x+2,places=5)
        self.assertAlmostEqual(r['controller']['predicted_state']['yaw'],-math.pi/2)
        self.assertEqual(self.world.instances,before)

    def test_actual_luau_damage_stops_walking_and_rejects_new_move(self):
        self.assertTrue(self.execute_at(0,'h:Move(Vector3.xAxis);return true')['ok'])
        r=self.execute_at(.1,'h:TakeDamage(100);h:Move(Vector3.xAxis);return true')
        self.assertTrue(r['ok'],r)
        self.assertEqual(r['local_movement']['controller']['predicted_state']['humanoid_state']['name'],'Dead')
        self.assertEqual(r['local_movement']['rejected_intents'],1)
        x=r['local_movement']['predicted_position'][0]
        r=self.runtime.movement_preview.advance(self.world,123,self.now+.2)
        self.assertAlmostEqual(r['predicted_position'][0],x,places=5)
        self.assertEqual(self.world.instances[(1,5)]['properties']['Health'],100)

    def test_received_same_value_supersedes_authored_speed_in_vm_and_prediction(self):
        self.assertTrue(self.execute_at(0,'h.WalkSpeed=8;h:Move(Vector3.xAxis);return true')['ok'])
        self.world.touch((1,5),['WalkSpeed'])
        r=self.execute_at(.1,'assert(h.WalkSpeed==16);return true');self.assertTrue(r['ok'],r)
        self.assertEqual(self.runtime.movement_preview.bridge.controller.controller.walk_speed,16)
        self.assertEqual(r['local_movement']['authored_properties']['active'],0)

    def test_actual_stale_worker_result_cannot_override_a_newer_receipt(self):
        self.assertTrue(self.execute_at(0,'return true')['ok'])
        old_revision=self.world.revision
        self.world.touch((1,5),['WalkSpeed'])
        # Issue directly to the existing worker to model an in-flight
        # background transaction against its older, already-loaded snapshot.
        result=self.runtime.host.call({'op':'execute','source':
            'game:GetService("Players").LocalPlayer.Character.Humanoid.WalkSpeed=4;return true'})
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['world_revision'],old_revision)
        with mock.patch('persistent_runtime.time.monotonic',return_value=self.now+.1):
            recorded=self.runtime._record(result)
        self.assertEqual(self.runtime.movement_preview.bridge.controller.controller.walk_speed,16)
        self.assertEqual(recorded['local_movement']['authored_properties']['stale_writes_ignored'],1)
        self.assertTrue(self.execute_at(.2,'assert(game:GetService("Players").LocalPlayer.Character.Humanoid.WalkSpeed==16);return true')['ok'])

    def test_script_error_does_not_undo_prior_committed_property_write(self):
        self.assertTrue(self.execute_at(0,'h:Move(Vector3.xAxis);return true')['ok'])
        r=self.execute_at(.1,'h.WalkSpeed=5;error("fixture")');self.assertFalse(r['ok'])
        self.assertEqual(self.runtime.movement_preview.bridge.controller.controller.walk_speed,5)

if __name__=='__main__':unittest.main()
