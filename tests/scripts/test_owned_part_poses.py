import copy
import unittest
from unittest.mock import patch

from owned_part_poses import OwnedPartPoses, _constructor_velocities, _velocities
from physics_packet import decode_transform_component
from physics_root_record import encode_instance_reference
from test_live_physics_trial import ready_world, PredictionStub
from live_character_physics import LiveCharacterPhysics


IDENTITY = [1.,0.,0.,0.,1.,0.,0.,0.,1.]


def fixture():
    world, root = ready_world()
    part = (1, 50)
    world.instances[part] = {'class': 3, 'parent': (1,6), 'properties': {
        'Name': b'AuthoredPart', 'Anchored': False, 'NetworkOwnerV3': world.client_scope,
        'ReplicationPV': {'position': [0., 10., 0.], 'rotation': 1,
                          'packed_velocities': [None, [0, 0]]}}}
    world.touch(part, ['Anchored', 'NetworkOwnerV3', 'ReplicationPV'])
    return world, part


def mutation(key, position=(20.,420.,67.)):
    return {'kind':'set','id':'%d:%d'%key,'property':'CFrame',
            'value':{'$cframe':{'position':list(position),'rotation':IDENTITY[:]}}}


class OwnedPartPoseTests(unittest.TestCase):
    def test_constructor_defaults_are_materialized_before_network_pose_preparation(self):
        _constructor_velocities.cache_clear()
        data='{"classes":{"Part":{"properties":{"AssemblyLinearVelocity":{"value":{"$vector3":[0,0,0]}},"AssemblyAngularVelocity":{"value":{"$vector3":[0,0,0]}}}}}}'
        try:
            world,key=fixture()
            del world.instances[key]['properties']['ReplicationPV']
            world.creation[key]={'fresh':True,'complete':True,'ordinary':True}
            with patch('owned_part_poses.Path.read_text',return_value=data) as read:
                queue=OwnedPartPoses()
                self.assertEqual(read.call_count,1)
                read.side_effect=OSError('file unavailable after session startup')
                values,source=_velocities(world.instances[key]['properties'],world.creation[key],'Part')
                self.assertEqual(values,[[0,0,0],[0,0,0]])
                self.assertEqual(source,'local_constructor_initial_state')
                self.assertEqual(read.call_count,1)
        finally:
            _constructor_velocities.cache_clear()

    def test_authored_pose_and_received_velocities_survive_wire_codec(self):
        world, key = fixture();before=copy.deepcopy(world.instances)
        queue=OwnedPartPoses();queue.observe(world,[mutation(key)],world.revision)
        records,keys=queue.prepare(world)
        self.assertEqual(keys,[key]);self.assertEqual(world.instances,before)
        prefix=encode_instance_reference(world.client_scope,key)+b'\x00\x01\x20\x00'
        self.assertTrue(records[0].startswith(prefix))
        component=decode_transform_component(records[0][len(prefix):-1],velocity_mode='compact')
        for actual,expected in zip(component.position,(20.,420.,67.)):
            self.assertAlmostEqual(actual,expected,delta=.1)
        self.assertEqual(component.linear_velocity.vector,(0.,0.,0.))
        queue.acknowledge(keys);self.assertEqual(queue.report()['records_submitted'],1)
        self.assertEqual(queue.report()['recent_submitted_poses'],[{'id':'%d:%d'%key,'position':[20.,420.,67.]}])
        self.assertFalse(queue.report()['server_acceptance_confirmed'])

    def test_authority_rechecked_after_submission(self):
        for field,value in [('NetworkOwnerV3',2),('Anchored',True)]:
            with self.subTest(field=field):
                world,key=fixture();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
                world.instances[key]['properties'][field]=value
                self.assertEqual(q.prepare(world),([],[]))
                self.assertFalse(q.pending)

    def test_received_pose_supersedes_old_worker_write(self):
        world,key=fixture();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
        world.touch(key,['ReplicationPV'])
        self.assertEqual(q.prepare(world),([],[]))
        self.assertEqual(q.report()['rejected']['newer_received_pose'],1)

    def test_characters_connected_and_unparented_parts_are_excluded(self):
        for case in ('character','joint','unparented','other_root'):
            with self.subTest(case=case):
                world,key=fixture();q=OwnedPartPoses()
                if case=='character':world.instances[key]['parent']=(1,3)
                if case=='unparented':world.instances[key]['parent']=None
                if case=='other_root':world.instances[key]['properties']['PhysicsRepRootPart']=(1,4)
                if case=='joint':
                    world.schema['classes'].append({'name':'WeldConstraint','properties':[]})
                    world.instances[(1,51)]={'class':len(world.schema['classes'])-1,
                                            'properties':{'Part0':key,'Part1':(1,4)}}
                q.observe(world,[mutation(key)],world.revision)
                self.assertEqual(q.prepare(world),([],[]))

    def test_unknown_velocity_and_nonrotation_fail_without_zero_substitution(self):
        world,key=fixture();q=OwnedPartPoses()
        del world.instances[key]['properties']['ReplicationPV']['packed_velocities']
        q.observe(world,[mutation(key)],world.revision);self.assertEqual(q.prepare(world),([],[]))
        change=mutation(key);change['value']['$cframe']['rotation']=[0]*9
        q.observe(world,[change],world.revision);self.assertFalse(q.pending)

    def test_coalescing_and_session_isolation(self):
        world,key=fixture();q=OwnedPartPoses()
        q.observe(world,[mutation(key),mutation(key,(67.,350.,67.))],world.revision)
        self.assertEqual(len(q.pending),1)
        self.assertEqual(q.pending[key][0],(67.,350.,67.))
        other,_=fixture()
        with self.assertRaises(ValueError):q.prepare(other)

    def test_fresh_complete_creation_uses_labeled_local_constructor_state(self):
        world,key=fixture();q=OwnedPartPoses()
        del world.instances[key]['properties']['ReplicationPV']
        world.creation[key]={'fresh':True,'complete':True,'ordinary':True}
        q.observe(world,[mutation(key)],world.revision)
        self.assertEqual(q.prepare(world)[1],[key])
        self.assertEqual(q.report()['prepared_velocity_sources'],{'local_constructor_initial_state':1})
        world.creation[key]['complete']=False
        self.assertEqual(q.prepare(world),([],[]))

    def test_unowned_background_writes_cannot_fill_owned_pose_queue(self):
        world,key=fixture();q=OwnedPartPoses();changes=[]
        for index in range(100,200):
            other=(1,index);world.instances[other]=copy.deepcopy(world.instances[key])
            world.instances[other]['properties']['NetworkOwnerV3']=2
            changes.append(mutation(other))
        changes.append(mutation(key));q.observe(world,changes,world.revision)
        self.assertEqual(list(q.pending),[key])
        self.assertEqual(q.report()['rejected']['ownership_not_current_at_write'],100)

    def test_cached_topology_keeps_new_rigid_edges_and_character_changes(self):
        world,key=fixture();q=OwnedPartPoses()
        q.observe(world,[mutation(key)],world.revision)
        self.assertEqual(q.prepare(world)[1],[key])
        world.schema['classes'].append({'name':'WeldConstraint','properties':[]})
        joint=(1,51)
        world.instances[joint]={'class':len(world.schema['classes'])-1,
                                'properties':{'Part0':key,'Part1':(1,4)}}
        world.touch(joint,created=True)
        self.assertEqual(q.prepare(world),([],[]))
        del world.instances[joint];world.touch(joint,deleted=True)
        q.observe(world,[mutation(key)],world.revision)
        self.assertEqual(q.prepare(world)[1],[key])
        world.instances[(1,2)]['properties']['Character']=key
        world.touch((1,2),['Character'])
        self.assertEqual(q.prepare(world),([],[]))
        self.assertEqual(q.report()['rejected']['character_pose_excluded'],1)

    def test_topology_cache_reuses_pose_traffic_but_refreshes_after_journal_gap(self):
        import collections
        world,key=fixture();q=OwnedPartPoses()
        q.observe(world,[mutation(key)],world.revision)
        self.assertEqual(q.prepare(world)[1],[key])
        connected=q._topology_connected
        world.touch(key,['Color'])
        self.assertEqual(q.prepare(world)[1],[key])
        self.assertIs(q._topology_connected,connected)
        world.actions=collections.deque(world.actions,maxlen=1)
        world.schema['classes'].append({'name':'WeldConstraint','properties':[]})
        joint=(1,51)
        world.instances[joint]={'class':len(world.schema['classes'])-1,
                                'properties':{'Part0':key,'Part1':(1,4)}}
        world.touch(joint,created=True)
        world.touch(key,['Color'])
        self.assertEqual(q.prepare(world),([],[]))
        self.assertIsNot(q._topology_connected,connected)

    def test_cached_topology_rechecks_existing_joint_endpoints(self):
        world,key=fixture();q=OwnedPartPoses()
        world.schema['classes'].append({'name':'WeldConstraint','properties':[]})
        joint=(1,51)
        world.instances[joint]={'class':len(world.schema['classes'])-1,
                                'properties':{'Part0':(1,100),'Part1':(1,4)}}
        world.touch(joint,created=True)
        q.observe(world,[mutation(key)],world.revision)
        self.assertEqual(q.prepare(world)[1],[key])
        world.instances[joint]['properties']['Part0']=key
        world.touch(joint,['Part0'])
        self.assertEqual(q.prepare(world),([],[]))

    def test_single_packet_sequence_contains_character_and_authored_part(self):
        world,key=fixture();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
        driver=LiveCharacterPhysics(experimental_enabled=True,prediction=PredictionStub())
        driver.authored_parts=q;sent=[];sequence=driver.report()['sequence']
        report=driver.tick(world=world,own_user_id=123,now=1.,delta=.05,timestamp_ms=1000,
                           connected=True,send_unreliable=lambda *a,**kw:sent.append(a[1]))
        self.assertEqual(len(sent),1)
        self.assertIn(encode_instance_reference(world.client_scope,key)+b'\x00\x01\x20',sent[0])
        self.assertEqual(report['authored_part_poses']['records_submitted'],1)
        self.assertEqual(report['sequence'],sequence+1)

    def test_failed_send_never_counts_part_record_as_submitted(self):
        world,key=fixture();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
        driver=LiveCharacterPhysics(experimental_enabled=True,prediction=PredictionStub())
        driver.authored_parts=q
        report=driver.tick(world=world,own_user_id=123,now=1.,delta=.05,timestamp_ms=1000,
                           connected=True,send_unreliable=lambda *a,**kw:False)
        self.assertEqual(report['authored_part_poses']['records_submitted'],0)
        self.assertEqual(report['stop_reason'],'send_result_ambiguous_no_retry')


if __name__=='__main__':unittest.main()
