"""Swept root boxes and local wall response; never opens a network session."""
import copy
import importlib
import json
import math
from pathlib import Path
import unittest
from unittest import mock

import local_runtime
import movement_collision as collision
import physics_body_collision as body_collision
from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, OBB, TriangleMesh, VoxelGrid, GroundGeometry
from test_physics_controller import snapshot, intent

IDENTITY=(1,0,0,0,1,0,0,0,1)


def body_snapshot(position=(0,4,0),size=(2,2,1)):
    data=snapshot(position)
    data['nodes'][-1]['properties']['Size']={'$vector3':list(size)}
    return data


def walls(*boxes):
    return GroundGeometry(tuple(boxes),())


class BodyCollisionTests(unittest.TestCase):
    def test_sweep_hits_thin_wall_before_endpoint(self):
        hit=collision.sweep_aabb_obb((0,0,0),(1,1,.5),(20,0,0),
            (5,0,0),(.05,10,10),IDENTITY)
        self.assertAlmostEqual(hit.distance,(5-.05-1)/20)
        self.assertEqual(hit.normal,(-1,0,0))

    def test_body_sweep_hits_exact_triangle_mesh_wall(self):
        mesh=TriangleMesh(((2,-2,-2),(2,4,-2),(2,4,2),(2,-2,2)),
            ((0,1,2),(0,2,3)),'decoded-wall')
        result=body_collision.move_horizontal_body((0,1,0),(4,0,0),(.5,.5,.5),
            walls(mesh),grounded=False)
        self.assertAlmostEqual(result.position[0],1.5,places=5)
        self.assertEqual(result.normals[0],(-1.,0.,0.))

    def test_mesh_world_bounds_are_not_used_as_solid_collision(self):
        mesh=TriangleMesh(((2,-2,-3),(2,4,-3),(2,4,-2),
                           (2,-2,2),(2,4,2),(2,4,3)),
            ((0,1,2),(3,4,5)),'disjoint-wall-patches')
        result=body_collision.move_horizontal_body((0,1,0),(4,0,0),(.25,.25,.25),
            walls(mesh),grounded=False)
        self.assertAlmostEqual(result.position[0],4)

    def test_body_sweep_hits_occupied_voxel_cell_and_skips_empty_cells(self):
        grid=VoxelGrid((2,0,-1),(.25,4,2),(2,1,1),(False,True),'decoded-voxels')
        result=body_collision.move_horizontal_body((0,1,0),(4,0,0),(.5,.5,.5),
            walls(grid),grounded=False)
        self.assertAlmostEqual(result.position[0],1.75,places=5)
        empty=VoxelGrid((2,0,-1),(.25,4,2),(2,1,1),(False,False),'empty')
        result=body_collision.move_horizontal_body((0,1,0),(4,0,0),(.5,.5,.5),
            walls(empty),grounded=False)
        self.assertAlmostEqual(result.position[0],4)

    def test_mesh_step_clearance_uses_triangle_faces_not_mesh_bounds(self):
        floor=TriangleMesh(((-4,0,-4),(4,0,-4),(4,0,4),(-4,0,4)),
            ((0,2,1),(0,3,2)),'floor')
        # Vertical triangles form an actual .5 high step; the mesh's overall
        # bounds must not be treated as a solid enclosing box.
        step=TriangleMesh(((1,0,-2),(1,.5,-2),(2,.5,-2),(2,0,-2),
                           (1,0,2),(1,.5,2),(2,.5,2),(2,0,2)),
            ((0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
             (3,2,6),(3,6,7),(1,5,6),(1,6,2),(0,3,7),(0,7,4)),'step')
        # A mesh-only narrow test confirms exact surface contacts; closed mesh
        # stepping relies on the independent ground-ray support geometry.
        result=body_collision.move_horizontal_body((0,1,0),(3,0,0),(.5,.5,.5),
            walls(floor,step),grounded=False,foot_offset=1)
        self.assertLess(result.position[0],1)

    def test_vertical_body_sweep_stops_at_exact_mesh_ceiling(self):
        ceiling=TriangleMesh(((-2,3,-2),(2,3,-2),(2,3,2),(-2,3,2)),
            ((0,2,1),(0,3,2)),'decoded-ceiling')
        step,body=body_collision.step_vertical_body((0,1,0),8.,.25,walls(ceiling),False,
            half_size=(.5,.5,.5),gravity=0.)
        self.assertAlmostEqual(step.position[1],2.5,places=5)
        self.assertIsNotNone(body)

    def test_sweep_respects_vertical_clearance(self):
        self.assertIsNone(collision.sweep_aabb_obb((0,20,0),(1,1,.5),(20,0,0),
            (5,0,0),(.05,2,10),IDENTITY))

    def test_rotated_wall_uses_box_support_radius_and_world_normal(self):
        c=math.sqrt(.5);rotation=(c,0,c,0,1,0,-c,0,c)
        hit=collision.sweep_aabb_obb((0,0,0),(1,1,.5),(20,0,0),
            (5,0,0),(.05,10,100),rotation)
        self.assertAlmostEqual(hit.distance,(5*c-(1.5*c+.05))/(20*c))
        self.assertAlmostEqual(hit.normal[0],-c)
        self.assertAlmostEqual(hit.normal[2],c)

    def test_touching_inward_hits_but_tangent_and_outward_do_not(self):
        args=((0,0,0),(1,1,1))
        inward=collision.sweep_aabb_obb(*args,(1,0,0),(3,0,0),(2,2,2),IDENTITY)
        self.assertEqual(inward.distance,0)
        for delta in [(-1,0,0),(0,0,1),(0,0,0)]:
            self.assertIsNone(collision.sweep_aabb_obb(*args,delta,(3,0,0),(2,2,2),IDENTITY))

    def test_initial_overlap_has_no_invented_exit_normal(self):
        hit=collision.sweep_aabb_obb((.1,0,0),(1,1,1),(1,0,0),(3,0,0),(2,2,2),IDENTITY)
        self.assertEqual(hit,(0,(0,0,0)))

    def test_sweep_rejects_nonfinite_negative_or_nonrigid_geometry(self):
        for half,delta,rotation in [((-1,1,1),(1,0,0),IDENTITY),
            ((1,1,1),(math.nan,0,0),IDENTITY),((1,1,1),(1,0,0),(2,0,0,0,1,0,0,0,1))]:
            with self.assertRaises(ValueError):
                collision.sweep_aabb_obb((0,0,0),half,delta,(3,0,0),(2,2,2),rotation)

    def test_edge_cross_axis_separates_boxes_whose_face_projections_overlap(self):
        rotation=(-.31462370206421775,-.49372899632213646,.8107056212276699,
                  -.8834621481283713,-.160034288932919,-.4403222220035444,
                  .3471405463552334,-.8547635372174405,-.3858402992427298)
        self.assertIsNone(collision.sweep_aabb_obb((0,0,0),(2,.3,.4),(0,0,0),
            (.67730440508568,-1.7643478472687697,1.2240469729212036),(1.5,.25,.35),rotation))

    def test_sliding_keeps_tangential_distance_without_wall_penetration(self):
        module=importlib.import_module('physics_body_collision')
        result=module.move_horizontal_body((0,4,0),(8,0,4),(1,1,.5),
            walls(AABB((4,-10,-100),(4.1,20,100))),grounded=False)
        self.assertAlmostEqual(result.position[0],3,places=5)
        self.assertAlmostEqual(result.position[2],4,places=5)
        self.assertEqual(len(result.normals),1)

    def test_corner_stops_both_components(self):
        module=importlib.import_module('physics_body_collision')
        result=module.move_horizontal_body((0,4,0),(8,0,8),(1,1,1),
            walls(AABB((4,-10,-100),(4.1,20,100)),AABB((-100,-10,4),(100,20,4.1))),grounded=False)
        self.assertAlmostEqual(result.position[0],3,places=5)
        self.assertAlmostEqual(result.position[2],3,places=5)
        self.assertEqual(len(result.normals),2)

    def test_rotated_wall_slides_along_its_actual_face(self):
        module=importlib.import_module('physics_body_collision')
        c=math.sqrt(.5);rotation=(c,0,c,0,1,0,-c,0,c)
        box=OBB((5,4,0),(.05,10,100),rotation)
        result=module.move_horizontal_body((0,4,0),(10,0,0),(1,1,.5),walls(box),grounded=False)
        # The final box stays outside the wall's local -X face; its remaining
        # motion follows the diagonal wall instead of its enclosing AABB.
        distance=c*(result.position[0]-5)-c*result.position[2]
        self.assertAlmostEqual(distance,-(.05+1.5*c),places=5)
        self.assertGreater(result.position[2],2)

    def test_nearby_wall_after_first_slide_is_also_swept(self):
        module=importlib.import_module('physics_body_collision')
        result=module.move_horizontal_body((0,4,0),(10,0,10),(1,1,1),
            walls(AABB((4,-10,-100),(4.1,20,100)),AABB((-100,-10,6),(100,20,6.1))),grounded=False)
        self.assertAlmostEqual(result.position[0],3,places=5)
        self.assertAlmostEqual(result.position[2],5,places=5)

    def test_descending_edge_landing_uses_footprint_then_root_box_fallback(self):
        module=importlib.import_module('physics_body_collision')
        # The .5 edge reaches a footprint probe and keeps Humanoid foot height.
        # The .95 sliver misses all probes but must still stop the root hull.
        for edge, expected in ((.5, 3.1), (.95, 1.1)):
            with self.subTest(edge=edge):
                geometry=walls(AABB((edge,0,-10),(10,.1,10)))
                step,body=module.step_vertical_body((0,4,0),-20,.2,geometry,False,
                    half_size=(1,1,.5),gravity=100)
                self.assertIsNone(body)
                self.assertTrue(step.grounded)
                self.assertEqual(step.vertical_velocity,0)
                self.assertAlmostEqual(step.position[1],expected,places=5)
                for _ in range(3):
                    step,body=module.step_vertical_body(step.position,step.vertical_velocity,.1,
                        geometry,step.grounded,half_size=(1,1,.5),gravity=100)
                    self.assertIsNone(body)
                    self.assertTrue(step.grounded)
                    self.assertAlmostEqual(step.position[1],expected,places=5)

    def test_controller_cannot_tunnel_through_thin_wall(self):
        data=body_snapshot();data['nodes'][-2]['properties']['WalkSpeed']=128
        controller=OwnCharacterPhysicsController(data,geometry=walls(AABB((4,-10,-100),(4.01,20,100))),gravity=0)
        controller.consume([intent(1)])
        state=controller.step(.25)['predicted_state']
        self.assertAlmostEqual(state['position'][0],3,places=5)
        self.assertEqual(state['linear_velocity'][0],0)
        again=controller.step(.25)['predicted_state']
        self.assertAlmostEqual(again['position'][0],3,places=5)

    def test_slide_velocity_and_move_to_target_survive_obstruction(self):
        controller=OwnCharacterPhysicsController(body_snapshot(),geometry=walls(AABB((2,-10,-100),(2.1,20,100))),gravity=0)
        controller.consume([intent(1,'MoveTo',(4,4,0))])
        blocked=controller.step(.25)['predicted_state']
        self.assertAlmostEqual(blocked['position'][0],1,places=5)
        self.assertIsNotNone(blocked['target'])
        controller.update_geometry(walls())
        controller.step(.25)
        reached=controller.step(.25)['predicted_state']
        self.assertLessEqual(abs(reached['position'][0]-4),1)
        self.assertIsNone(reached['target'])
        self.assertEqual(reached['linear_velocity'],[0,0,0])
        self.assertEqual(controller.feedback_events()['move_to'],[{'sequence':1,'reached':True}])

    def test_controller_reports_overlap_and_does_not_escape_through_box(self):
        controller=OwnCharacterPhysicsController(body_snapshot(),geometry=walls(AABB((-2,2,-2),(2,6,2))),gravity=0)
        controller.consume([intent(1)])
        report=controller.step(.1)
        self.assertEqual(report['predicted_state']['position'],[0,4,0])
        self.assertTrue(report['body_collision']['initial_overlap'])

    def test_unknown_or_invalid_root_size_cannot_reuse_stale_proxy(self):
        controller=OwnCharacterPhysicsController(body_snapshot(),gravity=0)
        self.assertTrue(controller.report()['body_collision']['available'])
        for value in [None,{'$vector3':[0,2,1]},{'$vector3':[2,float('nan'),1]}]:
            data=body_snapshot();data['nodes'][-1]['properties']['Size']=value
            controller.update_snapshot(data)
            self.assertFalse(controller.step(0)['body_collision']['available'])

    def test_ground_following_still_works_with_sized_root_proxy(self):
        from test_physics_slopes import ramp_world
        from physics_ground import extract_ground
        angle=math.pi/6;initial=3+1/math.cos(angle)
        controller=OwnCharacterPhysicsController(body_snapshot((0,initial,0)),geometry=extract_ground(ramp_world()))
        controller.consume([intent(1)])
        for i in range(1,5):
            state=controller.step(.1)['predicted_state']
            self.assertTrue(state['grounded'])
            self.assertAlmostEqual(state['position'][0],i)
            self.assertAlmostEqual(state['position'][1],initial+i*math.tan(angle))

    def test_wall_on_ramp_stops_upward_contact_velocity(self):
        from test_physics_slopes import ramp_world
        from physics_ground import extract_ground
        angle=math.pi/6;initial=3+1/math.cos(angle)
        ramp=extract_ground(ramp_world()).colliders[0]
        controller=OwnCharacterPhysicsController(body_snapshot((0,initial,0)),
            geometry=walls(ramp,AABB((2,-10,-100),(2.1,20,100))))
        controller.consume([intent(1)])
        state=controller.step(.25)['predicted_state']
        self.assertAlmostEqual(state['position'][0],1,places=5)
        self.assertAlmostEqual(state['position'][1],initial+math.tan(angle),places=5)
        self.assertEqual(state['linear_velocity'],[0,0,0])

    def test_raising_or_removing_wall_lets_controller_resume(self):
        wall=AABB((2,-10,-100),(2.1,20,100))
        controller=OwnCharacterPhysicsController(body_snapshot(),geometry=walls(wall),gravity=0)
        controller.consume([intent(1)]);controller.step(.25)
        controller.update_geometry(walls(AABB((2,10,-100),(2.1,20,100))))
        state=controller.step(.25)['predicted_state']
        self.assertGreater(state['position'][0],3)

    def test_known_geometry_is_not_inferred_from_class_default_root_size(self):
        data=body_snapshot();data['nodes'][-1]['origins']={'Anchored':'received','Size':'class_default'}
        controller=OwnCharacterPhysicsController(data,gravity=0)
        self.assertFalse(controller.step(0)['body_collision']['available'])

    def test_sweep_bounds_cull_distant_obstacles_before_exact_sat(self):
        module=importlib.import_module('physics_body_collision')
        boxes=[AABB((4,-10,-10),(4.1,20,10))]
        boxes += [AABB((i*30,-10,100),(i*30+1,20,101)) for i in range(1,1000)]
        with mock.patch.object(module,'sweep_aabb_obb',wraps=collision.sweep_aabb_obb) as exact:
            result=module.move_horizontal_body((0,4,0),(10,0,0),(1,1,.5),walls(*boxes),grounded=False)
        self.assertEqual(exact.call_count,1)
        self.assertAlmostEqual(result.position[0],3,places=5)

    def test_compiled_luau_move_hits_wall_in_both_workers(self):
        project=Path(__file__).resolve().parents[1]
        data=json.loads((project/'runtime/tests/world_snapshot.json').read_text())
        for node in data['nodes']:
            if node['name']=='HumanoidRootPart':
                node['properties'].update(Anchored=False,Size={'$vector3':[2,2,1]},Position={'$vector3':[0,4,0]})
                node['properties']['CFrame']['$cframe']['position']=[0,4,0]
        before=copy.deepcopy(data)
        for host in [local_runtime.HOST,local_runtime.RUST_HOST]:
            with self.subTest(host=str(host)),mock.patch.object(local_runtime,'RUST_HOST',host):
                result=local_runtime.run_source('''
game:GetService("Players").LocalPlayer.Character.Humanoid:Move(Vector3.xAxis,false)
return true
''',world_snapshot=data,timeout_ms=1000)
                self.assertTrue(result['ok'],result)
                controller=OwnCharacterPhysicsController(data,geometry=walls(AABB((2,-10,-100),(2.1,20,100))),gravity=0)
                controller.consume(result['movement_intents'])
                state=controller.step(.25)['predicted_state']
                self.assertAlmostEqual(state['position'][0],1,places=5)
                self.assertEqual(state['linear_velocity'][0],0)
        self.assertEqual(data,before)

    def test_runtime_predictor_consumes_received_wall_and_collision_changes(self):
        from physics_runtime_prediction import RuntimePhysicsPrediction
        from test_world_snapshot import fixture
        world,root=fixture()
        world.schema['enums']=[{'name':'PartType'}]
        for spec in [{'name':'CanCollide','type':9},{'name':'size','type':22},
                     {'name':'shape','type':7,'enum':0}]:
            index=len(world.schema['properties']);world.schema['properties'].append(spec)
            world.schema['classes'][3]['properties'].append(index)
        world.instances[root]['properties'].update(Anchored=False,size=[2,2,1],
            CFrame={'position':[0,4,0],'rotation_id':2})
        world.touch(root,['Anchored','size','CFrame'])
        world.instances[(1,6)]['properties']['Gravity']=0.
        wall=(1,90)
        world.instances[wall]={'class':3,'parent':(1,6),'properties':{
            'CanCollide':True,'Anchored':True,'size':[.1,20,200],'shape':1,
            'CFrame':{'position':[2,4,0],'rotation_id':2}}}
        world.touch(wall,created=True)
        before=copy.deepcopy(world.instances)
        predictor=RuntimePhysicsPrediction()
        predictor.advance(world,123,10.,[intent(1,instance_id='1:5')])
        blocked=predictor.advance(world,123,10.25)
        self.assertAlmostEqual(blocked['predicted_position'][0],.95,places=5)
        self.assertTrue(blocked['controller']['body_collision']['available'])
        self.assertEqual(world.instances,before)
        world.instances[wall]['properties']['CanCollide']=False
        world.touch(wall,['CanCollide'])
        released=predictor.advance(world,123,10.5)
        self.assertGreater(released['predicted_position'][0],4)
        self.assertEqual(released['network_packets_sent'],0)


if __name__=='__main__':unittest.main()
