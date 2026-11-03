"""Local ballistic jumps and actual Luau requests, with no live connection."""
import copy
import math
from pathlib import Path
import sys
import unittest
from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, OBB, GroundGeometry, step_vertical
from physics_body_collision import step_vertical_body
from physics_script_bridge import PhysicsScriptBridge
from test_physics_controller import snapshot
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime'))
import test_cframe_transforms as transforms

FLOOR = AABB((-100.,-1.,-100.),(100.,0.,100.))
GROUND = GroundGeometry((FLOOR,),())

def request(sequence=1, **settings):
    return dict(sequence=sequence, kind='Jump', instance_id='humanoid', jump_settings=settings)

def actor(**settings):
    s=snapshot((0.,3.,0.))
    s['nodes'][2]['properties'].update(settings)
    s['nodes'][3]['properties']['Size']={'$vector3':[2.,2.,1.]}
    return s

class JumpPhysicsTests(unittest.TestCase):
    def test_power_apex_and_landing_partition_invariant(self):
        for steps in (2,10,50):
            c=OwnCharacterPhysicsController(actor(),geometry=GROUND,gravity=100.)
            c.consume([request(JumpPower=50.)])
            for _ in range(steps):c.step(.5/steps)
            self.assertAlmostEqual(c.vertical_position,15.5,places=9)
            for _ in range(steps):c.step(.5/steps)
            self.assertAlmostEqual(c.vertical_position,3.)
            self.assertTrue(c.grounded)
            self.assertEqual(c.step(0)['predicted_state']['humanoid_state']['name'],'Running')

    def test_zero_time_report_preserves_pending_upward_velocity(self):
        c=OwnCharacterPhysicsController(actor(),geometry=GROUND)
        c.consume([request()])
        report=c.report()
        self.assertFalse(report['predicted_state']['grounded'])
        self.assertEqual(c.vertical_velocity,50.)
        self.assertGreater(c.step(.1)['predicted_state']['position'][1],3.)

    def test_height_under_different_gravity(self):
        for g in (20.,196.2,500.):
            c=OwnCharacterPhysicsController(actor(UseJumpPower=False,JumpHeight=7.2),geometry=GROUND,gravity=g)
            c.consume([request()]); left=math.sqrt(2*7.2/g)
            while left>1e-12:
                dt=min(.25,left);c.step(dt);left-=dt
            self.assertAlmostEqual(c.vertical_position,10.2,places=9)
            self.assertAlmostEqual(c.vertical_velocity,0.,places=9)

    def test_state_midair_rejection_and_repeat_after_landing(self):
        c=OwnCharacterPhysicsController(actor(),geometry=GROUND)
        c.consume([request()])
        self.assertEqual(c.step(.1)['predicted_state']['humanoid_state']['name'],'Jumping')
        velocity=c.vertical_velocity;c.consume([request(2)])
        self.assertEqual(c.last_reason,'jump_requires_ground')
        self.assertEqual(c.vertical_velocity,velocity)
        self.assertEqual(c.step(.25)['predicted_state']['humanoid_state']['name'],'Freefall')
        for _ in range(6):c.step(.1)
        c.consume([request(3)])
        self.assertEqual(c.accepted,2)
        self.assertGreater(c.vertical_velocity,0)

    def test_invalid_disabled_dead_and_unsupported_settings(self):
        cases=[({'JumpPower':0},'jump_disabled'),({'JumpHeight':0,'UseJumpPower':False},'jump_disabled'),
               ({'JumpPower':-1},'invalid_jump_settings'),({'JumpPower':float('nan')},'invalid_jump_settings'),
               ({'JumpPower':True},'invalid_jump_settings'),({'JumpPower':1001},'invalid_jump_settings'),
               ({'UseJumpPower':1},'invalid_jump_settings'),({'Health':0},'humanoid_not_alive'),
               ({'PlatformStand':True},'jump_state_not_supported'),({'Sit':True},'jump_state_not_supported')]
        for settings,reason in cases:
            with self.subTest(settings=settings):
                c=OwnCharacterPhysicsController(actor(),geometry=GROUND);c.consume([request(**settings)])
                self.assertEqual(c.last_reason,reason);self.assertEqual(c.vertical_velocity,0.)
                self.assertTrue(c.grounded);self.assertEqual(c.rejected,1)

    def test_zero_gravity_power_works_height_disabled(self):
        c=OwnCharacterPhysicsController(actor(),geometry=GROUND,gravity=0.)
        c.consume([request(UseJumpPower=False,JumpHeight=7.2)])
        self.assertEqual(c.last_reason,'jump_disabled')
        c.consume([request(2,JumpPower=50)])
        self.assertAlmostEqual(c.step(.1)['predicted_state']['position'][1],8.)

    def test_ownership_anchoring_and_removed_ground(self):
        for state,geometry,receiver,reason in [
            (actor(),GROUND,'player','jump_requires_humanoid'),
            (actor(),GROUND,'other','different_character'),
            (snapshot((0,3,0),anchored=True),GROUND,'humanoid','anchored'),
            (actor(),GroundGeometry((),()),'humanoid','jump_requires_ground')]:
            c=OwnCharacterPhysicsController(state,geometry=geometry)
            c.consume([{**request(),'instance_id':receiver}]);self.assertEqual(c.last_reason,reason)
        c=OwnCharacterPhysicsController(actor(),geometry=GROUND)
        c.update_geometry(GroundGeometry((),()));c.consume([request()])
        self.assertEqual(c.last_reason,'jump_requires_ground')

    def test_settings_refresh_and_replacement(self):
        s=actor(JumpPower=10);c=OwnCharacterPhysicsController(s,geometry=GROUND)
        s['nodes'][2]['properties']['JumpPower']=40;c.update_snapshot(s);c.consume([request()])
        self.assertEqual(c.vertical_velocity,40);c.step(.1)
        s=actor(JumpPower=20);s['nodes'][3]['id']='root2';c.update_snapshot(s)
        self.assertTrue(c.grounded);self.assertEqual(c.vertical_velocity,0)
        c.consume([request(2)]);self.assertEqual(c.vertical_velocity,20)

    def test_jump_preserves_move_to(self):
        c=OwnCharacterPhysicsController(actor(),geometry=GROUND)
        c.consume([dict(sequence=1,kind='MoveTo',instance_id='humanoid',vector=[20,3,0]),request(2)])
        r=c.step(.1)['predicted_state'];self.assertAlmostEqual(r['position'][0],1.)
        self.assertGreater(r['position'][1],3.);self.assertEqual(r['target'],[20.,3.,0.])

    def test_bridge_once_and_received_snapshot_unchanged(self):
        s=actor();before=copy.deepcopy(s)
        b=PhysicsScriptBridge(lambda s:OwnCharacterPhysicsController(s,geometry=GROUND));b.update(s)
        self.assertTrue(b.consume([request()])[0]['accepted'])
        with self.assertRaises(ValueError):b.consume([request()])
        r=b.step(.1);self.assertGreater(r['predicted_state']['position'][1],3.)
        self.assertEqual(s,before);self.assertEqual(r['network_packets_sent'],0)
        self.assertFalse(r['server_confirmed'])

    def test_thin_ceiling_and_remaining_frame_fall(self):
        g=GroundGeometry((FLOOR,AABB((-20.,6.,-20.),(20.,6.001,20.))),())
        c=OwnCharacterPhysicsController(actor(),geometry=g,gravity=100.)
        c.consume([request(JumpPower=50)]);r=c.step(.2)
        impact=(50-math.sqrt(2500-400))/100
        self.assertAlmostEqual(c.vertical_position,5.-1e-6-50*(.2-impact)**2,places=9)
        self.assertLess(c.vertical_velocity,0);self.assertTrue(r['body_collision']['ceiling_contact'])
        for _ in range(5):c.step(.1)
        self.assertTrue(c.grounded)

    def test_ceiling_before_apex_despite_smaller_net_rise(self):
        g=GroundGeometry((FLOOR,AABB((-10,4.45,-10),(10,4.46,10))),())
        step,hit=step_vertical_body((0,3,0),10.,.15,g,False,half_size=(1,1,.5),gravity=100.)
        self.assertIsNotNone(hit);self.assertLess(step.position[1],3.45);self.assertLess(step.vertical_velocity,0)

    def test_rotated_ceiling_and_initial_overlap(self):
        c,s=math.cos(.3),math.sin(.3)
        g=GroundGeometry((FLOOR,OBB((0,6,0),(10,.1,10),(c,-s,0,s,c,0,0,0,1))),())
        step,hit=step_vertical_body((0,3,0),30.,.2,g,False,half_size=(1,1,.5))
        self.assertIsNotNone(hit);self.assertLess(step.position[1],5.)
        step,hit=step_vertical_body((0,6,0),30.,.1,g,False,half_size=(1,1,.5))
        self.assertTrue(hit.initial_overlap);self.assertEqual(step.position,(0.,6.,0.))
        self.assertEqual(step.vertical_velocity,0.)

    def test_missing_dimensions_does_not_invent_ceiling(self):
        g=GroundGeometry((AABB((-10,4,-10),(10,4.1,10)),),())
        step,hit=step_vertical_body((0,3,0),50.,.1,g,False)
        self.assertIsNone(hit);self.assertGreater(step.position[1],4.1)

    def test_terminal_speed_crossing_partition_invariant(self):
        g=GroundGeometry((),());kw=dict(gravity=10,max_fall_speed=10)
        a=step_vertical((0,100,0),-9,.2,g,False,**kw)
        b=step_vertical((0,100,0),-9,.1,g,False,**kw)
        b=step_vertical(b.position,b.vertical_velocity,.1,g,False,**kw)
        self.assertAlmostEqual(a.position[1],98.05);self.assertEqual(a.vertical_velocity,-10)
        self.assertAlmostEqual(a.position[1],b.position[1])

class NativeJumpTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script
    prefix='local h=game:GetService("Players").LocalPlayer.Character.Humanoid\n'

    def test_default_coalescing_and_capture_settings_at_boundary(self):
        for r in self.run_script(self.prefix+'''
assert(h.Jump==false);h.Jump=true;h.Jump=true
h.JumpPower=60;h.UseJumpPower=false;h.JumpHeight=12;assert(h.Jump==true)
'''):
            jumps=r['movement_intents'];self.assertEqual(len(jumps),1)
            self.assertEqual(jumps[0]['kind'],'Jump');self.assertNotIn('vector',jumps[0])
            self.assertEqual(jumps[0]['jump_settings']['JumpHeight'],12)
            self.assertFalse(jumps[0]['jump_settings']['UseJumpPower'])

    def test_cancel(self):
        for r in self.run_script(self.prefix+'h.Jump=true;h.Jump=false'):
            self.assertEqual(r['movement_intents'],[])

    def test_reenable_keeps_order_and_single_request(self):
        for r in self.run_script(self.prefix+'''
h:Move(Vector3.xAxis);h.Jump=true;h.Jump=false;h.Jump=true;h:Move(Vector3.zero)
'''):
            self.assertEqual([i['kind'] for i in r['movement_intents']],['Move','Jump','Move'])
            self.assertEqual([i['sequence'] for i in r['movement_intents']],[1,2,3])

    def test_reset_and_new_request_after_yield(self):
        for r in self.run_script(self.prefix+'''
h.Jump=true;task.wait();assert(h.Jump==false)
h.Jump=true;task.wait();assert(h.Jump==false)
'''):
            self.assertEqual([i['kind'] for i in r['movement_intents']],['Jump','Jump'])

    def test_destroy_before_scheduler_cancels(self):
        for r in self.run_script('local h=Instance.new("Humanoid");h.Jump=true;h:Destroy()'):
            self.assertEqual(r['movement_intents'],[])

    def test_invalid_write_and_full_queue_atomic(self):
        for r in self.run_script(self.prefix+'''
assert(not pcall(function() h.Jump=1 end));assert(h.Jump==false)
for i=1,256 do h:Move(Vector3.zero) end
assert(not pcall(function() h.Jump=true end));assert(h.Jump==false)
'''):
            self.assertEqual(len(r['movement_intents']),256)
            self.assertTrue(all(i['kind']=='Move' for i in r['movement_intents']))

    def test_settings_validation(self):
        self.run_script(self.prefix+'''
h.JumpPower=2000;assert(h.JumpPower==1000)
h.JumpPower=-10;assert(h.JumpPower==0);h.JumpHeight=-2;assert(h.JumpHeight==0)
assert(not pcall(function() h.JumpHeight=0/0 end));assert(h.JumpHeight==0)
assert(not pcall(function() h.UseJumpPower=1 end))
''')

    def test_actual_luau_intent_through_bridge_rises_and_lands(self):
        native=copy.deepcopy(transforms.SNAPSHOT)
        p=next(n for n in native['nodes'] if n['id']==native['local_player'])
        character=p['properties']['Character']['$ref']
        root=next(n for n in native['nodes'] if n.get('parent')==character and n['name']=='HumanoidRootPart')
        root['properties'].update(Anchored=False,Position={'$vector3':[0,3,0]},Size={'$vector3':[2,2,1]})
        root.pop('origins',None);before=copy.deepcopy(native)
        for r in self.run_script(self.prefix+'h.JumpPower=50;h.Jump=true',native):
            b=PhysicsScriptBridge(lambda s:OwnCharacterPhysicsController(s,geometry=GROUND));b.update(native)
            self.assertTrue(b.consume(r['movement_intents'])[0]['accepted'])
            self.assertGreater(b.step(.1)['predicted_state']['position'][1],3)
            for _ in range(6):report=b.step(.1)
            self.assertTrue(report['predicted_state']['grounded']);self.assertEqual(native,before)
            self.assertEqual(report['network_packets_sent'],0)

class PersistentJumpTests(unittest.TestCase):
    def test_persistent_runtime_jump_and_second_landing_without_receipt_mutation(self):
        import os
        import time
        from unittest import mock
        import persistent_runtime
        from test_world_snapshot import fixture
        world,root=fixture()
        world.schema['enums']=[{'name':'PartType'}]
        for spec in [{'name':'CanCollide','type':9},{'name':'size','type':22},
                     {'name':'shape','type':7,'enum':0}]:
            index=len(world.schema['properties']);world.schema['properties'].append(spec)
            world.schema['classes'][3]['properties'].append(index)
        world.instances[root]['properties'].update(Anchored=False,size=[2,2,1],
            CFrame={'position':[0,3,0],'rotation_id':2})
        world.touch(root,['Anchored','size','CFrame'])
        floor=(1,90)
        world.instances[floor]={'class':3,'parent':(1,6),'properties':{
            'CanCollide':True,'Anchored':True,'size':[200,2,200],'shape':1,
            'CFrame':{'position':[0,-1,0],'rotation_id':2}}}
        world.touch(floor,created=True)
        before=copy.deepcopy(world.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:world,123)
            self.addCleanup(runtime.close)
            now=time.monotonic()
            # Keep prediction time deterministic without sleeping between steps.
            with mock.patch.object(persistent_runtime.time,'monotonic',return_value=now):
                result=runtime.execute(NativeJumpTests.prefix+'h.JumpPower=50;h.Jump=true;return true')
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['local_movement']['accepted_intents'],1)
            prediction=runtime.movement_preview
            rising=prediction.advance(world,123,now+.1)
            self.assertGreater(rising['predicted_position'][1],3.)
            for i in range(2,9):landed=prediction.advance(world,123,now+i*.1)
            self.assertTrue(landed['controller']['predicted_state']['grounded'])
            with mock.patch.object(persistent_runtime.time,'monotonic',return_value=now+.9):
                result=runtime.execute(NativeJumpTests.prefix+'assert(h.Jump==false);h.Jump=true;return true')
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['local_movement']['accepted_intents'],2)
            self.assertGreater(prediction.advance(world,123,now+1.)['predicted_position'][1],3.)
            self.assertEqual(world.instances,before)
            self.assertEqual(result['local_movement']['network_packets_sent'],0)

if __name__=='__main__':unittest.main()
