"""Continuous target proximity and its own-character feedback integration."""
import math
import copy
import random
import unittest

from physics_target_sweep import curve_reaches_target
from physics_ground import AABB, GroundGeometry
from test_body_velocity import world, bridge
from test_body_position import create


class TargetCurveTests(unittest.TestCase):
    def test_fast_crossing_between_endpoints(self):
        self.assertTrue(curve_reaches_target((0,0,0),(100,0,0),(0,0,0),.1,(5,0,0)))

    def test_parabola_reaches_apex_target(self):
        self.assertTrue(curve_reaches_target((0,0,0),(50,10,0),(0,-100,0),.2,(5,.5,0)))

    def test_straight_chord_does_not_substitute_for_parabola(self):
        self.assertFalse(curve_reaches_target((0,0,0),(50,10,0),(0,-100,0),.2,(5,0,0)))

    def test_tangent_and_near_miss(self):
        self.assertTrue(curve_reaches_target((0,0,0),(100,0,0),(0,0,0),.1,(5,.1,0)))
        self.assertFalse(curve_reaches_target((0,0,0),(100,0,0),(0,0,0),.1,(5,.100001,0)))

    def test_turnaround_even_when_endpoints_coincide(self):
        self.assertTrue(curve_reaches_target((1,0,0),(-20,0,0),(200,0,0),.2,(0,0,0),radius=0))

    def test_stationary_path_and_zero_time(self):
        self.assertTrue(curve_reaches_target((0,0,0),(0,0,0),(0,0,0),.2,(0,.1,0)))
        self.assertFalse(curve_reaches_target((0,0,0),(100,0,0),(0,0,0),0,(5,0,0)))

    def test_three_stationary_points_do_not_hide_the_second_contact(self):
        # x=1-12t+12t^2 crosses zero twice; all extrema must be considered.
        self.assertTrue(curve_reaches_target((1,.05,0),(-60,0,0),(600,0,0),.2,(0,0,0)))

    def test_nonfinite_input_is_rejected(self):
        with self.assertRaises(ValueError):
            curve_reaches_target((0,math.nan,0),(0,0,0),(0,0,0),.1,(0,0,0))

    def test_known_interior_points_on_varied_three_dimensional_curves(self):
        rng=random.Random(1357)
        for _ in range(100):
            start=tuple(rng.uniform(-100,100) for _ in range(3))
            velocity=tuple(rng.uniform(-500,500) for _ in range(3))
            acceleration=tuple(rng.uniform(-2000,2000) for _ in range(3))
            dt=rng.uniform(.001,.25);t=rng.uniform(.01,.99)*dt
            target=tuple(start[i]+velocity[i]*t+.5*acceleration[i]*t*t for i in range(3))
            self.assertTrue(curve_reaches_target(start,velocity,acceleration,dt,target,radius=1e-6))


class TargetSweepIntegrationTests(unittest.TestCase):
    def test_fast_mover_crossing_is_latched_until_feedback(self):
        b=bridge(world());b.apply_mutations([create((5,20,0),gain=0,damping=0)])
        b.controller._impulse_velocity=(50,0,0)
        b.controller.step(.2)
        self.assertAlmostEqual(b.controller.controller.position[0],10)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])
        b.controller.step(.2)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])

    def test_ballistic_crossing_and_chord_false_positive(self):
        for target_y,expected in [(20.5,True),(20,False)]:
            b=bridge(world(),gravity=100)
            b.apply_mutations([create((5,target_y,0),gain=0,damping=0)])
            b.controller._impulse_velocity=(50,0,0);b.controller.vertical_velocity=10
            b.controller.step(.2)
            self.assertEqual(b.controller.feedback_events()['movers'][0]['reached'],expected)

    def test_wall_blocks_unrealized_target_crossing(self):
        b=bridge(world());b.apply_mutations([create((5,20,0),gain=0,damping=0)])
        b.controller.body_half_size=(.5,.5,.5)
        b.controller.geometry=GroundGeometry((AABB((3,0,-10),(3.1,40,10),'wall'),),())
        b.controller._impulse_velocity=(50,0,0);b.controller.step(.2)
        self.assertLess(b.controller.controller.position[0],3)
        self.assertFalse(b.controller.feedback_events()['movers'][0]['reached'])

    def test_starting_at_target_counts_even_if_later_motion_hits_a_wall(self):
        b=bridge(world());b.apply_mutations([create((0,20,0),gain=0,damping=0)])
        b.controller.body_half_size=(.5,.5,.5)
        b.controller.geometry=GroundGeometry((AABB((3,0,-10),(3.1,40,10),'wall'),),())
        b.controller._impulse_velocity=(50,0,0);b.controller.step(.2)
        self.assertGreater(b.controller.controller.position[0],1)
        self.assertTrue(b.controller.feedback_events()['movers'][0]['reached'])


class TargetSweepNativeTests(unittest.TestCase):
    def test_script_receives_crossing_once_after_several_physics_steps(self):
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
mover.Position=root.Position+Vector3.new(5,0,0);mover.P=0;mover.D=0
getgenv().hits=0;mover.ReachedTarget:Connect(function()hits+=1 end)
''')
        b=bridge(data);b.apply_mutations(result['mutations'])
        b.controller._impulse_velocity=(50,0,0)
        b.controller.step(.2);b.controller.step(.2)
        journal=b.controller.feedback_events()
        payload={'humanoid_id':b.controller.humanoid_id,'state':journal['state'],
                 'transitions':journal['transitions'],'signals':journal['signals'],'movers':journal['movers']}
        for _ in range(2):
            response=host.call({'op':'physics_feedback','feedback':payload,'timeout_ms':1000})
            self.assertTrue(response['ok'],response)
            execute('task.wait(.001);assert(hits==1)')


if __name__=='__main__':unittest.main()
