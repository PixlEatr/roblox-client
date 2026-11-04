"""Scripted rig dimensions determine supported root height and landings."""
import copy
import math
import unittest
from test_physics_foot_offset import r15_snapshot,r6_snapshot
from test_authored_forces import write
from test_physics_controller import intent
from test_direct_velocity import native_world
from physics_foot_offset import derive_foot_offset
from physics_script_bridge import PhysicsScriptBridge
from physics_controller import OwnCharacterPhysicsController
from physics_ground import GroundGeometry,AABB

FLOOR=AABB((-100,-1,-100),(100,0,100),'floor')


def setup(data=None,geometry=None):
    data=r15_snapshot(hip_height=2) if data is None else data
    b=PhysicsScriptBridge(lambda state:OwnCharacterPhysicsController(state,
        geometry=geometry or GroundGeometry((FLOOR,),()),gravity=100))
    b.update(data)
    return b


def settle(b):
    for _ in range(100):b.step(.02)
    return b.controller


class AuthoredRigClearanceTests(unittest.TestCase):
    def test_hip_height_and_root_size_update_r15_clearance(self):
        data=r15_snapshot(hip_height=2);original=copy.deepcopy(data);b=setup(data)
        b.apply_mutations([write('humanoid','HipHeight',4)])
        self.assertEqual(b.controller.foot_offset,5)
        self.assertEqual(b.controller.foot_offset_origin,'r15_authored_root_size_and_hip_height')
        b.apply_mutations([write('root','Size',{'$vector3':[2,4,1]})])
        self.assertEqual(b.controller.foot_offset,6)
        self.assertEqual(settle(b).vertical_position,6)
        self.assertTrue(b.controller.grounded);self.assertEqual(data,original)

    def test_growing_and_shrinking_grounded_rig_settles_at_new_height(self):
        b=setup();c=settle(b);self.assertEqual(c.vertical_position,3)
        b.apply_mutations([write('humanoid','HipHeight',4)])
        c=settle(b);self.assertEqual(c.vertical_position,5)
        b.apply_mutations([write('humanoid','HipHeight',1)])
        c=settle(b);self.assertEqual(c.vertical_position,2)
        self.assertTrue(c.grounded);self.assertEqual(c.vertical_velocity,0)

    def test_r6_leg_resize_and_reference_change_update_clearance(self):
        data=r6_snapshot();other=copy.deepcopy(data['nodes'][-1]);other['id']='other-leg'
        other['properties']['Size']={'$vector3':[1,5,1]};data['nodes'].append(other)
        b=setup(data);self.assertEqual(b.controller.foot_offset,3.5)
        b.apply_mutations([write('left-leg','Size',{'$vector3':[1,3,1]})])
        self.assertEqual(b.controller.foot_offset,4.5)
        b.apply_mutations([write('humanoid','LeftLeg',{'$ref':'other-leg'})])
        self.assertEqual(b.controller.foot_offset,6.5)
        self.assertEqual(b.controller.foot_offset_origin,'r6_authored_root_left_leg_size_and_hip_height')
        self.assertEqual(settle(b).vertical_position,6.5)

    def test_r15_ignores_leg_size(self):
        data=r6_snapshot();data['nodes'][2]['properties']['RigType']=1;b=setup(data)
        b.apply_mutations([write('left-leg','Size',{'$vector3':[1,50,1]})])
        self.assertEqual(b.controller.foot_offset,1.5)
        self.assertEqual(b.controller.foot_offset_origin,'r15_received_root_size_and_hip_height')

    def test_airborne_resize_does_not_reset_ballistic_position_or_speed(self):
        b=setup();c=settle(b)
        out=b.consume([{**intent(1,'Jump',(0,0,0),'humanoid'),'jump_settings':{'UseJumpPower':True,'JumpPower':50}}])
        self.assertTrue(out[0]['accepted'],out);b.step(.1)
        y,v=c.vertical_position,c.vertical_velocity
        b.apply_mutations([write('humanoid','HipHeight',4)])
        self.assertEqual((c.vertical_position,c.vertical_velocity),(y,v))
        b.step(.1);self.assertGreater(c.vertical_position,y);self.assertLess(c.vertical_velocity,v)
        self.assertEqual(settle(b).vertical_position,5)

    def test_resize_cannot_raise_root_through_low_ceiling(self):
        ceiling=AABB((-10,4.5,-10),(10,5,10),'ceiling')
        data=r15_snapshot(hip_height=2);data['nodes'][3]['properties']['Position']={'$vector3':[0,3,0]}
        b=setup(data,GroundGeometry((FLOOR,ceiling),()))
        b.apply_mutations([write('humanoid','HipHeight',8)])
        c=settle(b)
        self.assertLessEqual(c.vertical_position+c.body_half_size[1],4.5)

    def test_receipts_supersede_local_hip_height_without_losing_other_edits(self):
        data=r15_snapshot(hip_height=2);b=setup(data)
        b.apply_mutations([write('humanoid','HipHeight',4),write('root','Size',{'$vector3':[2,4,1]})])
        b.update(copy.deepcopy(data));self.assertEqual(b.controller.foot_offset,6)
        data['nodes'][2]['property_revisions']={'HipHeight':10};b.update(data)
        self.assertEqual(b.controller.foot_offset,4)
        b.apply_mutations([write('humanoid','HipHeight',9)],observed_revision=9)
        self.assertEqual(b.controller.foot_offset,4)

    def test_provenance_enum_validation_and_invalid_dimensions(self):
        malformed=r15_snapshot();malformed['allow_authored_motion']=True;malformed['nodes'][3]['origins']=None
        self.assertEqual(derive_foot_offset(malformed)[0],3)
        data=r15_snapshot(hip_height=4);data['nodes'][2]['origins']['HipHeight']='local'
        self.assertEqual(derive_foot_offset(data)[0],3)
        data['allow_authored_motion']=True
        self.assertEqual(derive_foot_offset(data)[0],5)
        data['nodes'][2]['properties']['RigType']={'$enum':{'type':'HumanoidRigType','value':1}}
        self.assertEqual(derive_foot_offset(data)[0],5)
        data['nodes'][2]['properties']['RigType']['$enum']['type']='PartType'
        self.assertEqual(derive_foot_offset(data)[0],3)
        for value in (math.inf,math.nan,True,-100):
            b=setup();b.apply_mutations([write('humanoid','HipHeight',value)])
            self.assertEqual(b.controller.foot_offset,3)
            self.assertTrue(b.controller.foot_offset_origin.startswith('fallback_'))
        for size in ([0,2,1],[-1,2,1],[1,-1,1]):
            b=setup();b.apply_mutations([write('root','Size',{'$vector3':size})])
            self.assertTrue(b.controller.foot_offset_origin.startswith('fallback_'))

    def test_explicit_controller_configuration_remains_explicit(self):
        data=r15_snapshot();data['allow_authored_motion']=True
        c=OwnCharacterPhysicsController(data,foot_offset=7)
        data['nodes'][2]['properties']['HipHeight']=20;data['nodes'][2]['origins']['HipHeight']='local'
        c.update_snapshot(data);self.assertEqual(c.foot_offset,7)
        self.assertEqual(c.foot_offset_origin,'explicit_configuration')

    def test_native_luau_hip_height_and_size_determine_landing(self):
        import test_cframe_transforms as transforms
        data,root=native_world();h=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
        h['properties'].update(RigType=1,HipHeight=2);h.setdefault('origins',{}).update(RigType='received',HipHeight='received')
        root['properties']['Size']={'$vector3':[2,2,1]};root['origins']['Size']='received'
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
char.Humanoid.HipHeight=4
char.HumanoidRootPart.Size=Vector3.new(2,4,1)
''',data):
            b=setup(data);b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertEqual(b.controller.foot_offset,6)
            self.assertEqual(settle(b).vertical_position,6)

    def test_native_r6_left_leg_reference_and_size_reach_clearance(self):
        import test_cframe_transforms as transforms
        data,root=native_world();h=next(n for n in data['nodes'] if n['class_name']=='Humanoid')
        h['properties'].update(RigType=0,HipHeight=.5,LeftLeg={'$ref':'leg-a'})
        h.setdefault('origins',{}).update(RigType='received',HipHeight='received',LeftLeg='received')
        root['properties']['Size']={'$vector3':[2,2,1]};root['origins']['Size']='received'
        for identifier,name in [('leg-a','LegA'),('leg-b','LegB')]:
            data['nodes'].append({'id':identifier,'class_name':'Part','name':name,'parent':root['parent'],
                'properties':{'Size':{'$vector3':[1,2,1]}},'origins':{'Size':'received'}})
        for result in transforms.CFrameTransformTests.run_script(self,'''
local char=game:GetService("Players").LocalPlayer.Character
char.Humanoid.LeftLeg=char.LegB
char.LegB.Size=Vector3.new(1,4,1)
''',data):
            b=setup(data);b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertEqual(b.controller.foot_offset,5.5)
            self.assertEqual(settle(b).vertical_position,5.5)

    def test_persistent_runtime_uses_scripted_dimensions_at_ground(self):
        import os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,_=ready_world()
        state.instances[(1,5)]['properties'].update(RigType=1,HipHeight=2)
        state.touch((1,5),['RigType','HipHeight']);original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                result=runtime.execute('''
local char=game:GetService("Players").LocalPlayer.Character
char.Humanoid.HipHeight=4
char.HumanoidRootPart.Size=Vector3.new(2,4,1)
return true
''')
                self.assertTrue(result['ok'],result)
                b=runtime.movement_preview.bridge
                self.assertEqual(b.controller.foot_offset,6)
                self.assertEqual(settle(b).vertical_position,6)
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
