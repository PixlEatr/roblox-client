"""Spring feedback, own-character movement, and persistent Luau lifecycle."""
import copy
import math
import unittest
from types import SimpleNamespace

from physics_forces import position_servo_values, collect_received_force_sources
from physics_velocity_servo import solve_movers
from test_body_velocity import world, bridge, source
from test_authored_forces import write
from physics_ground import AABB, GroundGeometry
from persistent_runtime import PersistentClientScriptRuntime


def spring(target=(10,20,0), gain=100, damping=20, limits=(4000,)*3):
    return {'id':'spring',**position_servo_values(target,limits,gain,damping)}


def create(target=(10,20,0),gain=100,damping=20,limits=(4000,)*3):
    return {'kind':'create','node':{'id':'local:spring','class_name':'BodyPosition','parent':'root',
        'properties':{'Position':{'$vector3':list(target)},'P':gain,'D':damping,
                      'MaxForce':{'$vector3':list(limits)}}}}


class PositionServoTests(unittest.TestCase):
    def test_implicit_spring_and_damping_equation(self):
        a,forces=solve_movers((0,0,0),[],2,.1,position=(0,20,0),position_sources=[spring()])
        velocity=a[0]*.1;position=.05*velocity
        expected=100*(10-position)-20*velocity
        self.assertAlmostEqual(forces['spring'][0],expected)
        self.assertAlmostEqual(2*a[0],expected)

    def test_damping_only_slows_existing_motion(self):
        a,forces=solve_movers((10,0,0),[],2,.1,position=(0,20,0),position_sources=[spring(gain=0)])
        self.assertAlmostEqual(10+.1*a[0],5)
        self.assertAlmostEqual(forces['spring'][0],-100)

    def test_zero_gain_and_damping_do_not_move(self):
        a,forces=solve_movers((0,0,0),[],2,.1,position=(0,20,0),position_sources=[spring(gain=0,damping=0)])
        self.assertEqual(a,(0,0,0));self.assertEqual(forces['spring'],[0,0,0])

    def test_per_axis_force_caps(self):
        a,forces=solve_movers((0,0,0),[],2,.1,position=(0,0,0),
                             position_sources=[spring((100,100,-100),limits=(4,0,8))])
        self.assertEqual(forces['spring'],[4,0,-8])
        for got,want in zip(a,(2,0,-4)):self.assertAlmostEqual(got,want)

    def test_simultaneous_position_and_velocity_movers_share_force_balance(self):
        a,forces=solve_movers((0,0,0),[source((-10,0,0),gain=100)],2,.1,
            position=(0,20,0),position_sources=[spring(damping=0)])
        self.assertAlmostEqual(a[0],0)
        self.assertAlmostEqual(forces['spring'][0],1000)

    def test_bad_damping_is_rejected(self):
        for value in (-1,math.nan,math.inf,True):
            with self.assertRaises(ValueError):spring(damping=value)

    def test_default_strength_converges_without_exploding(self):
        b=bridge(world());b.apply_mutations([create(gain=10000,damping=1250)])
        for _ in range(180):b.controller.step(1/60)
        self.assertAlmostEqual(b.controller.controller.position[0],10,places=4)
        self.assertLess(abs(b.controller.linear_velocity[0]),.01)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])

    def test_gravity_creates_expected_spring_equilibrium(self):
        b=bridge(world(),gravity=100);b.apply_mutations([create((0,20,0),gain=100,damping=30)])
        for _ in range(300):b.controller.step(1/60)
        self.assertAlmostEqual(b.controller.vertical_position,18,places=3)
        self.assertAlmostEqual(b.controller.feedback_events()['movers'][0]['last_force'][1],200,places=2)

    def test_target_edits_rearm_reached_and_destroy_clears_feedback(self):
        b=bridge(world());b.apply_mutations([create((0,20,0))]);b.controller.step(.1)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])
        b.apply_mutations([write('local:spring','position',{'$vector3':[10,20,0]})]);b.controller.step(.1)
        mover=b.controller.feedback_events()['movers'][0]
        self.assertFalse(mover['reached']);self.assertEqual(mover['target_generation'],1)
        b.apply_mutations([{'kind':'destroy','ids':['local:spring']}]);b.controller.step(.1)
        self.assertEqual(b.controller.feedback_events()['movers'],[])

    def test_unrelated_snapshot_preserves_goal_and_new_revision_replaces_it(self):
        data=world();n=copy.deepcopy(create()['node']);n['id']='spring'
        n['origins']=dict.fromkeys(n['properties'],'received');data['nodes'].append(n)
        b=bridge(data);b.apply_mutations([write('spring','position',{'$vector3':[-10,20,0]})])
        b.update(copy.deepcopy(data));self.assertEqual(b.controller._position_sources[0]['target_position'][0],-10)
        n['property_revisions']={'Position':5};b.update(data)
        self.assertEqual(b.controller._position_sources[0]['target_position'][0],10)

    def test_missing_received_damping_disables_source(self):
        data=world();n=copy.deepcopy(create()['node']);n['id']='spring'
        n['origins']=dict.fromkeys(n['properties'],'received');data['nodes'].append(n)
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)
        n['origins']['D']='class_default'
        self.assertEqual(collect_received_force_sources(data,'root'),[])

    def test_unparenting_and_anchoring_stop_force_prediction(self):
        data=world();b=bridge(data);b.apply_mutations([create()]);b.controller.step(.1)
        b.apply_mutations([write('local:spring','Parent',None)]);b.controller.step(.1)
        self.assertEqual(b.controller.feedback_events()['movers'],[])
        b.apply_mutations([write('local:spring','Parent',{'$ref':'root'})])
        data['nodes'][-1]['properties']['Anchored']=True;b.update(data)
        b.controller.step(.1)
        self.assertEqual(b.controller.feedback_events()['movers'],[])

    def test_same_goal_write_rearms_after_leaving_target(self):
        b=bridge(world());b.apply_mutations([create((0,20,0))]);b.controller.step(.1)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])
        b.controller.controller.resolve_position((10,20,0))
        b.apply_mutations([write('local:spring','Position',{'$vector3':[0,20,0]})])
        b.controller.step(.01)
        self.assertFalse(b.controller.feedback_events()['movers'][0]['reached'])

    def test_position_updates_preserve_received_snapshot(self):
        data=world();original=copy.deepcopy(data);b=bridge(data)
        b.apply_mutations([create()]);b.controller.step(.1)
        self.assertEqual(data,original)

    def test_spring_cannot_pull_through_a_wall(self):
        b=bridge(world());b.apply_mutations([create()])
        b.controller.body_half_size=(.5,.5,.5)
        b.controller.geometry=GroundGeometry((AABB((3,0,-10),(3.1,40,10),'wall'),),())
        for _ in range(120):b.controller.step(1/60)
        self.assertLessEqual(b.controller.controller.position[0],2.50001)
        self.assertFalse(b.controller.feedback_events()['movers'][0]['reached'])

    def test_received_assembly_member_goal_uses_its_position_offset(self):
        data=world();root=data['nodes'][-1]
        root['properties']['CFrame']={'$cframe':{'position':[0,20,0],'rotation':[1,0,0,0,1,0,0,0,1]}}
        root['origins']['CFrame']='received'
        data['nodes'].append({'id':'limb','class_name':'Part','parent':'char',
            'properties':{'Position':{'$vector3':[0,22,0]},'AssemblyRootPart':{'$ref':'root'}},
            'origins':{'Position':'received','AssemblyRootPart':'received'}})
        n=create((0,22,0))['node'];n['id']='spring';n['parent']='limb'
        n['origins']=dict.fromkeys(n['properties'],'received');data['nodes'].append(n)
        b=bridge(data);b.controller.step(.1)
        self.assertAlmostEqual(b.controller.vertical_position,20)
        self.assertEqual(b.controller.feedback_events()['movers'][0]['last_force'],[0,0,0])
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])
        data['nodes'][-2]['origins'].pop('Position')
        b.update(data);self.assertEqual(b.controller._position_sources,())


class NativeBodyPositionTests(unittest.TestCase):
    def test_luau_aliases_and_initial_force(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local mover=Instance.new("BodyPosition")
vec(mover:GetLastForce(),Vector3.zero);vec(mover:lastForce(),Vector3.zero)
local changes=0;mover:GetPropertyChangedSignal("position"):Connect(function()changes+=1 end)
mover.position=Vector3.new(5,6,7);vec(mover.Position,mover.position)
mover.maxForce=Vector3.new(4000,0,4000);vec(mover.MaxForce,mover.maxForce)
assert(typeof(mover.ReachedTarget)=="RBXScriptSignal")
assert(not pcall(function()mover.GetLastForce(Instance.new("Folder"))end))
task.wait(.001);assert(changes==1)
''')

    def test_persistent_movement_force_query_and_reached_event(self):
        import test_cframe_transforms as transforms
        import test_joint_poses as poses
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        host=poses.JointPoseTests.host(self,data)
        def execute(code):return poses.JointPoseTests.execute(self,host,code)
        result=execute('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
getgenv().mover=Instance.new("BodyPosition",root)
mover.Position=root.Position+Vector3.new(3,0,0);mover.P=10000;mover.D=1250
getgenv().reached=0;mover.ReachedTarget:Connect(function()reached+=1 end)
''')
        b=bridge(data);b.apply_mutations(result['mutations'])
        runtime=PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.host=host;runtime.movement_preview=SimpleNamespace(bridge=b,
            report=lambda:{'controller':b.controller.report()})
        runtime.background_budget_ms=1000;runtime._physics_feedback_token=None
        def feedback():runtime._publish_physics_feedback()
        b.controller.step(1/60);feedback()
        execute('assert(mover:GetLastForce().X>0);assert(reached==0)')
        old_movers=b.controller.feedback_events()['movers']
        result=execute('mover.Position=mover.Position')
        state=b.controller.report()['predicted_state']['humanoid_state']
        response=host.call({'op':'physics_feedback','feedback':{
            'humanoid_id':b.controller.humanoid_id,'state':{'name':state['name'],'value':state['value']},
            'transitions':[],'signals':[],'movers':old_movers},'timeout_ms':1000})
        self.assertTrue(response['ok'],response)
        execute('vec(mover:GetLastForce(),Vector3.zero);assert(reached==0)')
        b.apply_mutations(result['mutations']);b.controller.step(1/60);feedback()
        for _ in range(150):b.controller.step(1/60)
        feedback();execute('task.wait(.001);assert(reached==1)')
        feedback();execute('task.wait(.001);assert(reached==1)')
        result=execute('mover.position=mover.Position')
        b.apply_mutations(result['mutations']);b.controller.step(1/60);feedback()
        execute('task.wait(.001);assert(reached==2)')
        response=host.call({'op':'physics_feedback','feedback':{
            'humanoid_id':b.controller.humanoid_id,'state':None},'timeout_ms':1000})
        self.assertTrue(response['ok'],response)
        execute('vec(mover:GetLastForce(),Vector3.zero)')
        result=execute('mover:Destroy()');b.apply_mutations(result['mutations'])
        b.controller.step(1/60);feedback()
        self.assertEqual(b.controller.feedback_events()['movers'],[])


if __name__=='__main__':unittest.main()
