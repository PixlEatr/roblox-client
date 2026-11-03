"""PlatformStand stops Humanoid locomotion, not passive body motion."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

from test_authored_forces import write
from test_physics_controller import intent
from test_authored_rig_clearance import setup, settle
from test_body_velocity import bridge, world
from test_direct_velocity import native_world
from physics_character_states import classify_state, state_signal_events


class PlatformStandingTests(unittest.TestCase):
    def test_stops_walking_and_target_but_keeps_gravity(self):
        for kind in ('Move', 'MoveTo'):
            b=bridge(world(),gravity=100)
            b.consume([intent(1,kind,(10,20,0))]);b.step(.1)
            x=b.controller.controller.position[0]
            b.apply_mutations([write('humanoid','PlatformStand',True)])
            report=b.controller.step(.1)['predicted_state']
            self.assertEqual(report['position'][0],x)
            self.assertLess(report['position'][1],20)
            self.assertEqual(report['humanoid_state']['name'],'PlatformStanding')
            self.assertIsNone(b.controller.target)
            for seq,command in enumerate(('Move','MoveTo','Jump'),2):
                self.assertEqual(b.consume([intent(seq,command)])[0]['reason'],'platform_standing')

    def test_impulses_and_bodyforce_continue(self):
        b=bridge(world(),gravity=100)
        b.apply_mutations([write('humanoid','PlatformStand',True),
            {'kind':'create','node':{'id':'local:force','class_name':'BodyForce','parent':'root',
                'properties':{'Force':{'$vector3':[0,200,0]}}}}])
        out=b.consume([intent(1,'Impulse',(20,0,0),'root')])
        self.assertTrue(out[0]['accepted'],out)
        b.step(.1)
        self.assertAlmostEqual(b.controller.controller.position[0],1)
        self.assertAlmostEqual(b.controller.vertical_position,20)

    def test_floor_uses_body_contact_then_stance_recovers(self):
        b=setup();c=settle(b);self.assertEqual(c.vertical_position,3)
        b.apply_mutations([write('humanoid','PlatformStand',True)])
        self.assertEqual(settle(b).vertical_position,1)
        b.apply_mutations([write('humanoid','PlatformStand',False)])
        self.assertEqual(settle(b).vertical_position,3)
        self.assertTrue(b.consume([intent(1)])[0]['accepted'])

    def test_water_and_ladder_do_not_restore_locomotion(self):
        for field in ('water_regions','ladder_regions'):
            b=bridge(world(),gravity=100)
            b.controller.update_environment(**{field:[{'min':[-100,-100,-100],'max':[100,100,100]}]})
            b.apply_mutations([write('humanoid','PlatformStand',True)])
            state=b.controller.step(.1)['predicted_state']
            self.assertEqual(state['humanoid_state']['name'],'PlatformStanding')
            self.assertAlmostEqual(state['position'][1],19.5)

    def test_knockback_does_not_step_over_a_ledge(self):
        from test_physics_foot_offset import r15_snapshot
        from test_authored_rig_clearance import FLOOR
        from physics_ground import AABB,GroundGeometry
        data=r15_snapshot(hip_height=2)
        data['nodes'][3]['properties']['AssemblyMass']=2
        data['nodes'][3]['origins']['AssemblyMass']='received'
        ledge=AABB((1.5,0,-10),(4,.5,10),'ledge')
        b=setup(data,GroundGeometry((FLOOR,ledge),()))
        b.apply_mutations([write('humanoid','PlatformStand',True)]);settle(b)
        out=b.consume([intent(1,'Impulse',(20,0,0),'root')])
        self.assertTrue(out[0]['accepted'],out)
        b.step(.1)
        self.assertLessEqual(b.controller.controller.position[0],.5)
        self.assertAlmostEqual(b.controller.vertical_position,1)

    def test_change_state_persists_until_explicit_write_or_exit(self):
        data=world();b=bridge(data)
        self.assertTrue(b.consume([intent(1,'ChangeState',state='PlatformStanding')])[0]['accepted'])
        b.update(copy.deepcopy(data));self.assertTrue(b.controller._platform_standing())
        self.assertFalse(b.consume([intent(2,'ChangeState',state='Jumping')])[0]['accepted'])
        self.assertTrue(b.consume([intent(3,'ChangeState',state='Freefall')])[0]['accepted'])
        self.assertFalse(b.controller._platform_standing())
        b.apply_mutations([write('humanoid','PlatformStand',False)])
        b.consume([intent(4,'ChangeState',state='PlatformStanding')])
        b.apply_mutations([write('humanoid','WalkSpeed',20)])
        self.assertTrue(b.controller._platform_standing())
        b.apply_mutations([write('humanoid','PlatformStand',False)])
        self.assertFalse(b.controller._platform_standing())

    def test_receipts_stale_writes_and_replacement(self):
        data=world();original=copy.deepcopy(data);b=bridge(data)
        b.apply_mutations([write('humanoid','PlatformStand',True)])
        self.assertEqual(data,original)
        b.update(copy.deepcopy(data));self.assertTrue(b.controller._platform_standing())
        data['nodes'][2]['properties']['PlatformStand']=False
        data['nodes'][2]['property_revisions']={'PlatformStand':10}
        b.update(data);self.assertFalse(b.controller._platform_standing())
        b.apply_mutations([write('humanoid','PlatformStand',True)],observed_revision=9)
        self.assertFalse(b.controller._platform_standing())
        b.consume([intent(1,'ChangeState',state='PlatformStanding')])
        b.clear();b.update(data);self.assertFalse(b.controller._platform_standing())

    def test_disabled_state_and_dead_precedence(self):
        b=bridge(world());b.controller.update_state_enabled('PlatformStanding',False)
        self.assertFalse(b.consume([intent(1,'ChangeState',state='PlatformStanding')])[0]['accepted'])
        b.apply_mutations([write('humanoid','PlatformStand',True)])
        self.assertFalse(b.controller._platform_standing())
        b.controller.update_state_enabled('PlatformStanding',True)
        self.assertTrue(b.controller._platform_standing())
        self.assertEqual(classify_state(grounded=False,vertical_velocity=0,dead=True,
            platform_standing=True)['name'],'Dead')

    def test_signals_emit_only_on_edges(self):
        b=bridge(world());b.step(0)
        b.apply_mutations([write('humanoid','PlatformStand',True)]);b.step(.01);b.step(.01)
        b.apply_mutations([write('humanoid','PlatformStand',False)]);b.step(.01)
        signals=[e for e in b.controller.feedback_events()['signals'] if e['name']=='PlatformStanding']
        self.assertEqual([e['active'] for e in signals],[True,False])
        self.assertEqual(state_signal_events('PlatformStanding','PlatformStanding'),[])

    def test_running_state_exit_allows_jump_despite_stale_property(self):
        b=setup();b.apply_mutations([write('humanoid','PlatformStand',True)]);settle(b)
        out=b.consume([intent(1,'ChangeState',state='Running')])
        self.assertTrue(out[0]['accepted'],out);settle(b)
        out=b.consume([intent(2,'Jump',jump_settings={'PlatformStand':True,'JumpPower':50})])
        self.assertTrue(out[0]['accepted'],out)


class NativePlatformStandingTests(unittest.TestCase):
    def test_feedback_syncs_property_and_typed_events_without_authored_writes(self):
        import test_joint_poses as poses
        data,_=native_world();host=poses.JointPoseTests.host(self,data)
        humanoid=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
        execute=lambda source:poses.JointPoseTests.execute(self,host,source)
        execute('''
getgenv().h=game:GetService("Players").LocalPlayer.Character.Humanoid
getgenv().changes={}
h.StateChanged:Connect(function(old,new)table.insert(changes,new)end)
''')
        for name,value in [('Freefall',5),('PlatformStanding',14),('Freefall',5)]:
            result=host.call({'op':'physics_feedback','timeout_ms':1000,'feedback':{
                'humanoid_id':humanoid['id'],'state':{'name':name,'value':value}}})
            self.assertTrue(result['ok'],result)
            self.assertFalse(result.get('mutations'),result)
            result=execute('''
assert(h:GetState()==Enum.HumanoidStateType.%s)
assert(h.PlatformStand==%s)
'''%(name,'true' if value==14 else 'false'))
            self.assertFalse(result.get('mutations'),result)
        execute('''
assert(changes[#changes-1]==Enum.HumanoidStateType.PlatformStanding)
assert(changes[#changes]==Enum.HumanoidStateType.Freefall)
''')

    def test_luau_property_and_state_commands_reach_prediction_in_order(self):
        import test_cframe_transforms as transforms
        data,_=native_world()
        for source,expected in [('h.PlatformStand=true',True),
                ('h.PlatformStand=false;h:ChangeState(Enum.HumanoidStateType.PlatformStanding)',True),
                ('h:ChangeState(Enum.HumanoidStateType.PlatformStanding);h.PlatformStand=false',False),
                ('h.PlatformStand=false;h:ChangeState(Enum.HumanoidStateType.PlatformStanding);h.PlatformStand=false',False)]:
            for result in transforms.CFrameTransformTests.run_script(self,
                    'local h=game:GetService("Players").LocalPlayer.Character.Humanoid\n'+source,data):
                b=bridge(data);b.consume_batch(result['mutations'],result['movement_intents'])
                self.assertEqual(b.controller._platform_standing(),expected)

    def test_persistent_runtime_feedback_and_events(self):
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,_=ready_world();original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                r=runtime.execute('''
getgenv().h=game:GetService("Players").LocalPlayer.Character.Humanoid
getgenv().edges={}
h.PlatformStanding:Connect(function(active)table.insert(edges,active)end)
return true
''');self.assertTrue(r['ok'],r)
                b=runtime.movement_preview.bridge;b.step(0);runtime._publish_physics_feedback()
                r=runtime.execute('h.PlatformStand=true;return true');self.assertTrue(r['ok'],r)
                b.step(.02);runtime._publish_physics_feedback()
                r=runtime.execute('''
assert(h:GetState()==Enum.HumanoidStateType.PlatformStanding)
assert(h.PlatformStand)
h.PlatformStand=false
return true
''');self.assertTrue(r['ok'],r)
                b.step(.02);runtime._publish_physics_feedback()
                # Feedback queues callbacks; a scheduler poll delivers them.
                runtime.poll(force=True)
                r=runtime.execute('''
assert(h:GetState()~=Enum.HumanoidStateType.PlatformStanding)
assert(not h.PlatformStand)
assert(#edges==2 and edges[1]==true and edges[2]==false,
    "PlatformStanding edges: "..tostring(#edges)..","..tostring(edges[1])..","..tostring(edges[2]))
return true
''');self.assertTrue(r['ok'],r)
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
