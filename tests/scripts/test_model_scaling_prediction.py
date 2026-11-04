"""Authored Luau scaling must reach the real local physics controller."""
import copy
import math
import unittest

from test_authored_mass_prediction import mass_world
from test_body_velocity import bridge
import test_cframe_transforms as native
from test_physics_controller import intent
from physics_ground import AABB, GroundGeometry


def world():
    data,root=mass_world(native=True)
    root['properties']['Position']={'$vector3':[0,20,0]}
    root['properties']['PivotOffset']={'$cframe':{'position':[0,0,0],
        'rotation':[1,0,0,0,1,0,0,0,1]}}
    root['origins'].update(Position='received',PivotOffset='received')
    h=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
    h['properties'].update(RigType=1,HipHeight=2,WalkSpeed=16,JumpPower=50,
                           JumpHeight=7.2,UseJumpPower=True)
    h.setdefault('origins',{}).update(dict.fromkeys(h['properties'],'received'))
    return data,root,h


SETUP='''
local char=game:GetService("Players").LocalPlayer.Character
local root=char.HumanoidRootPart
char.PrimaryPart=root
root.Size=Vector3.new(2,2,1)
root.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
'''


class ModelScalingPredictionTests(unittest.TestCase):
    run_script=native.CFrameTransformTests.run_script

    def test_scaled_force_and_mass_preserve_acceleration(self):
        data,root,_=world();original=copy.deepcopy(data)
        for result in self.run_script(SETUP+'''
local force=Instance.new("BodyForce",root);force.Force=Vector3.new(0,400,0)
char:ScaleTo(2)
near(root.AssemblyMass,32);vec(force.Force,Vector3.new(0,3200,0))
''',data):
            b=bridge(data,gravity=100)
            b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertEqual(b.controller.assembly_mass,32)
            self.assertEqual(b.controller._force_acceleration(),(0,100,0))
            b.step(.1)
            self.assertAlmostEqual(b.controller.vertical_position,20)
            self.assertAlmostEqual(b.controller.vertical_velocity,0)
            self.assertEqual(data,original)

    def test_velocity_is_scaled_once_and_impulse_uses_scaled_mass(self):
        data,root,_=world()
        for result in self.run_script(SETUP+'''
root.AssemblyLinearVelocity=Vector3.new(4,6,8)
char:ScaleTo(2)
root:ApplyImpulse(Vector3.new(32,64,96))
vec(root.AssemblyLinearVelocity,Vector3.new(8,12,16))
''',data):
            movement=result['movement_intents']
            self.assertEqual([i['kind'] for i in movement],
                             ['SetLinearVelocity','SetLinearVelocity','Impulse'])
            self.assertGreater(movement[1]['mutation_cursor'],movement[0]['mutation_cursor'])
            b=bridge(data)
            outcomes=b.consume_batch(result['mutations'],movement)
            self.assertTrue(all(o['accepted'] for o in outcomes),outcomes)
            self.assertEqual(b.controller.assembly_mass,32)
            b.step(.1)
            self.assertEqual(b.controller.linear_velocity,(9,14,19))

    def test_scaled_jump_settings_drive_takeoff_and_landing(self):
        for use_power in (True,False):
            data,root,h=world()
            source=SETUP+'char.Humanoid.UseJumpPower='+str(use_power).lower()+'\nchar:ScaleTo(2)'
            for result in self.run_script(source,data):
                b=bridge(data,gravity=100)
                b.controller.geometry=GroundGeometry((AABB((-100,-1,-100),(100,0,100),'floor'),),())
                b.consume_batch(result['mutations'],result['movement_intents'])
                c=b.controller
                self.assertAlmostEqual(c.jump_settings['JumpPower'],50*math.sqrt(2))
                self.assertAlmostEqual(c.jump_settings['JumpHeight'],14.4)
                self.assertEqual(c.jump_settings['UseJumpPower'],use_power)
                self.assertEqual(c.controller.walk_speed,32)
                self.assertEqual(c.foot_offset,6)
                for _ in range(100):b.step(.02)
                self.assertTrue(c.grounded)
                baseline=c.vertical_position
                outcomes=b.consume([intent(b.last_sequence+1,'Jump',(0,0,0),h['id'])])
                self.assertTrue(outcomes[0]['accepted'],outcomes)
                expected_speed=50*math.sqrt(2) if use_power else math.sqrt(2*100*14.4)
                self.assertAlmostEqual(c.vertical_velocity,expected_speed)
                peak=baseline
                for _ in range(120):
                    b.step(.02);peak=max(peak,c.vertical_position)
                self.assertAlmostEqual(peak-baseline,expected_speed**2/200,delta=.03)
                self.assertTrue(c.grounded)
                self.assertAlmostEqual(c.vertical_position,baseline)


if __name__=='__main__':unittest.main()
