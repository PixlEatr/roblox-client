"""Attachment-relative force intake and controller heading integration."""
import copy
import math
import unittest

from physics_forces import collect_received_force_sources
from physics_controller import OwnCharacterPhysicsController
from physics_ground import GroundGeometry
from physics_script_bridge import PhysicsScriptBridge
from test_physics_controller import snapshot

IDENTITY=[1,0,0,0,1,0,0,0,1]
Y90=[0,0,1,0,1,0,-1,0,0]
Z90=[0,-1,0,1,0,0,0,0,1]


def frame(rotation):
    return {'$cframe':{'position':[100,200,300],'rotation':list(rotation)}}


def force_world(mode=0):
    data=snapshot()
    root=data['nodes'][3]
    root['properties'].update(CFrame=frame(IDENTITY),AssemblyMass=2)
    root['origins']={'CFrame':'received','AssemblyMass':'received','Anchored':'received'}
    data['nodes'] += [
        {'id':'a0','class_name':'Attachment','parent':'root',
         'properties':{'CFrame':frame(IDENTITY)},'origins':{'CFrame':'received'}},
        {'id':'reference','class_name':'Part','parent':'workspace',
         'properties':{'CFrame':frame(Y90)},'origins':{'CFrame':'received'}},
        {'id':'a1','class_name':'Attachment','parent':'reference',
         'properties':{'CFrame':frame(IDENTITY)},'origins':{'CFrame':'received'}},
        {'id':'force','class_name':'VectorForce','parent':'root',
         'properties':{'Enabled':True,'Force':{'$vector3':[8,0,0]},
                       'Attachment0':{'$ref':'a0'},'Attachment1':{'$ref':'a1'},
                       'ApplyAtCenterOfMass':True,'RelativeTo':mode},
         'origins':dict.fromkeys(['Enabled','Force','Attachment0','Attachment1',
                                 'ApplyAtCenterOfMass','RelativeTo'],'received')},
    ]
    return data


def node(data,name):
    return next(n for n in data['nodes'] if n['id']==name)


class ForceFrameTests(unittest.TestCase):
    def records(self,data):
        return collect_received_force_sources(data,'root')

    def test_attachment_zero_composes_parent_and_local_rotation_not_translation(self):
        data=force_world()
        node(data,'root')['properties']['CFrame']=frame(Y90)
        node(data,'a0')['properties']['CFrame']=frame(Z90)
        record=self.records(data)[0]
        self.assertEqual(record['force'],[0,8,0])
        self.assertEqual(record['direction_frame'],'Attachment0')
        self.assertAlmostEqual(record['root_reference_yaw'],math.pi/2)

    def test_external_attachment_one_resolves_without_following_character_yaw(self):
        data=force_world({'$enum':{'type':'ActuatorRelativeTo','value':1}})
        record=self.records(data)[0]
        self.assertEqual(record['force'],[0,0,-8])
        self.assertNotIn('root_reference_yaw',record)
        controller=self.controller(data);controller.yaw=math.pi/2
        self.assertEqual(controller._force_acceleration(),(0,0,-4))

    def test_world_force_does_not_need_attachment_orientation(self):
        data=force_world(2)
        node(data,'a0')['properties'].clear();node(data,'root')['origins'].pop('CFrame')
        self.assertEqual(self.records(data)[0]['force'],[8,0,0])
        controller=self.controller(data);controller.yaw=math.pi/2
        self.assertEqual(controller._force_acceleration(),(4,0,0))

    def test_attachment_one_on_same_assembly_tracks_local_heading(self):
        data=force_world(1)
        reference=node(data,'reference')
        reference['properties']['AssemblyRootPart']={'$ref':'root'}
        reference['origins']['AssemblyRootPart']='received'
        controller=self.controller(data)
        self.assertEqual(controller._force_acceleration(),(0,0,-4))
        controller.yaw=math.pi/2
        acceleration=controller._force_acceleration()
        self.assertAlmostEqual(acceleration[0],-4)
        self.assertAlmostEqual(acceleration[2],0)

    def test_receipt_refresh_does_not_double_apply_predicted_turn(self):
        data=force_world()
        controller=self.controller(data);controller.yaw=math.pi/2
        before=controller._force_acceleration()
        node(data,'root')['properties']['CFrame']=frame(Y90)
        controller.update_snapshot(data)
        controller.update_force_sources(self.records(data))
        after=controller._force_acceleration()
        for actual,expected in zip(after,before):
            self.assertAlmostEqual(actual,expected)

    def test_nested_attachments_and_received_absolute_frames(self):
        data=force_world()
        node(data,'a0')['parent']='outer'
        data['nodes'].append({'id':'outer','class_name':'Attachment','parent':'root',
                              'properties':{'CFrame':frame(Z90)},'origins':{'CFrame':'received'}})
        self.assertEqual(self.records(data)[0]['force'],[0,8,0])
        node(data,'a0')['properties']['WorldCFrame']=frame(Y90)
        node(data,'a0')['origins']['WorldCFrame']='received'
        node(data,'outer')['origins'].clear()
        self.assertEqual(self.records(data)[0]['force'],[0,0,-8])

    def test_unreceived_opaque_scaled_reflected_and_cyclic_frames_are_ignored(self):
        changes=[lambda d:node(d,'a0')['origins'].clear(),
                 lambda d:node(d,'root')['origins'].clear(),
                 lambda d:node(d,'a0')['properties'].update(CFrame={'$cframe':{'rotation_id':'unknown'}}),
                 lambda d:node(d,'a0')['properties'].update(CFrame=frame([2,0,0,0,1,0,0,0,1])),
                 lambda d:node(d,'a0')['properties'].update(CFrame=frame([-1,0,0,0,1,0,0,0,1])),
                 lambda d:node(d,'a0').update(parent='a0'),
                 lambda d:node(d,'a0').update(class_name='Bone')]
        for change in changes:
            data=force_world();change(data)
            self.assertEqual(self.records(data),[])

    def test_disabled_unresolved_external_owner_and_off_center_sources_are_ignored(self):
        changes=[lambda d:node(d,'force')['properties'].update(Enabled=False),
                 lambda d:node(d,'force')['properties'].update(ApplyAtCenterOfMass=False),
                 lambda d:node(d,'a0').update(parent='reference'),
                 lambda d:node(d,'force')['properties'].update(RelativeTo=1,Attachment1={'$ref':'missing'})]
        for change in changes:
            data=force_world();change(data)
            self.assertEqual(self.records(data),[])

    def controller(self,data):
        controller=OwnCharacterPhysicsController(data,geometry=GroundGeometry((),()),gravity=0.)
        controller.update_force_sources(self.records(data))
        return controller

    def test_local_force_tracks_predicted_yaw_and_does_not_steer_its_own_character(self):
        controller=self.controller(force_world())
        controller.step(.1)
        self.assertEqual(controller.yaw,0)
        self.assertAlmostEqual(controller.linear_velocity[0],.4)
        controller.yaw=math.pi/2
        acceleration=controller._force_acceleration()
        self.assertAlmostEqual(acceleration[0],0)
        self.assertAlmostEqual(acceleration[2],-4)

    def test_facing_uses_requested_movement_even_with_stronger_sideways_force(self):
        controller=self.controller(force_world())
        controller._impulse_velocity=(100,0,0)
        controller.consume([{'sequence':1,'kind':'Move','instance_id':'humanoid','vector':[0,0,-1]}])
        controller.step(.1)
        self.assertAlmostEqual(controller.yaw,0)
        self.assertGreater(controller.linear_velocity[0],controller.linear_velocity[2])

    def test_script_bridge_refreshes_direction_and_removes_disabled_force(self):
        bridge=PhysicsScriptBridge(lambda data:OwnCharacterPhysicsController(
            data,geometry=GroundGeometry((),()),gravity=0.))
        data=force_world(1);original=copy.deepcopy(data)
        bridge.update(data)
        self.assertEqual(bridge.controller._force_acceleration(),(0,0,-4))
        changed=copy.deepcopy(data);node(changed,'reference')['properties']['CFrame']=frame(Z90)
        bridge.update(changed)
        self.assertEqual(bridge.controller._force_acceleration(),(0,4,0))
        node(changed,'force')['properties']['Enabled']=False
        bridge.update(changed)
        self.assertEqual(bridge.controller._force_acceleration(),(0,0,0))
        self.assertEqual(data,original)


if __name__=='__main__':
    unittest.main()
