"""Rotated block geometry and authored movement, with no network connection."""
import copy
import json
import math
from pathlib import Path
import unittest
from unittest import mock

import local_runtime
import movement_collision
import physics_ground
from physics_controller import OwnCharacterPhysicsController
from physics_ground import extract_ground, ground_raycast, step_vertical
from test_physics_ground import make_world, part
from test_physics_controller import snapshot, intent


def rotation_z(angle):
    c,s=math.cos(angle),math.sin(angle)
    return (c,-s,0,s,c,0,0,0,1)


def ramp_world(angle=math.pi/6, size=(20,2,10)):
    return make_world({'ramp':part(CanCollide=True,Anchored=True,Size=size,
        CFrame={'position':[0,0,0],'rotation':rotation_z(angle)})})


class RotatedGroundTests(unittest.TestCase):
    def test_ray_uses_rotated_faces_and_world_distances(self):
        angle=math.pi/6
        hit=movement_collision.ray_obb((2,10,0),(0,-4,0),(0,0,0),(10,1,5),rotation_z(angle),20)
        y=2*math.tan(angle)+1/math.cos(angle)
        self.assertAlmostEqual(hit.distance,10-y)
        for actual,expected in zip(hit.normal,(-math.sin(angle),math.cos(angle),0)):
            self.assertAlmostEqual(actual,expected)
        self.assertIsNone(movement_collision.ray_obb((2,10,0),(0,-1,0),(0,0,0),(10,1,5),rotation_z(angle),1))

    def test_rotated_thin_box_does_not_fill_its_bounding_box_corners(self):
        c=math.sqrt(.5);matrix=(c,0,c,0,1,0,-c,0,c)
        self.assertIsNone(movement_collision.ray_obb((3,10,3),(0,-1,0),(0,0,0),(5,1,.2),matrix,20))
        self.assertIsNotNone(movement_collision.ray_obb((3,10,-3),(0,-1,0),(0,0,0),(5,1,.2),matrix,20))

    def test_surface_roundoff_and_inside_origin_are_distinguished(self):
        angle=.43;matrix=rotation_z(angle);top=(-math.sin(angle),math.cos(angle),0)
        hit=movement_collision.ray_obb(top,(0,-1,0),(0,0,0),(4,1,3),matrix,.1)
        self.assertAlmostEqual(hit.distance,0,places=8)
        self.assertGreater(hit.normal[1],.5)
        inside=movement_collision.ray_obb((0,0,0),(0,-1,0),(0,0,0),(4,1,3),matrix,.1)
        self.assertEqual(inside.normal,(0,0,0))

    def test_invalid_matrices_are_not_silently_normalized(self):
        bad=[(2,0,0,0,1,0,0,0,1),(-1,0,0,0,1,0,0,0,1),
             (1,.2,0,0,1,0,0,0,1),(math.nan,0,0,0,1,0,0,0,1),
             (True,0,0,0,1,0,0,0,1)]
        for matrix in bad:
            with self.subTest(matrix=matrix),self.assertRaises(ValueError):
                movement_collision.ray_obb((0,10,0),(0,-1,0),(0,0,0),(4,1,3),matrix,20)

    def test_flat_and_nested_received_matrices_create_exact_rotated_colliders(self):
        for nested in [False,True]:
            world=ramp_world();before=copy.deepcopy(world.instances)
            matrix=world.instances['ramp']['properties']['CFrame']['rotation']
            if nested:
                world.instances['ramp']['properties']['CFrame']['rotation']=[matrix[i:i+3] for i in (0,3,6)]
                before=copy.deepcopy(world.instances)
            geometry=extract_ground(world)
            self.assertEqual(len(geometry.colliders),1)
            self.assertFalse(geometry.unsupported)
            self.assertEqual(world.instances,before)
            hit=ground_raycast((2,10,0),geometry,foot_offset=0,max_distance=20)
            self.assertAlmostEqual(hit.y,2*math.tan(math.pi/6)+1/math.cos(math.pi/6))

    def test_malformed_rotation_does_not_fall_back_to_older_valid_pose(self):
        world=ramp_world()
        world.instances['ramp']['properties']['ReplicationPV']={'position':[0,0,0],
            'position_present':True,'rotation':[2,0,0,0,1,0,0,0,1]}
        world.property_versions['ramp']={'CFrame':1,'ReplicationPV':2}
        geometry=extract_ground(world)
        self.assertFalse(geometry.colliders)
        self.assertIn('rotation',dict(geometry.unsupported)['ramp'])

    def test_fall_lands_and_remains_supported_on_ramp(self):
        geometry=extract_ground(ramp_world());position=(2,20,0);velocity=0;grounded=False
        for _ in range(25):
            result=step_vertical(position,velocity,.05,geometry,grounded)
            position,velocity,grounded=result.position,result.vertical_velocity,result.grounded
        self.assertTrue(grounded)
        self.assertAlmostEqual(position[1],3+2*math.tan(math.pi/6)+1/math.cos(math.pi/6))
        self.assertEqual(velocity,0)

    def test_probe_from_inside_ramp_does_not_teleport_to_top(self):
        geometry=extract_ground(ramp_world())
        self.assertIsNone(ground_raycast((0,3,0),geometry,foot_offset=3,max_distance=50))

    def test_walking_up_and_down_follows_supported_plane(self):
        angle=math.pi/6;geometry=extract_ground(ramp_world())
        initial=3+1/math.cos(angle)
        for sign in [-1,1]:
            controller=OwnCharacterPhysicsController(snapshot((0,initial,0)),geometry=geometry)
            controller.consume([intent(1,vector=(sign,0,0))])
            for step in range(1,5):
                state=controller.step(.1)['predicted_state']
                self.assertTrue(state['grounded'])
                self.assertAlmostEqual(state['position'][0],sign*step)
                self.assertAlmostEqual(state['position'][1],initial+sign*step*math.tan(angle))
                self.assertAlmostEqual(state['linear_velocity'][1],sign*10*math.tan(angle))

    def test_leaving_rotated_edge_returns_to_fall(self):
        angle=math.pi/6;geometry=extract_ground(ramp_world(size=(4,2,10)))
        controller=OwnCharacterPhysicsController(snapshot((0,3+1/math.cos(angle),0)),geometry=geometry)
        controller.consume([intent(1)])
        controller.step(.1)
        state=controller.step(.25)['predicted_state']
        # Leave the entire rotated volume, including its upward-facing end.
        self.assertGreater(state['position'][0],2*math.cos(angle)+math.sin(angle))
        self.assertFalse(state['grounded'])
        self.assertLess(state['linear_velocity'][1],0)

    def test_zero_time_does_not_gain_vertical_velocity_on_slope(self):
        angle=math.pi/6;geometry=extract_ground(ramp_world())
        controller=OwnCharacterPhysicsController(snapshot((0,3+1/math.cos(angle),0)),geometry=geometry)
        controller.consume([intent(1)])
        self.assertEqual(controller.step(0)['predicted_state']['linear_velocity'][1],0)

    def test_compiled_luau_move_and_cframe_drive_slope_prediction(self):
        path=Path(__file__).resolve().parents[2]
        data=json.loads((path/'tests/fixtures/world_snapshot.json').read_text())
        initial=3+1/math.cos(math.pi/6)
        for n in data['nodes']:
            if n['name']=='HumanoidRootPart':
                n['properties']['Anchored']=False
                n['properties']['Position']={'$vector3':[0,initial,0]}
                n['properties']['CFrame']['$cframe']['position']=[0,initial,0]
        before=copy.deepcopy(data)
        for host in [local_runtime.HOST,local_runtime.RUST_HOST]:
            with self.subTest(host=str(host)),mock.patch.object(local_runtime,'RUST_HOST',host):
                result=local_runtime.run_source('''
game:GetService("Players").LocalPlayer.Character.Humanoid:Move(Vector3.xAxis,false)
return CFrame.Angles(0,0,math.rad(30))
''',world_snapshot=data,timeout_ms=1000)
                self.assertTrue(result['ok'],result)
                self.assertFalse(result.get('task_errors'),result)
                world=ramp_world()
                world.instances['ramp']['properties']['CFrame']=result['values'][0]['$cframe']
                controller=OwnCharacterPhysicsController(data,geometry=extract_ground(world))
                controller.consume(result['movement_intents'])
                state=controller.step(.1)['predicted_state']
                self.assertTrue(state['grounded'])
                self.assertAlmostEqual(state['position'][0],1.6)
                self.assertAlmostEqual(state['position'][1],initial+1.6*math.tan(math.pi/6),places=5)
        self.assertEqual(data,before)

    def test_runtime_predictor_uses_rotated_ground_and_refreshes_changed_pose(self):
        from physics_runtime_prediction import RuntimePhysicsPrediction
        from test_world_snapshot import fixture
        world,root=fixture()
        world.schema['enums']=[{'name':'PartType'}]
        for spec in [{'name':'CanCollide','type':9},{'name':'size','type':22},
                     {'name':'shape','type':7,'enum':0}]:
            index=len(world.schema['properties']);world.schema['properties'].append(spec)
            world.schema['classes'][3]['properties'].append(index)
        angle=math.pi/6;initial=3+1/math.cos(angle)
        world.instances[root]['properties'].update(Anchored=False,
            CFrame={'position':[0,initial,0],'rotation_id':2})
        world.touch(root,['Anchored','CFrame'])
        ground=(1,90)
        world.instances[ground]={'class':3,'parent':(1,6),'properties':{
            'CanCollide':True,'Anchored':True,'size':[20,2,10],'shape':1,
            'CFrame':{'position':[0,0,0],'rotation':rotation_z(angle)}}}
        world.touch(ground,created=True)
        before=copy.deepcopy(world.instances)
        predictor=RuntimePhysicsPrediction()
        predictor.advance(world,123,10.,[intent(1,instance_id='1:5')])
        report=predictor.advance(world,123,10.1)
        self.assertEqual(report['ground_collider_count'],1)
        self.assertAlmostEqual(report['predicted_position'][0],1.6)
        self.assertAlmostEqual(report['predicted_position'][1],initial+1.6*math.tan(angle))
        self.assertEqual(world.instances,before)
        self.assertEqual(report['network_packets_sent'],0)
        previous=predictor._geometry
        world.instances[ground]['properties']['CFrame']['rotation']=rotation_z(-angle)
        world.touch(ground,['CFrame'])
        report=predictor.advance(world,123,10.5)
        self.assertIsNot(predictor._geometry,previous)
        self.assertGreater(predictor._geometry.colliders[0].rotation[1],0)

    def test_exact_rotated_face_tests_only_visit_vertical_ray_candidates(self):
        world=ramp_world();template=world.instances['ramp']
        world.instances={i:{**template,'properties':{**template['properties'],
            'CFrame':{'position':[30*i,0,0],'rotation':rotation_z(.3)}}} for i in range(1000)}
        geometry=extract_ground(world)
        with mock.patch.object(physics_ground,'ray_obb',wraps=movement_collision.ray_obb) as exact:
            hit=ground_raycast((0,10,0),geometry,max_distance=20)
        self.assertIsNotNone(hit)
        self.assertEqual(exact.call_count,1)

    def test_exact_top_corners_survive_inverse_transform_roundoff(self):
        for degrees in range(1,90):
            angle=math.radians(degrees)
            x=10*math.cos(angle)-math.sin(angle)
            y=10*math.sin(angle)+math.cos(angle)
            with self.subTest(degrees=degrees):
                hit=ground_raycast((x,y+3,0),extract_ground(ramp_world(angle)),
                                   foot_offset=3,max_distance=0)
                self.assertIsNotNone(hit)


if __name__=='__main__':unittest.main()
