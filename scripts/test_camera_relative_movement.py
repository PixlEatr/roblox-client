"""Camera-relative Luau Move requests retain the camera used at call time."""
import copy
import math
import unittest

from physics_controller import OwnCharacterPhysicsController
from physics_script_bridge import PhysicsScriptBridge
from local_movement_preview import LocalMovementPreview
from test_physics_controller import snapshot, intent
import test_authored_physics_properties as authored_tests

IDENTITY = [1,0,0,0,1,0,0,0,1]
YAW90 = [0,0,1,0,1,0,-1,0,0]


def camera_intent(sequence=1, vector=(0,0,-1), rotation=None, **extra):
    return intent(sequence, vector=vector, relative_to_camera=True,
                  camera={'instance_id':'camera', 'origin':'local',
                          'rotation':list(IDENTITY if rotation is None else rotation)}, **extra)


class CameraMovementTests(unittest.TestCase):
    def controller(self):
        return OwnCharacterPhysicsController(snapshot(), gravity=0)

    def test_camera_yaw_rotates_forward_right_and_back(self):
        for direction, expected in [((0,0,-1),(-1,0,0)), ((1,0,0),(0,0,-1)),
                                    ((0,0,1),(1,0,0)), ((-1,0,0),(0,0,1))]:
            with self.subTest(direction=direction):
                c=self.controller();c.consume([camera_intent(vector=direction,rotation=YAW90)])
                state=c.step(.1)
                self.assertEqual(state['accepted_intents'],1)
                for actual,wanted in zip(state['predicted_state']['position'],expected):
                    self.assertAlmostEqual(actual,wanted)

    def test_camera_pitch_does_not_turn_ground_walking_into_flying(self):
        a=math.pi/4;rotation=[1,0,0,0,math.cos(a),-math.sin(a),0,math.sin(a),math.cos(a)]
        c=self.controller();c.consume([camera_intent(rotation=rotation)])
        self.assertEqual(c.step(.1)['predicted_state']['position'],[0,0,-1])

    def test_zero_direction_stops_camera_relative_walk(self):
        c=self.controller();c.consume([camera_intent()]);c.step(.1)
        c.consume([camera_intent(2,vector=(0,0,0))])
        self.assertEqual(c.step(.1)['predicted_state']['position'],[0,0,-1])

    def test_stopping_does_not_require_unavailable_camera_rotation(self):
        c=self.controller();c.consume([camera_intent()]);c.step(.1)
        stop=camera_intent(2,vector=(0,0,0));stop.pop('camera')
        c.consume([stop]);state=c.step(.1)
        self.assertEqual(state['accepted_intents'],2)
        self.assertEqual(state['predicted_state']['position'],[0,0,-1])

    def test_vertical_projection_does_not_amplify_roundoff_to_full_speed(self):
        rotation=[1,0,0,0,0,-1,0,1,1e-16]
        c=self.controller();c.consume([camera_intent(rotation=rotation)])
        state=c.step(.1)
        self.assertEqual(state['predicted_state']['position'],[0,0,0])

    def test_missing_nonrigid_nonfinite_and_unknown_camera_do_not_move(self):
        for camera in (None,{}, {'rotation':IDENTITY},
                       {'instance_id':'camera','origin':'local','rotation':[2,0,0,0,1,0,0,0,1]},
                       {'instance_id':'camera','origin':'unavailable','rotation':IDENTITY},
                       {'instance_id':'camera','origin':'local','rotation':[float('nan')]*9}):
            with self.subTest(camera=camera):
                c=self.controller();request=camera_intent();request['camera']=camera
                c.consume([request]);state=c.step(.1)
                self.assertEqual(state['rejected_intents'],1)
                self.assertEqual(state['predicted_state']['position'],[0,0,0])

    def test_world_relative_move_ignores_camera_metadata(self):
        c=self.controller();request=camera_intent(vector=(1,0,0),rotation=YAW90)
        request['relative_to_camera']=False;c.consume([request])
        self.assertEqual(c.step(.1)['predicted_state']['position'],[1,0,0])

    def test_bridge_keeps_request_identity_and_received_snapshot(self):
        state=snapshot();before=copy.deepcopy(state);b=PhysicsScriptBridge(
            lambda s:OwnCharacterPhysicsController(s,gravity=0));b.update(state)
        request=camera_intent(rotation=YAW90);original=copy.deepcopy(request)
        result=b.consume([request]);self.assertTrue(result[0]['accepted'])
        self.assertEqual(b.step(.1)['predicted_state']['position'],[-1,0,0])
        self.assertEqual(request,original);self.assertEqual(state,before)
        self.assertEqual(b.last_sequence,1)
        with self.assertRaises(ValueError):b.consume([request])

    def test_legacy_preview_uses_the_same_captured_camera(self):
        preview=LocalMovementPreview(snapshot());preview.consume([camera_intent(rotation=YAW90)])
        self.assertEqual(preview.step(.1)['predicted_position'],[-1,0,0])


class NativeCameraMovementTests(unittest.TestCase):
    def run_script(self,source,snapshot=None):
        import test_cframe_transforms as transforms
        return transforms.CFrameTransformTests.run_script(self,source,snapshot)

    def test_each_call_captures_its_camera_and_preserves_original_direction(self):
        results=self.run_script('''
local p=game:GetService("Players").LocalPlayer
local h=p.Character.Humanoid
local first=workspace.CurrentCamera
first.CFrame=CFrame.new(500,600,700)*CFrame.Angles(0,math.pi/2,0)
h:Move(Vector3.new(0,0,-1),true)
local second=Instance.new("Camera",workspace)
second.CFrame=CFrame.identity;workspace.CurrentCamera=second
p:Move(Vector3.xAxis,true)
second.CFrame=CFrame.Angles(0,-math.pi/2,0)
h:Move(Vector3.zAxis,false)
''')
        for result in results:
            first,second,third=result['movement_intents']
            self.assertEqual(first['vector'],[0,0,-1]);self.assertTrue(first['relative_to_camera'])
            for actual,wanted in zip(first['camera']['rotation'],YAW90):self.assertAlmostEqual(actual,wanted,places=6)
            self.assertEqual(second['camera']['rotation'],IDENTITY)
            self.assertNotEqual(first['camera']['instance_id'],second['camera']['instance_id'])
            self.assertNotEqual(first['instance_id'],second['instance_id'])
            self.assertNotIn('camera',third)

    def test_failed_calls_do_not_consume_sequence_or_queue_intents(self):
        for result in self.run_script('''
local h=game:GetService("Players").LocalPlayer.Character.Humanoid
assert(not pcall(function()h:Move("bad",true)end))
assert(not pcall(function()h:Move(Vector3.xAxis,true,4)end))
h:Move(Vector3.zero,true)
'''):
            self.assertEqual(len(result['movement_intents']),1)
            self.assertEqual(result['movement_intents'][0]['sequence'],1)
            self.assertIn('camera',result['movement_intents'][0])

    def test_unknown_camera_rotation_is_not_replaced_with_identity(self):
        import test_cframe_transforms as transforms
        state=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in state['nodes'] if n['name']=='HumanoidRootPart')
        root['properties']['CFrame']={'$cframe':{'position':[1,2,3]}}
        for result in self.run_script('''
local character=game:GetService("Players").LocalPlayer.Character
workspace.CurrentCamera.CFrame=character.HumanoidRootPart.CFrame
character.Humanoid:Move(Vector3.xAxis,true)
character.Humanoid:Move(Vector3.xAxis,false)
''',state):
            self.assertEqual(len(result['movement_intents']),2)
            for request in result['movement_intents']:
                self.assertNotIn('camera',request)


class PersistentCameraMovementTests(unittest.TestCase):
    setUp=authored_tests.PersistentAuthoredPropertyTests.setUp
    execute_at=authored_tests.PersistentAuthoredPropertyTests.execute_at

    def test_actual_luau_camera_turn_and_player_move_reach_physics(self):
        before=copy.deepcopy(self.world.instances)
        result=self.execute_at(0, '''
workspace.CurrentCamera.CFrame=CFrame.Angles(0,math.pi/2,0)
h:Move(Vector3.new(0,0,-1),true)
workspace.CurrentCamera.CFrame=CFrame.identity
return true
''');self.assertTrue(result['ok'],result)
        result=self.runtime.movement_preview.advance(self.world,123,self.now+.1)
        self.assertAlmostEqual(result['predicted_position'][0],-1.6,places=5)
        self.assertAlmostEqual(result['predicted_position'][2],0,places=5)
        result=self.execute_at(.2, 'game:GetService("Players").LocalPlayer:Move(Vector3.new(0,0,-1),true);return true')
        x=result['local_movement']['predicted_position'][0]
        result=self.runtime.movement_preview.advance(self.world,123,self.now+.3)
        self.assertAlmostEqual(result['predicted_position'][0],x,places=5)
        self.assertAlmostEqual(result['predicted_position'][2],-1.6,places=5)
        self.assertEqual(self.world.instances,before)

    def test_nonrigid_camera_is_rejected_without_fabricating_a_transform(self):
        result=self.execute_at(0, '''
workspace.CurrentCamera.CFrame=CFrame.new(0,0,0,2,0,0,0,1,0,0,0,1)
h:Move(Vector3.xAxis,true);return true
''');self.assertTrue(result['ok'],result)
        self.assertEqual(result['local_movement']['rejected_intents'],1)
        result=self.runtime.movement_preview.advance(self.world,123,self.now+.1)
        self.assertAlmostEqual(result['predicted_position'][0],0)

if __name__=='__main__':unittest.main()
