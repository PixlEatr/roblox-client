import copy
import math
import unittest

from owned_part_contacts import OwnedPartContacts, boxes_overlap, encode_contacts
from isr_sender import OwnedIsrSender
from owned_part_poses import OwnedPartPoses
from test_isr_sender import prepared_world
from test_owned_part_poses import IDENTITY, mutation


def fixture():
    world, source = prepared_world()
    workspace = world.instances[source]['parent']
    target, listener = (1, 51), (1, 52)
    world.instances[target] = {'class': world.instances[source]['class'], 'parent': workspace,
        'properties': {'Anchored': True, 'NetworkOwnerV3': 1, 'size': [4, 1, 2],
                       'CFrame': {'position': [20, 420, 67], 'rotation': IDENTITY[:]}}}
    world.schema['classes'].append({'name': 'TouchTransmitter', 'properties': []})
    world.instances[listener] = {'class': len(world.schema['classes'])-1,
                                 'parent': target, 'properties': {}}
    for key in (source, workspace, target):
        world.creation[key] = {'fresh': True, 'complete': True, 'ordinary': key == source}
    return world, source, target, listener


class ContactTests(unittest.TestCase):
    def test_wire_has_two_scoped_guids_transition_and_null_terminator(self):
        self.assertEqual(encode_contacts([((1,42),(130,7),True)]),
                         bytes.fromhex('86 01 2a000000 8201 07000000 00 00'))
        self.assertEqual(encode_contacts([((1,42),(1,7),False)])[-2:], b'\x03\x00')
        for rows in ([], [((0,1),(1,2),True)], [((1,1),(1,1),True)],
                     [((1,1),(1,2),1)], [((1,1),(1,2),True)]*65):
            with self.assertRaises(ValueError): encode_contacts(rows)

    def test_box_overlap_rotated_boxes_rejects_aabb_false_positive(self):
        c = math.sqrt(.5)
        rotation = [c,-c,0,c,c,0,0,0,1]
        box = ([0,0,0],rotation,[4,.1,.1])
        self.assertFalse(boxes_overlap(box,([0,1,0],rotation,[4,.1,.1])))
        self.assertTrue(boxes_overlap(box,([.01,.01,0],rotation,[4,.1,.1])))
        self.assertFalse(boxes_overlap(box,([0,0,1],IDENTITY,[1,1,1])))
        with self.assertRaises(ValueError): boxes_overlap(box,([0,0,0],IDENTITY,[0,1,1]))

    def test_submitted_poses_generate_one_begin_and_one_end(self):
        world, key, target, listener = fixture()
        before = copy.deepcopy(world.instances)
        contacts = OwnedPartContacts(); sent = []
        def send(*args, **kwargs): sent.append((args, kwargs))
        for position in ([20,420,67], [20,420,67], [67,350,67], [67,350,67]):
            contacts.observe_submitted(world, {key: (position, IDENTITY, world.revision)}, send)
        self.assertEqual(len(sent),2)
        self.assertEqual(sent[0], ((7,encode_contacts([(key,target,True)])),{'application':4}))
        self.assertEqual(sent[1][0][1], encode_contacts([(key,target,False)]))
        self.assertEqual(world.instances,before)
        self.assertEqual(contacts.report()['begins_submitted'],1)
        self.assertEqual(contacts.report()['ends_submitted'],1)
        self.assertFalse(contacts.report()['server_acceptance_confirmed'])

    def test_unsupported_or_unknown_state_never_fabricates_contacts(self):
        for case in ('owner','source_anchor','target_unanchored','cantouch','shape',
                     'creation','size','listener','workspace','groups','newer_pose'):
            with self.subTest(case=case):
                world,key,target,listener=fixture(); contacts=OwnedPartContacts(); sent=[]
                props=world.instances[key]['properties']
                if case=='owner':props['NetworkOwnerV3']=99
                if case=='source_anchor':props['Anchored']=True
                if case=='target_unanchored':world.instances[target]['properties']['Anchored']=False
                if case=='cantouch':props['CanTouch']=False
                if case=='shape':props['shape']=0
                if case=='creation':world.creation.pop(key)
                if case=='size':props['size']=[0,1,1]
                if case=='listener':world.instances.pop(listener)
                if case=='workspace':world.instances[target]['parent']=None
                if case=='groups':world.instances[world.instances[key]['parent']]['properties']['TouchesUseCollisionGroups']=True
                if case=='newer_pose':
                    world.instances[target]['properties']['ReplicationPV']={'position':[1000,1000,1000],'rotation':1}
                    world.touch(target,['ReplicationPV'])
                contacts.observe_submitted(world,{key:([20,420,67],IDENTITY,world.revision)},
                    lambda *a,**kw:sent.append(a))
                self.assertFalse(sent)

    def test_failure_disables_retry_and_session_reuse_is_rejected(self):
        world,key,_,_=fixture(); c=OwnedPartContacts(); calls=[]
        def fail(*a,**kw):calls.append(a);raise OSError('transport')
        for _ in range(2):c.observe_submitted(world,{key:([20,420,67],IDENTITY,0)},fail)
        self.assertEqual(len(calls),1);self.assertFalse(c.active)
        self.assertEqual(c.report()['packets_submitted'],0)
        other,*_=fixture()
        with self.assertRaises(ValueError):c.observe_submitted(other,{},fail)

    def test_isr_commit_precedes_contact_and_failed_pose_never_touches(self):
        for failure in (False,True):
            world,key,target,_=fixture();q=OwnedPartPoses();s=OwnedIsrSender(q);calls=[]
            q.observe(world,[mutation(key)],world.revision)
            def pose(*a,**kw):calls.append('pose');return not failure
            def contact(*a,**kw):calls.append('contact')
            s.send(world,local_step=1,legacy_pv_enabled=True,send_unreliable=pose,send_reliable=contact)
            self.assertEqual(calls,['pose'] if failure else ['pose','contact'])

    def test_lost_ownership_drops_previous_pair_without_spurious_end(self):
        world,key,_,_=fixture();c=OwnedPartContacts();sent=[]
        send=lambda *a,**kw:sent.append(a)
        c.observe_submitted(world,{key:([20,420,67],IDENTITY,0)},send)
        world.instances[key]['properties']['NetworkOwnerV3']=100
        c.observe_submitted(world,{key:([67,350,67],IDENTITY,0)},send)
        self.assertEqual(len(sent),1);self.assertFalse(c.active)


if __name__=='__main__':unittest.main()
