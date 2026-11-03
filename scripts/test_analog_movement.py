"""Partial Move input affects speed; MoveTo remains full-speed targeting."""
import copy
import math
import unittest
from movement_controller import Controller
from physics_controller import OwnCharacterPhysicsController
from local_movement_preview import LocalMovementPreview
from test_physics_controller import snapshot, intent
from test_camera_relative_movement import camera_intent, YAW90
import test_authored_physics_properties as authored


class AnalogMovementTests(unittest.TestCase):
    def test_fractional_input_scales_velocity_and_displacement(self):
        for vector,velocity in [((.25,0,0),(4,0,0)),((.3,0,.4),(4.8,0,6.4)),
                                ((-.5,100,0),(-8,0,0)),((1e-9,0,0),(16e-9,0,0))]:
            c=Controller(walk_speed=16);c.set_direction(vector);state=c.step(.1)
            for a,b in zip(state['velocity'],velocity):self.assertAlmostEqual(a,b)
            for a,b in zip(state['position'],velocity):self.assertAlmostEqual(a,b*.1)

    def test_large_diagonals_are_capped_without_overflow(self):
        for x,z in [(1,1),(3,4),(1.7e308,1.7e308)]:
            c=Controller(walk_speed=16);c.set_direction((x,0,z))
            v=c.step(.1)['velocity'];self.assertAlmostEqual(math.hypot(v[0],v[2]),16)
            self.assertTrue(all(math.isfinite(n) for n in v))

    def test_speed_changes_preserve_input_strength_and_zero_can_resume(self):
        c=Controller(walk_speed=16);c.set_direction((.25,0,0))
        self.assertEqual(c.step(0)['velocity'],(4,0,0))
        c.walk_speed=0;self.assertEqual(c.step(.1)['velocity'],(0,0,0))
        c.walk_speed=32;self.assertEqual(c.step(0)['velocity'],(8,0,0))
        c.stop();self.assertEqual(c.step(0)['velocity'],(0,0,0))

    def test_elapsed_time_partition_does_not_change_fractional_motion(self):
        a,b=Controller(),Controller()
        for c in (a,b):c.set_direction((.3,0,.4))
        a.step(.2)
        for _ in range(10):b.step(.02)
        for x,y in zip(a.position,b.position):self.assertAlmostEqual(x,y)

    def test_move_to_nearby_target_uses_full_speed_and_arrives(self):
        for cls in (LocalMovementPreview,OwnCharacterPhysicsController):
            c=cls(snapshot()) if cls==LocalMovementPreview else cls(snapshot(),gravity=0)
            c.consume([intent(1,'MoveTo',(.25,0,0))]);r=c.step(.02)
            position=r['predicted_position'] if cls==LocalMovementPreview else r['predicted_state']['position']
            self.assertEqual(position,[.2,0,0])
            r=c.step(.02);position=r['predicted_position'] if cls==LocalMovementPreview else r['predicted_state']['position']
            # The physics controller accepts arrival within one stud; the
            # older geometry-free preview still walks to the exact point.
            self.assertEqual(position,[.25 if cls==LocalMovementPreview else .2,0,0]);self.assertIsNone(c.target)

    def test_both_adapters_apply_partial_world_relative_move(self):
        state=snapshot();before=copy.deepcopy(state)
        a=LocalMovementPreview(state);b=OwnCharacterPhysicsController(state,gravity=0)
        for c in (a,b):c.consume([intent(1,vector=(.25,0,0))])
        self.assertEqual(a.step(.1)['predicted_position'],[.25,0,0])
        self.assertEqual(b.step(.1)['predicted_state']['position'],[.25,0,0])
        self.assertEqual(state,before)

    def test_camera_rotation_keeps_fractional_strength(self):
        c=OwnCharacterPhysicsController(snapshot(),gravity=0)
        c.consume([camera_intent(vector=(0,0,-.25),rotation=YAW90)])
        state=c.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][0],-.25)
        self.assertAlmostEqual(state['linear_velocity'][0],-2.5)

    def test_camera_pitch_does_not_reduce_input_strength(self):
        angle=math.pi/3;rotation=[1,0,0,0,math.cos(angle),-math.sin(angle),0,math.sin(angle),math.cos(angle)]
        c=OwnCharacterPhysicsController(snapshot(),gravity=0)
        c.consume([camera_intent(vector=(0,0,-.25),rotation=rotation)])
        state=c.step(.1)['predicted_state']
        self.assertAlmostEqual(state['position'][2],-.25)
        self.assertEqual(state['position'][1],0)

    def test_wall_sliding_keeps_partial_tangential_speed(self):
        from physics_ground import GroundGeometry,AABB
        from physics_body_collision import SKIN
        state=snapshot((0,3,0));state['nodes'][-1]['properties']['Size']={'$vector3':[1,2,1]}
        geometry=GroundGeometry((AABB((.75,-100,-100),(1.75,100,100)),),())
        c=OwnCharacterPhysicsController(state,gravity=0,geometry=geometry)
        c.consume([intent(1,vector=(.3,0,.4))])
        state=c.step(.1)['predicted_state']
        # The contact solver deliberately leaves a small separation margin.
        self.assertLessEqual(state['position'][0],.25)
        self.assertAlmostEqual(state['position'][0],.25,delta=SKIN)
        self.assertAlmostEqual(state['position'][2],.4,delta=SKIN)
        self.assertAlmostEqual(state['linear_velocity'][0],0)
        self.assertAlmostEqual(state['linear_velocity'][2],4)


class PersistentAnalogMovementTests(unittest.TestCase):
    setUp=authored.PersistentAuthoredPropertyTests.setUp
    execute_at=authored.PersistentAuthoredPropertyTests.execute_at

    def test_actual_luau_partial_move_and_speed_change(self):
        before=copy.deepcopy(self.world.instances)
        r=self.execute_at(0,'h:Move(Vector3.new(.25,0,0));return true');self.assertTrue(r['ok'],r)
        r=self.runtime.movement_preview.advance(self.world,123,self.now+.1)
        self.assertAlmostEqual(r['predicted_position'][0],.4,places=5)
        r=self.execute_at(.2,'h.WalkSpeed=32;return true');self.assertTrue(r['ok'],r)
        x=r['local_movement']['predicted_position'][0]
        r=self.runtime.movement_preview.advance(self.world,123,self.now+.3)
        self.assertAlmostEqual(r['predicted_position'][0],x+.8,places=5)
        self.assertEqual(self.world.instances,before)

    def test_actual_luau_partial_player_move_uses_camera(self):
        r=self.execute_at(0, '''
workspace.CurrentCamera.CFrame=CFrame.Angles(0,math.pi/2,0)
game:GetService("Players").LocalPlayer:Move(Vector3.new(0,0,-.25),true)
return true
''');self.assertTrue(r['ok'],r)
        r=self.runtime.movement_preview.advance(self.world,123,self.now+.1)
        self.assertAlmostEqual(r['predicted_position'][0],-.4,places=5)
        self.assertAlmostEqual(r['predicted_position'][2],0,places=5)

if __name__=='__main__':unittest.main()
