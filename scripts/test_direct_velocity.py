"""Ordered velocity assignments and feedback, without a network session."""
import copy
import unittest
from types import SimpleNamespace

from test_body_velocity import world, bridge
from test_physics_controller import intent
from physics_ground import GroundGeometry, AABB
from persistent_runtime import PersistentClientScriptRuntime
from test_physics_feedback import FakeHost, JournalController


def velocity(sequence, vector, receiver='root'):
    return intent(sequence, 'SetLinearVelocity', vector, receiver)


class DirectVelocityTests(unittest.TestCase):
    def test_assignment_replaces_impulses_and_needs_no_mass(self):
        data=world();data['nodes'][-1]['properties'].pop('AssemblyMass')
        b=bridge(data);b.consume([velocity(1,(10,30,4)),velocity(2,(2,5,-3))])
        self.assertEqual(b.controller.accepted,2)
        b.controller.step(.1)
        self.assertEqual(b.controller.linear_velocity,(2,5,-3))
        self.assertAlmostEqual(b.controller.vertical_position,20.5)

    def test_impulse_order_is_observable(self):
        impulse=intent(1,'Impulse',(20,20,0),'root')
        b=bridge(world());b.consume([impulse,velocity(2,(2,5,0))]);b.controller.step(.1)
        self.assertEqual(b.controller.linear_velocity,(2,5,0))
        b=bridge(world());b.consume([velocity(1,(2,5,0)),{**impulse,'sequence':2}]);b.controller.step(.1)
        self.assertEqual(b.controller.linear_velocity,(12,15,0))

    def test_replaces_walking_velocity_and_zero_stops_motion(self):
        b=bridge(world());b.consume([intent(1),velocity(2,(3,0,4))]);b.controller.step(.1)
        self.assertEqual(b.controller.linear_velocity,(3,0,4))
        b.consume([velocity(3,(0,0,0))]);before=b.controller.controller.position
        b.controller.step(.1)
        for actual,expected in zip(b.controller.controller.position,before):
            self.assertAlmostEqual(actual,expected)
        self.assertEqual(b.controller.linear_velocity,(0,0,0))

    def test_gravity_changes_written_speed_and_landing_clears_it(self):
        b=bridge(world(),gravity=100);c=b.controller
        c.geometry=GroundGeometry((AABB((-100,-1,-100),(100,0,100),'floor'),),())
        c.vertical_position=c.foot_offset;c.grounded=True
        b.consume([velocity(1,(0,50,0))]);self.assertFalse(c.grounded)
        heights=[]
        for _ in range(100):c.step(.02);heights.append(c.vertical_position)
        self.assertGreater(max(heights),c.foot_offset+12)
        self.assertTrue(c.grounded);self.assertEqual(c.vertical_velocity,0)
        self.assertAlmostEqual(c.vertical_position,c.foot_offset)

    def test_collision_clears_written_momentum(self):
        b=bridge(world());c=b.controller;c.body_half_size=(.5,.5,.5)
        c.geometry=GroundGeometry((AABB((3,-100,-100),(3.1,100,100),'wall'),),())
        b.consume([velocity(1,(50,0,0))]);c.step(.2)
        self.assertEqual(c._impulse_velocity,(0,0,0))
        x=c.controller.position[0];c.geometry=GroundGeometry((),());c.step(.1)
        self.assertAlmostEqual(c.controller.position[0],x)

    def test_wrong_receivers_and_invalid_vectors_are_rejected(self):
        for receiver in ('humanoid','player','other'):
            b=bridge(world());self.assertFalse(b.consume([velocity(1,(1,2,3),receiver)])[0]['accepted'])
        for vector in ([True,0,0],[float('nan'),0,0],[513,0,0],[1,2]):
            b=bridge(world());item=velocity(1,(0,0,0));item['vector']=vector
            self.assertFalse(b.consume([item])[0]['accepted'])
            self.assertEqual(b.controller.vertical_velocity,0)

    def test_anchored_and_stale_identity_cannot_move(self):
        data=world();data['nodes'][-1]['properties']['Anchored']=True
        b=bridge(data);self.assertFalse(b.consume([velocity(1,(0,50,0))])[0]['accepted'])
        data=world();b=bridge(data);data['nodes'][-1]['id']='replacement';b.update(data)
        self.assertFalse(b.consume([velocity(1,(0,50,0))])[0]['accepted'])

    def test_replay_rejected_and_received_snapshot_unchanged(self):
        data=world();original=copy.deepcopy(data);b=bridge(data)
        b.consume([velocity(1,(1,2,3))]);b.controller.step(.1)
        with self.assertRaises(ValueError):b.consume([velocity(1,(1,2,3))])
        self.assertEqual(data,original)

    def test_upward_world_velocity_does_not_add_conveyor_momentum(self):
        b=bridge(world());c=b.controller;c.grounded=True;c._ground_source='belt'
        c.update_surface_velocities({'belt':(20,0,0)})
        b.consume([velocity(1,(3,50,4))]);c.step(.1)
        self.assertEqual(c.linear_velocity,(3,50,4))

    def test_root_feedback_deferral_keeps_journal_for_retry(self):
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.stats={};runtime._physics_feedback_token=('old',)
        self.assertFalse(runtime._accept_physics_feedback({'ok':False,
            'error':'physics feedback root is not the current local character'},'failed'))
        self.assertIsNone(runtime._physics_feedback_token)
        self.assertEqual(runtime.stats['physics_feedback_deferred'],1)

    def test_velocity_change_publishes_during_same_freefall_state(self):
        controller=JournalController();controller.root_id='root'
        predicted={'humanoid_state':{'name':'Freefall','value':5},'linear_velocity':[0,10,0]}
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=FakeHost();runtime._physics_feedback_token=None;runtime.background_budget_ms=20
        runtime.movement_preview=SimpleNamespace(bridge=SimpleNamespace(controller=controller),
            report=lambda:{'controller':{'predicted_state':predicted}})
        runtime._publish_physics_feedback();runtime._publish_physics_feedback()
        self.assertEqual(len(runtime.host.commands),1)
        predicted['linear_velocity']=[0,-10,0];runtime._publish_physics_feedback()
        self.assertEqual(len(runtime.host.commands),2)
        self.assertEqual(runtime.host.commands[-1]['feedback']['motion'],{'root_id':'root','linear_velocity':[0,-10,0]})


def native_world():
    import test_cframe_transforms as transforms
    data=copy.deepcopy(transforms.SNAPSHOT)
    root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
    root['properties'].update(AssemblyMass=2,Anchored=False,AssemblyLinearVelocity={'$vector3':[0,0,0]})
    root['properties']['CFrame']['$cframe']['position']=[0,20,0]
    root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
    return data,root


class NativeDirectVelocityTests(unittest.TestCase):
    def test_real_luau_preserves_impulse_assignment_order(self):
        import test_cframe_transforms as transforms
        data,root=native_world()
        for source,expected in [('root:ApplyImpulse(Vector3.new(20,20,0)); root.AssemblyLinearVelocity=Vector3.new(2,5,0)',(2,5,0)),
                                ('root.AssemblyLinearVelocity=Vector3.new(2,5,0); root:ApplyImpulse(Vector3.new(20,20,0))',(12,15,0))]:
            for result in transforms.CFrameTransformTests.run_script(self,
                    'local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart\n'+source,data):
                b=bridge(data);b.apply_mutations(result['mutations']);outcomes=b.consume(result['movement_intents'])
                self.assertTrue(all(o['accepted'] for o in outcomes),outcomes)
                b.controller.step(.1);self.assertEqual(b.controller.linear_velocity,expected)

    def test_invalid_and_full_queue_writes_leave_property_unchanged(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.AssemblyLinearVelocity=Vector3.new(1,2,3)
assert(not pcall(function()root.AssemblyLinearVelocity=Vector3.new(0/0,0,0)end))
assert(not pcall(function()root.AssemblyLinearVelocity=Vector3.new(math.huge,0,0)end))
assert(not pcall(function()root.AssemblyLinearVelocity={}end))
vec(root.AssemblyLinearVelocity,Vector3.new(1,2,3))
for i=2,256 do root.AssemblyLinearVelocity=Vector3.new(i,0,0) end
assert(not pcall(function()root.AssemblyLinearVelocity=Vector3.new(300,0,0)end))
vec(root.AssemblyLinearVelocity,Vector3.new(256,0,0))
''')

    def test_persistent_feedback_updates_readback_without_requeuing(self):
        import test_joint_poses as poses
        data,root=native_world();host=poses.JointPoseTests.host(self,data)
        execute=lambda code:poses.JointPoseTests.execute(self,host,code)
        result=execute('''
getgenv().root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.AssemblyLinearVelocity=Vector3.new(0,50,0)
getgenv().changes=0
root:GetPropertyChangedSignal("AssemblyLinearVelocity"):Connect(function()changes+=1 end)
''')
        b=bridge(data,gravity=100);b.consume(result['movement_intents']);b.controller.step(.1)
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=host;runtime._physics_feedback_token=None;runtime.background_budget_ms=1000
        runtime.movement_preview=SimpleNamespace(bridge=b,report=lambda:{'controller':b.controller.report()})
        runtime._publish_physics_feedback()
        checked=execute('task.wait(.001);near(root.AssemblyLinearVelocity.Y,40);assert(changes>=1)')
        self.assertEqual(checked['movement_intents'],[])
        self.assertEqual(checked['mutations'],[])
        b.controller.step(.1);runtime._publish_physics_feedback()
        execute('near(root.AssemblyLinearVelocity.Y,30)')
        invalid={'humanoid_id':b.controller.humanoid_id,'state':{'name':'Running','value':8},
                 'motion':{'root_id':'missing','linear_velocity':[1,2,3]}}
        response=host.call({'op':'physics_feedback','feedback':invalid,'timeout_ms':1000})
        self.assertFalse(response['ok'])
        execute('near(root.AssemblyLinearVelocity.Y,30)')
        for vector in ([True,0,0],[1000001,0,0],[1,2]):
            invalid['motion']={'root_id':root['id'],'linear_velocity':vector}
            response=host.call({'op':'physics_feedback','feedback':invalid,'timeout_ms':1000})
            self.assertFalse(response['ok'],response)
        execute('near(root.AssemblyLinearVelocity.Y,30)')


if __name__=='__main__':unittest.main()
