"""MoveTo completion is a deferred, acknowledged result of actual prediction."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

from physics_ground import GroundGeometry,AABB
from physics_controller import OwnCharacterPhysicsController
from test_physics_controller import intent
from test_body_velocity import bridge,world
from test_authored_forces import write


def events(b):
    return b.controller.feedback_events()['move_to']


class MoveToCompletionTests(unittest.TestCase):
    def test_arrival_is_latched_once_until_acknowledged(self):
        b=bridge(world());b.consume([intent(1,'MoveTo',(2.5,20,0))])
        b.step(.1);self.assertEqual(events(b),[])
        b.step(.1);self.assertEqual(events(b),[{'sequence':1,'reached':True}])
        self.assertEqual(b.controller.controller.position[0],2)
        self.assertIsNone(b.controller.target)
        for _ in range(40):b.step(.25)
        self.assertEqual(events(b),[{'sequence':1,'reached':True}])
        b.controller.ack_feedback(b.controller.feedback_events()['through_sequence'])
        self.assertEqual(events(b),[])

    def test_zero_speed_times_out_and_stops_at_eight_seconds(self):
        b=bridge(world());b.apply_mutations([write('humanoid','WalkSpeed',0)])
        b.consume([intent(1,'MoveTo',(100,20,0))])
        for _ in range(31):b.step(.25)
        self.assertEqual(events(b),[]);b.step(.25)
        self.assertEqual(events(b),[{'sequence':1,'reached':False}])
        self.assertIsNone(b.controller.target)
        self.assertEqual(b.controller.linear_velocity,(0,0,0))

    def test_collision_is_not_reported_as_arrival(self):
        data=world();root=data['nodes'][3]
        root['properties']['Size']={'$vector3':[2,2,1]};root['origins']['Size']='received'
        b=bridge(data);b.controller.update_geometry(GroundGeometry((AABB((2,0,-5),(3,30,5),'wall'),),()))
        b.consume([intent(1,'MoveTo',(20,20,0))])
        for _ in range(32):b.step(.25)
        self.assertEqual(events(b),[{'sequence':1,'reached':False}])
        self.assertLessEqual(b.controller.controller.position[0],1)

    def test_repeating_move_to_restarts_timeout_without_false_event(self):
        b=bridge(world());b.apply_mutations([write('humanoid','WalkSpeed',0)])
        b.consume([intent(1,'MoveTo',(100,20,0))])
        for _ in range(30):b.step(.25)
        b.consume([intent(2,'MoveTo',(100,20,0))])
        for _ in range(31):b.step(.25)
        self.assertEqual(events(b),[]);b.step(.25)
        self.assertEqual(events(b),[{'sequence':2,'reached':False}])

    def test_move_and_teleport_cancel_without_late_arrival(self):
        for teleport in (False,True):
            b=bridge(world());b.consume([intent(1,'MoveTo',(100,20,0))])
            if teleport:b.apply_mutations([write('root','Position',{'$vector3':[100,20,0]})])
            else:b.consume([intent(2,'Move',(0,0,0))])
            for _ in range(33):b.step(.25)
            self.assertEqual(events(b),[]);self.assertIsNone(b.controller.target)

    def test_later_target_supersedes_previous_request(self):
        b=bridge(world());b.consume([intent(1,'MoveTo',(100,20,0)),intent(2,'MoveTo',(2,20,0))])
        b.step(.2);self.assertEqual(events(b),[{'sequence':2,'reached':True}])

    def test_receipt_refresh_does_not_reset_timeout(self):
        data=world();b=bridge(data);b.apply_mutations([write('humanoid','WalkSpeed',0)])
        b.consume([intent(1,'MoveTo',(100,20,0))])
        for _ in range(32):b.update(copy.deepcopy(data));b.step(.25)
        self.assertEqual(events(b),[{'sequence':1,'reached':False}])

    def test_anchoring_preserves_request_but_does_not_pause_timeout(self):
        b=bridge(world());b.consume([intent(1,'MoveTo',(100,20,0))])
        b.apply_mutations([write('root','Anchored',True)])
        for _ in range(32):b.step(.25)
        self.assertEqual(events(b),[{'sequence':1,'reached':False}])
        self.assertIsNone(b.controller.step(0)['predicted_state'])

    def test_deadline_limits_walking_without_shortening_gravity_step(self):
        b=bridge(world(),gravity=10);b.consume([intent(1,'MoveTo',(10000,20,0))])
        for _ in range(31):b.step(.25)
        b.step(.2);b.step(.2)
        self.assertAlmostEqual(b.controller.controller.position[0],80)
        self.assertAlmostEqual(b.controller.vertical_position,20-5*8.15**2)
        self.assertEqual(events(b),[{'sequence':1,'reached':False}])

    def test_completion_preserves_external_velocity(self):
        b=bridge(world());b.consume([intent(1,'MoveTo',(3,20,0)),intent(2,'Impulse',(20,0,0),'root')])
        b.step(.1)
        self.assertEqual(events(b),[{'sequence':1,'reached':True}])
        self.assertEqual(b.controller.linear_velocity,(10,0,0))
        x=b.controller.controller.position[0];b.step(.1)
        self.assertGreater(b.controller.controller.position[0],x)

    def test_completion_bursts_are_drained_in_bounded_prefixes(self):
        b=bridge(world())
        for seq in range(1,401):
            x=b.controller.controller.position[0]
            b.consume([intent(seq,'MoveTo',(x+2,20,0))]);b.step(.2)
        first=b.controller.feedback_events()
        self.assertEqual([e['sequence'] for e in first['move_to']],list(range(1,257)))
        b.controller.ack_feedback(first['through_sequence'])
        second=b.controller.feedback_events()
        self.assertEqual([e['sequence'] for e in second['move_to']],list(range(257,401)))
        b.controller.ack_feedback(second['through_sequence']);self.assertEqual(events(b),[])

    def test_suspended_feedback_drains_old_state_edges_before_completion(self):
        from types import SimpleNamespace
        from persistent_runtime import PersistentClientScriptRuntime
        from test_physics_feedback import FakeHost
        b=bridge(world());b.step(0)
        for i in range(20):
            b.apply_mutations([write('humanoid','PlatformStand',i%2==0)]);b.step(0)
        b.consume([intent(1,'MoveTo',(100,20,0))])
        b.apply_mutations([write('root','Anchored',True)])
        for _ in range(32):b.step(.25)
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=FakeHost();runtime.background_budget_ms=20;runtime._physics_feedback_token=None
        runtime.movement_preview=SimpleNamespace(bridge=b,report=lambda:{'controller':b.controller.report()})
        for _ in range(20):runtime._publish_physics_feedback()
        results=[event for command in runtime.host.commands for event in command['feedback'].get('move_to',[])]
        self.assertEqual(results,[{'sequence':1,'reached':False}])
        self.assertEqual(events(b),[])


class NativeMoveToCompletionTests(unittest.TestCase):
    def test_signal_surface_in_both_hosts(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self, '''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
local connection=h.MoveToFinished:Connect(function(reached)assert(type(reached)=="boolean")end)
assert(connection.Connected)
connection:Disconnect()
assert(not connection.Connected)
''')

    def test_signal_wait_and_duplicate_feedback(self):
        import test_joint_poses as poses
        from test_direct_velocity import native_world
        data,_=native_world();host=poses.JointPoseTests.host(self,data)
        h=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
        execute=lambda code:poses.JointPoseTests.execute(self,host,code)
        execute('''
getgenv().h=game:GetService("Players").LocalPlayer.Character.Humanoid
getgenv().results={}
getgenv().waited=nil
h.MoveToFinished:Connect(function(reached)table.insert(results,reached)end)
task.spawn(function() waited=h.MoveToFinished:Wait() end)
task.wait()
''')
        command={'op':'physics_feedback','timeout_ms':1000,'feedback':{'humanoid_id':h['id'],
            'state':None,'move_to':[{'sequence':1,'reached':True}]}}
        for _ in range(2):
            r=host.call(command);self.assertTrue(r['ok'],r);self.assertFalse(r.get('mutations'),r)
        host.call({'op':'poll','timeout_ms':1000})
        execute('assert(waited==true and #results==1 and results[1]==true)')
        command['feedback']['move_to']=[{'sequence':2,'reached':False}]
        self.assertTrue(host.call(command)['ok']);host.call({'op':'poll','timeout_ms':1000})
        execute('assert(#results==2 and results[2]==false)')

    def test_bad_feedback_is_rejected_before_any_completion_is_published(self):
        import test_joint_poses as poses
        from test_direct_velocity import native_world
        data,_=native_world();host=poses.JointPoseTests.host(self,data)
        h=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
        execute=lambda code:poses.JointPoseTests.execute(self,host,code)
        execute('''getgenv().count=0
game:GetService("Players").LocalPlayer.Character.Humanoid.MoveToFinished:Connect(function()count+=1 end)''')
        for results in ([{'sequence':1,'reached':True},{'sequence':0,'reached':False}],
                        [{'sequence':1,'reached':1}], [{'sequence':-1,'reached':True}]):
            r=host.call({'op':'physics_feedback','timeout_ms':1000,
                'feedback':{'humanoid_id':h['id'],'state':None,'move_to':results}})
            self.assertFalse(r['ok'],r)
        host.call({'op':'poll','timeout_ms':1000});execute('assert(count==0)')

    def test_persistent_runtime_delivers_actual_arrival_and_timeout(self):
        self._persistent_results()

    def test_persistent_runtime_delivers_timeout_while_anchored(self):
        self._persistent_results(suspended=True)

    def _persistent_results(self,suspended=False):
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,_=ready_world();original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                r=runtime.execute('''
getgenv().h=game:GetService("Players").LocalPlayer.Character.Humanoid
getgenv().results={}
h.MoveToFinished:Connect(function(reached)table.insert(results,reached)end)
h:MoveTo(Vector3.new(3,3,0))
return true
''');self.assertTrue(r['ok'],r)
                b=runtime.movement_preview.bridge
                for _ in range(5):b.step(.1)
                runtime._publish_physics_feedback();runtime.poll(force=True)
                r=runtime.execute('''assert(#results==1 and results[1]==true)
h.WalkSpeed=0;h:MoveTo(Vector3.new(100,3,0));'''+
                    ('h.Parent.HumanoidRootPart.Anchored=true;' if suspended else '')+'return true')
                self.assertTrue(r['ok'],r)
                for _ in range(32):b.step(.25)
                runtime._publish_physics_feedback();runtime.poll(force=True)
                r=runtime.execute('assert(#results==2 and results[2]==false);return true')
                self.assertTrue(r['ok'],r)
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
