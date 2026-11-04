"""Offline feedback, force integration, and actual Luau mutation coverage."""
import copy
import math
import unittest

from physics_velocity_servo import servo_acceleration
from physics_forces import velocity_servo_values, collect_received_force_sources
from physics_body_collision import step_vertical_accelerated
from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, GroundGeometry
from physics_script_bridge import PhysicsScriptBridge
from test_physics_controller import snapshot
from test_authored_forces import write

EMPTY=GroundGeometry((),())


def source(target=(10,0,0),limits=(4000,4000,4000),gain=1250):
    return velocity_servo_values(target,limits,gain)


def world():
    data=snapshot((0,20,0))
    data['nodes'][-1]['properties']['AssemblyMass']=2
    data['nodes'][-1]['origins']={'AssemblyMass':'received','Anchored':'received','Position':'received'}
    return data


def bridge(data,gravity=0):
    b=PhysicsScriptBridge(lambda state:OwnCharacterPhysicsController(state,geometry=EMPTY,gravity=gravity))
    b.update(data)
    return b


class VelocityServoTests(unittest.TestCase):
    def test_unlimited_feedback_matches_implicit_solution(self):
        result=servo_acceleration((0,0,0),[source(limits=(math.inf,)*3,gain=20)],2,.1)
        self.assertAlmostEqual(result[0]*.1,5)

    def test_high_gain_does_not_overshoot(self):
        result=servo_acceleration((0,0,0),[source(limits=(math.inf,)*3,gain=1e30)],2,.25)
        self.assertAlmostEqual(result[0]*.25,10)
        self.assertLessEqual(result[0]*.25,10)

    def test_caps_are_independent_per_axis(self):
        result=servo_acceleration((0,0,0),[source((100,-100,100),(4,0,8))],2,.1)
        for actual,expected in zip(result,(2,0,4)):self.assertAlmostEqual(actual,expected)

    def test_opposing_controllers_and_gravity_equilibrium(self):
        sources=[source((10,0,0),gain=100),source((-10,0,0),gain=100)]
        result=servo_acceleration((0,-1,0),sources,2,.1,(0,-100,0))
        self.assertAlmostEqual(result[0],0)
        self.assertAlmostEqual(result[1],100)

    def test_bad_parameters_and_infinite_force(self):
        self.assertEqual(source(limits=({'$number':'inf'},0,0))['max_force'][0],math.inf)
        for limits in [(-1,0,0),(math.nan,0,0),(True,0,0)]:
            with self.assertRaises(ValueError):source(limits=limits)
        for gain in [-1,math.inf,math.nan,True]:
            with self.assertRaises(ValueError):source(gain=gain)

    def test_received_fields_and_ownership_required(self):
        data=world()
        n={'id':'servo','class_name':'BodyVelocity','parent':'root',
           'properties':{'P':1250,'Velocity':{'$vector3':[10,0,0]},'MaxForce':{'$vector3':[4000]*3}},
           'origins':dict.fromkeys(['P','Velocity','MaxForce'],'received')}
        data['nodes'].append(n)
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)
        n['origins']['P']='class_default'
        self.assertEqual(collect_received_force_sources(data,'root'),[])
        n['origins']['P']='received';n['parent']='another-root'
        self.assertEqual(collect_received_force_sources(data,'root'),[])

    def test_local_lifecycle_alias_receipt_and_destroy(self):
        data=world();b=bridge(data)
        create={'kind':'create','node':{'id':'local:servo','class_name':'BodyVelocity','parent':'root',
            'properties':{'P':1250,'Velocity':{'$vector3':[10,0,0]},'MaxForce':{'$vector3':[4000]*3}}}}
        b.apply_mutations([create]);b.controller.step(.1)
        self.assertGreater(b.controller.linear_velocity[0],9)
        self.assertLess(b.controller.linear_velocity[0],10)
        b.apply_mutations([write('local:servo','velocity',{'$vector3':[-10,0,0]})])
        b.update(copy.deepcopy(data));b.controller.step(.1)
        self.assertLess(b.controller.linear_velocity[0],-9)
        b.apply_mutations([{'kind':'destroy','ids':['local:servo']}])
        self.assertEqual(b.controller._velocity_sources,())

    def test_received_alias_override_expires_on_canonical_revision(self):
        data=world();data['nodes'].append({'id':'servo','class_name':'BodyVelocity','parent':'root',
            'properties':{'P':1250,'Velocity':{'$vector3':[10,0,0]},'MaxForce':{'$vector3':[4000]*3}},
            'origins':dict.fromkeys(['P','Velocity','MaxForce'],'received')})
        b=bridge(data);b.apply_mutations([write('servo','velocity',{'$vector3':[-10,0,0]})])
        self.assertEqual(b.controller._velocity_sources[0]['target_velocity'][0],-10)
        data['nodes'][-1]['property_revisions']={'Velocity':5};b.update(data)
        self.assertEqual(b.controller._velocity_sources[0]['target_velocity'][0],10)

    def test_balanced_bodyforce_does_not_drift(self):
        b=bridge(world(),gravity=100)
        b.apply_mutations([{'kind':'create','node':{'id':'local:force','class_name':'BodyForce',
            'parent':'root','properties':{'Force':{'$vector3':[0,200,0]}}}}])
        for _ in range(60):b.controller.step(1/60)
        self.assertAlmostEqual(b.controller.vertical_position,20)
        self.assertAlmostEqual(b.controller.vertical_velocity,0)

    def test_removing_hover_force_restores_gravity(self):
        b=bridge(world(),gravity=100)
        b.apply_mutations([{'kind':'create','node':{'id':'local:force','class_name':'BodyForce',
            'parent':'root','properties':{'Force':{'$vector3':[0,200,0]}}}}])
        b.controller.step(.1)
        b.apply_mutations([{'kind':'destroy','ids':['local:force']}])
        b.controller.step(.1)
        self.assertAlmostEqual(b.controller.vertical_position,19.5)
        self.assertAlmostEqual(b.controller.vertical_velocity,-10)

    def test_zero_vertical_cap_does_not_cancel_gravity(self):
        b=bridge(world(),gravity=100)
        b.apply_mutations([{'kind':'create','node':{'id':'local:servo','class_name':'BodyVelocity',
            'parent':'root','properties':{'P':1250,'Velocity':{'$vector3':[10,100,0]},
                                        'MaxForce':{'$vector3':[4000,0,4000]}}}}])
        b.controller.step(.1)
        self.assertAlmostEqual(b.controller.vertical_position,19.5)
        self.assertAlmostEqual(b.controller.vertical_velocity,-10)


class AcceleratedVerticalTests(unittest.TestCase):
    def test_upward_force_lifts_from_grounded_support(self):
        floor=GroundGeometry((AABB((-10,-1,-10),(10,0,10),'floor'),),())
        step,_=step_vertical_accelerated((0,3,0),0,.1,floor,True,acceleration=100)
        self.assertAlmostEqual(step.position[1],3.5)
        self.assertAlmostEqual(step.vertical_velocity,10)
        self.assertFalse(step.grounded)

    def test_upward_force_has_correct_displacement(self):
        step,_=step_vertical_accelerated((0,10,0),0,.2,EMPTY,False,acceleration=100)
        self.assertAlmostEqual(step.position[1],12)
        self.assertAlmostEqual(step.vertical_velocity,20)

    def test_descend_then_rise_sweeps_both_segments(self):
        step,_=step_vertical_accelerated((0,10,0),-10,.2,EMPTY,False,acceleration=100)
        self.assertAlmostEqual(step.position[1],10)
        self.assertAlmostEqual(step.vertical_velocity,10)

    def test_floor_impact_then_force_lifts_off(self):
        floor=GroundGeometry((AABB((-10,-1,-10),(10,0,10),'floor'),),())
        step,_=step_vertical_accelerated((0,3.1,0),-10,.2,floor,False,acceleration=100)
        self.assertGreater(step.position[1],3)
        self.assertGreater(step.vertical_velocity,0)
        self.assertFalse(step.grounded)

    def test_ceiling_stops_upward_force(self):
        ceiling=GroundGeometry((AABB((-10,12,-10),(10,13,10),'ceiling'),),())
        step,hit=step_vertical_accelerated((0,10,0),0,.2,ceiling,False,
            acceleration=100,half_size=(.5,.5,.5))
        self.assertLessEqual(step.position[1],11.5)
        self.assertEqual(step.vertical_velocity,0)
        self.assertIsNotNone(hit)

    def test_upward_terminal_speed_integrates_remaining_time(self):
        step,_=step_vertical_accelerated((0,10,0),9,.2,EMPTY,False,
            acceleration=100,max_fall_speed=10)
        self.assertAlmostEqual(step.position[1],11.995)
        self.assertEqual(step.vertical_velocity,10)


class NativeBodyVelocityTests(unittest.TestCase):
    def test_persistent_luau_edit_and_destroy(self):
        import test_cframe_transforms as transforms
        import test_joint_poses as poses
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        host=poses.JointPoseTests.host(self,data)
        def execute(code):return poses.JointPoseTests.execute(self,host,code)
        result=execute('''
getgenv().mover=Instance.new("BodyVelocity",game:GetService("Players").LocalPlayer.Character.HumanoidRootPart)
mover.Velocity=Vector3.new(10,0,0);mover.MaxForce=Vector3.new(4000,0,4000)
''')
        b=bridge(data);b.apply_mutations(result['mutations']);b.controller.step(.1)
        self.assertGreater(b.controller.linear_velocity[0],9)
        result=execute('mover.velocity=Vector3.new(-10,0,0)')
        b.apply_mutations(result['mutations']);b.controller.step(.1)
        self.assertLess(b.controller.linear_velocity[0],-9)
        result=execute('mover:Destroy()');b.apply_mutations(result['mutations'])
        self.assertEqual(b.controller._velocity_sources,())

    def test_real_luau_aliases_queries_and_prediction(self):
        import test_cframe_transforms as transforms
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        results=transforms.CFrameTransformTests.run_script(self,'''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local mover=Instance.new("BodyVelocity",root)
local changes=0;mover:GetPropertyChangedSignal("velocity"):Connect(function()changes+=1 end)
mover.velocity=Vector3.new(10,0,0);vec(mover.Velocity,mover.velocity)
mover.maxForce=Vector3.new(4000,0,4000);vec(mover.MaxForce,mover.maxForce)
mover.P=1250
vec(mover:GetLastForce(),Vector3.zero);vec(mover:lastForce(),Vector3.zero)
assert(not pcall(function()mover.GetLastForce(root)end))
task.wait(.001);assert(changes==1)
''',data)
        for result in results:
            b=bridge(data);b.apply_mutations(result['mutations']);b.controller.step(.1)
            self.assertGreater(b.controller.linear_velocity[0],9)
            self.assertLess(b.controller.linear_velocity[0],10)


if __name__=='__main__':unittest.main()
