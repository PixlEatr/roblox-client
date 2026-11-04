"""Collision masks through received state, character movement and Luau edits."""
import base64
import copy
import json
from pathlib import Path
import struct
import unittest
from unittest import mock
import test_cframe_transforms as transforms

from collision_groups import (CollisionContext, CollisionTable, context_from_world,
                              decode_binary, decode_legacy, part_group)
from physics_runtime_prediction import RuntimePhysicsPrediction
from physics_controller import OwnCharacterPhysicsController
from physics_ground import AABB, GroundGeometry
from physics_script_bridge import PhysicsScriptBridge
from test_physics_controller import snapshot, intent
from test_world_snapshot import fixture
from world_snapshot import make_world_snapshot, make_world_delta


def matrix(default=-3,ghost=-2):
    result=bytearray([1,2])
    for id,name,mask in ((0,b'Default',default),(1,b'Ghost',ghost)):
        result+=bytes([id,4])+struct.pack('<I',mask&0xffffffff)+bytes([len(name)])+name
    return bytes(result)


def received_world(*, wall_group='Ghost',floor_group='Default',root_group='Default'):
    world,root=fixture()
    for name,kind in [('Size',22),('CanCollide',9),('Shape',7)]:
        world.schema['classes'][3]['properties'].append(len(world.schema['properties']))
        world.schema['properties'].append({'name':name,'type':kind,**({'enum':0} if name=='Shape' else {})})
    world.schema['enums']=[{'name':'PartType'}]
    world.instances[root]['properties'].update(Anchored=False,CanCollide=False,Size=[2,2,2],Shape=1,
        CFrame={'position':[0,3,0],'rotation_id':2},CollisionGroupReplicate=b'\x00'+root_group.encode())
    world.instances[(1,6)]['properties'].update(CollisionGroupData=matrix(),Gravity=0.)
    world.instances[(1,5)]['properties'].update(WalkSpeed=16.)
    for key,position,size,group in [((1,90),[0,-1,0],[100,2,100],floor_group),
                                   ((1,91),[3,3,0],[1,8,100],wall_group)]:
        world.instances[key]={'class':3,'parent':(1,6),'properties':{
            'Anchored':True,'CanCollide':True,'Size':size,'Shape':1,
            'CFrame':{'position':position,'rotation_id':2},
            'CollisionGroupReplicate':b'\x01'+group.encode()}}
    return world,root


class CollisionDecoderTests(unittest.TestCase):
    def test_public_binary_fixture_and_all_mask_pairs(self):
        ref=json.loads((Path(__file__).resolve().parents[1]/'fixtures/references/collision-groups-public.json').read_text())
        raw=base64.b64decode(ref['base64'])
        for value in (raw,{'$bytes_hex':raw.hex()}):
            table=decode_binary(value)
            self.assertEqual(table.groups,tuple((r['id'],r['name'],r['mask']&0xffffffff) for r in ref['groups']))
            for a in ref['groups']:
                for b in ref['groups']:
                    expected=bool(a['mask']&(1<<b['id']) and b['mask']&(1<<a['id']))
                    self.assertEqual(table.collidable(a['name'],b['name']),expected)

    def test_zero_high_bit_and_legacy_masks(self):
        data=b'\x01\x01\x00\x04'+b'\x00'*4+b'\x07Default'
        self.assertFalse(decode_binary(data.decode('utf-8')).collidable('Default','Default'))
        rows=decode_legacy('Default^0^-2147483648\\High^31^1')
        self.assertTrue(rows.collidable('Default','High'))
        self.assertFalse(rows.collidable('High','High'))
        self.assertEqual(decode_legacy(b'Default^0^-1').groups,((0,'Default',0xffffffff),))

    def test_incomplete_and_unknown_names_use_native_adapter_policy(self):
        self.assertTrue(CollisionTable().collidable('Default','Default'))
        with self.assertRaisesRegex(ValueError,'unavailable'):CollisionTable().collidable('Ghost','Default')
        table=decode_binary(matrix())
        self.assertFalse(table.collidable('unknown','Ghost'))
        self.assertTrue(table.collidable('unknown','Default'))

    def test_malformed_data_is_bounded_and_rejected(self):
        raw=matrix()
        bad=[b'',b'\x02'+raw[1:],raw[:-1],raw+b'\0',raw[:3]+b'\x03'+raw[4:],
             b'\x01\x00',b'\x01\x21',{'$bytes_hex':'ff gg'}, {'$bytes_hex':'1'},
             {'$bytes_hex':raw.hex(),'extra':1},b'\x01'*4097]
        self.assertTrue(decode_binary(b'').complete)
        for value in bad[1:]:
            with self.subTest(value=str(value)[:30]),self.assertRaises(ValueError):decode_binary(value)
        for value in ('Default^0^-1\\','Default^0^1\\Other^0^1','Other^0^1','Default^0^4294967296',
                      'Default^0^ 1','Default^0^1\\Other^32^1'):
            with self.subTest(value=value),self.assertRaises(ValueError):decode_legacy(value)

    def test_private_part_name_decoding_and_public_override(self):
        self.assertEqual(part_group({'CollisionGroupReplicate':b'\x00'}),'Default')
        self.assertEqual(part_group({'CollisionGroupReplicate':{'$bytes_hex':b'\x01Ghost'.hex()}}),'Ghost')
        self.assertEqual(part_group({'CollisionGroup':'Default','CollisionGroupReplicate':b'\x01Ghost'}),'Default')
        for props in ({'CollisionGroupReplicate':b''},{'CollisionGroupReplicate':b'\x01\xff'},
                      {'CollisionGroup':''},{'CollisionGroup':False}):
            with self.assertRaises(ValueError):part_group(props)


class CollisionMovementTests(unittest.TestCase):
    def test_wall_filter_changes_actual_walk_displacement(self):
        results=[]
        for group in ('Default','Ghost'):
            world,root=received_world(wall_group=group)
            before=copy.deepcopy(world.instances)
            engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
            engine.advance(world,123,10.,[intent(1,instance_id='1:5')])
            result=engine.advance(world,123,10.25)
            results.append(result['predicted_position'][0])
            self.assertEqual(world.instances,before)
        self.assertLess(results[0],1.6)
        self.assertAlmostEqual(results[1],4.)

    def test_noncolliding_floor_falls_instead_of_hovering(self):
        world,root=received_world(floor_group='Ghost')
        world.instances[(1,6)]['properties']['Gravity']=20.
        engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        result=engine.advance(world,123,10.25)
        self.assertLess(result['predicted_position'][1],3.)
        self.assertFalse(result['controller']['predicted_state']['grounded'])
        self.assertNotIn((1,90),{c.source for c in engine.bridge.controller.geometry.colliders})

    def test_matrix_and_part_group_changes_apply_before_geometry_interval(self):
        world,root=received_world(wall_group='Default')
        engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        geometry=engine._geometry
        world.instances[(1,91)]['properties']['CollisionGroupReplicate']=b'\x01Ghost'
        world.touch((1,91),['CollisionGroupReplicate'])
        engine.advance(world,123,10.01)
        self.assertIs(engine._geometry,geometry)
        self.assertNotIn((1,91),{c.source for c in engine.bridge.controller.geometry.colliders})
        world.instances[(1,6)]['properties']['CollisionGroupData']=matrix(-1,-1)
        world.touch((1,6),['CollisionGroupData'])
        engine.advance(world,123,10.02)
        self.assertIs(engine._geometry,geometry)
        self.assertIn((1,91),{c.source for c in engine.bridge.controller.geometry.colliders})

    def test_private_name_snapshot_revision_invalidates_luau_override(self):
        world,root=received_world(wall_group='Ghost')
        engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        engine.advance(world,123,10.01,mutations=[{'kind':'set','id':'1:4','property':'CollisionGroup','value':'Ghost'}])
        controller=engine.bridge.controller
        self.assertEqual(controller._root_collision_group,'Ghost')
        self.assertIn((1,91),{c.source for c in controller.geometry.colliders})
        self.assertNotIn((1,90),{c.source for c in controller.geometry.colliders})
        world.instances[root]['properties']['CollisionGroupReplicate']=b'\0Default'
        world.touch(root,['CollisionGroupReplicate'])
        engine.advance(world,123,10.02)
        self.assertEqual(controller._root_collision_group,'Default')
        self.assertIn((1,90),{c.source for c in controller.geometry.colliders})
        self.assertNotIn((1,91),{c.source for c in controller.geometry.colliders})
        self.assertEqual(world.instances[root]['properties']['CollisionGroupReplicate'],b'\0Default')

    def test_malformed_matrix_suspends_without_falling_and_recovers_pose(self):
        world,root=received_world()
        engine=RuntimePhysicsPrediction();first=engine.advance(world,123,10.)
        controller=engine.bridge.controller;position=controller.vertical_position
        world.instances[(1,6)]['properties']['CollisionGroupData']=b'bad'
        world.touch((1,6),['CollisionGroupData'])
        failed=engine.advance(world,123,10.1)
        self.assertIsNone(failed['predicted_position'])
        self.assertEqual(failed['last_reason'],'collision_group_state_unavailable')
        self.assertEqual(controller.vertical_position,position)
        self.assertTrue(failed['controller']['collision_filter']['error'])
        world.instances[(1,6)]['properties']['CollisionGroupData']=matrix()
        world.touch((1,6),['CollisionGroupData'])
        restored=engine.advance(world,123,10.2)
        self.assertEqual(restored['predicted_position'],first['predicted_position'])
        self.assertIs(engine.bridge.controller,controller)

    def test_worldmodel_floor_is_not_workspace_ground_and_reparent_updates(self):
        world,root=received_world(wall_group='Ghost')
        world.schema['classes'].append({'name':'WorldModel','properties':[]})
        model=(1,100);world.instances[model]={'class':6,'parent':(1,6),'properties':{}}
        world.instances[(1,90)]['parent']=model
        engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        self.assertEqual(engine.bridge.controller.geometry.colliders,())
        world.instances[(1,90)]['parent']=(1,6);world.touch((1,90),['Parent'])
        engine.advance(world,123,10.01)
        self.assertIn((1,90),{c.source for c in engine.bridge.controller.geometry.colliders})

    def test_worldmodel_local_matrix_and_workspace_opt_in(self):
        world,root=received_world(floor_group='Ghost')
        world.schema['classes'].append({'name':'WorldModel','properties':[]})
        model=(1,100)
        world.instances[model]={'class':6,'parent':(1,6),'properties':{'CollisionGroupData':matrix(-1,-1)}}
        world.instances[(1,3)]['parent']=model;world.instances[(1,90)]['parent']=model
        geometry=GroundGeometry((AABB((-10,-2,-10),(10,0,10),(1,90)),))
        context=context_from_world(world,geometry,(1,3))
        self.assertEqual(context.allowed_sources('Default'),frozenset({(1,90)}))
        world.instances[model]['properties']['UseWorkspaceCollisionGroups']=True
        self.assertEqual(context_from_world(world,geometry,(1,3)).allowed_sources('Default'),frozenset())
        world.instances[model]['properties']['UseWorkspaceCollisionGroups']='bad'
        self.assertIsNotNone(context_from_world(world,geometry,(1,3)).error)

    def test_missing_custom_matrix_does_not_fabricate_character_collisions(self):
        world,root=received_world(root_group='Ghost')
        world.instances[(1,6)]['properties'].pop('CollisionGroupData')
        engine=RuntimePhysicsPrediction();result=engine.advance(world,123,10.)
        self.assertEqual(result['last_reason'],'collision_group_state_unavailable')
        self.assertIsNone(result['predicted_position'])
        controller=engine.bridge.controller;before=controller.vertical_velocity
        controller.consume([intent(1,'Jump',instance_id='1:5')])
        self.assertEqual(controller.last_reason,'collision_group_state_unavailable')
        self.assertEqual(controller.vertical_velocity,before)

    def test_rules_are_cached_when_only_character_pose_changes(self):
        world,root=received_world();engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        with mock.patch('physics_runtime_prediction.context_from_world',wraps=context_from_world) as build:
            for i in range(1,5):
                world.touch(root,['CFrame']);engine.advance(world,123,10.+i*.01)
            self.assertEqual(build.call_count,0)

    def test_can_collide_false_and_removed_support_drop_without_platform_carry(self):
        world,root=received_world(wall_group='Ghost')
        engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
        controller=engine.bridge.controller
        controller._ground_source=(1,90);controller.grounded=True;controller._platform_carry_pending=True
        world.instances[(1,90)]['properties']['CanCollide']=False;world.touch((1,90),['CanCollide'])
        engine.advance(world,123,10.01)
        self.assertFalse(controller.grounded)
        self.assertIsNone(controller._ground_source)
        self.assertFalse(controller._platform_carry_pending)

    def test_floor_ceiling_and_wall_use_one_filtered_geometry(self):
        state=snapshot((0,3,0));state['nodes'][3]['properties'].update(Size={'$vector3':[2,2,2]},CollisionGroup='Default')
        floor=AABB((-20,-2,-20),(20,0,20),'floor')
        ceiling=AABB((-20,5,-20),(20,6,20),'ceiling')
        controller=OwnCharacterPhysicsController(state,geometry=GroundGeometry((floor,ceiling)),gravity=0.,foot_offset=3.)
        rules=CollisionContext(decode_binary(matrix()),(('floor','Default'),('ceiling','Ghost')))
        controller.update_collision_context(rules)
        controller.consume([{'sequence':1,'kind':'Jump','instance_id':'humanoid',
                             'jump_settings':{'UseJumpPower':True,'JumpPower':20.}}])
        self.assertEqual(controller.accepted,1)
        hit=controller.step(.25)['predicted_state']
        self.assertGreater(hit['position'][1],6.)
        self.assertFalse(controller._body_collision.get('ceiling_contact',False))


class NativeCollisionBridgeTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_actual_luau_group_write_changes_character_wall_collision(self):
        world,root=received_world()
        before=copy.deepcopy(world.instances)
        results=self.run_script('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.CollisionGroup="Ghost"
assert(root.CollisionGroupId==1)
''',make_world_snapshot(world,123))
        for result in results:
            engine=RuntimePhysicsPrediction();engine.advance(world,123,10.)
            engine.advance(world,123,10.,[intent(1,instance_id='1:5')],mutations=result['mutations'])
            report=engine.advance(world,123,10.25)
            self.assertLess(report['predicted_position'][0],1.6)
            self.assertEqual(report['controller']['collision_filter']['group'],'Ghost')
        self.assertEqual(world.instances,before)


if __name__=='__main__':unittest.main()
