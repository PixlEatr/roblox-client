import copy
import math
import unittest

from isr_sender import (UNKNOWN_OFFSET, OwnedIsrSender, encode_packets,
                        encode_pv, encode_record)
from isr_replication import decode_packet
from owned_part_poses import OwnedPartPoses
from test_isr_replication import fixture as schema_fixture
from test_owned_part_poses import fixture, mutation, IDENTITY


def prepared_world():
    world, key = fixture()
    start = len(world.schema['properties'])
    world.schema['properties'].extend(copy.deepcopy(schema_fixture().schema['properties']))
    world.schema['classes'][world.instances[key]['class']]['isr_properties'] = list(range(start, start+3))
    return world, key


class IsrSenderTests(unittest.TestCase):
    def test_explicit_null_root_uses_schema_order_and_preserves_pose(self):
        for order in ([0,1,2], [2,1,0]):
            schema = schema_fixture().schema
            schema['classes'][0]['isr_properties'] = order
            pv = encode_pv((20.,420.,67.), IDENTITY, (0.,0.,0.), (0.,0.,0.))
            raw, = encode_packets([encode_record(schema,(1,42),0,pv,
                                  explicit_null_root=True)],local_step=50)
            fields = decode_packet(raw,schema,legacy_pv_enabled=True)['records'][0]['fields']
            self.assertEqual(set(fields), {'PhysicsRepRootPart','ReplicationPV'})
            self.assertIsNone(fields['PhysicsRepRootPart'])
            for actual,expected in zip(fields['ReplicationPV']['position'],(20.,420.,67.)):
                self.assertAlmostEqual(actual,expected,delta=.01)

    def test_null_root_experiment_never_detaches_received_roots_or_uses_unknown_creation(self):
        for case in ('fresh','unknown_creation','root_part','root_ref','newer_null_root','anchored','unowned'):
            with self.subTest(case=case):
                world,key=prepared_world()
                world.creation[key]={'fresh':True,'complete':True,'ordinary':True}
                if case=='unknown_creation':world.creation.pop(key)
                if case=='root_part':world.instances[key]['properties']['PhysicsRepRootPart']=key
                if case=='root_ref':world.instances[key]['properties']['PhysicsRepRootRef']=key
                if case=='anchored':world.instances[key]['properties']['Anchored']=True
                queue=OwnedPartPoses();queue.observe(world,[mutation(key)],world.revision)
                if case=='newer_null_root':
                    world.instances[key]['properties']['PhysicsRepRootPart']=None
                    world.touch(key,['PhysicsRepRootPart'])
                if case=='unowned':world.instances[key]['properties']['NetworkOwnerV3']=2
                before=copy.deepcopy(world.instances);sent=[]
                sender=OwnedIsrSender(queue,explicit_null_root=True)
                sender.send(world,local_step=1,legacy_pv_enabled=True,
                            send_unreliable=lambda *a,**kw:sent.append(a))
                self.assertEqual(len(sent), int(case=='fresh'))
                self.assertEqual(world.instances,before)

    def test_schema_directed_round_trip_preserves_pose_and_signed_offset(self):
        schema = schema_fixture().schema
        pv = encode_pv((67.,350.,67.), IDENTITY, (1.,-2.,3.), (0.,0.,0.))
        record = encode_record(schema, (1,42), 0, pv)
        raw, = encode_packets([record], local_step=1234, remote_to_local_offset=250)
        out = decode_packet(raw, schema, legacy_pv_enabled=True)
        self.assertEqual((out['timestamp'],out['header_value']), (1234,-250))
        self.assertEqual(out['records'][0]['key'], (1,42))
        fields = out['records'][0]['fields']
        self.assertEqual(list(fields), ['ReplicationPV'])
        for a,b in zip(fields['ReplicationPV']['position'], (67.,350.,67.)):
            self.assertAlmostEqual(a,b,delta=.01)
        for a,b in zip(fields['ReplicationPV']['decoded_velocities'][0], (1.,-2.,3.)):
            self.assertAlmostEqual(a,b,delta=.01)

    def test_datagrams_are_bounded_and_preserve_all_record_order(self):
        schema=schema_fixture().schema
        pv=encode_pv((20.,420.,67.), IDENTITY, (0.,0.,0.), (0.,0.,0.))
        records=[encode_record(schema,(1,i),0,pv) for i in range(100)]
        packets=encode_packets(records,local_step=2)
        self.assertGreater(len(packets),1)
        self.assertTrue(all(len(p)<=1100 for p in packets))
        decoded=[decode_packet(p,schema,legacy_pv_enabled=True) for p in packets]
        self.assertTrue(all(p['header_value']==UNKNOWN_OFFSET for p in decoded))
        self.assertEqual([r['key'][1] for p in decoded for r in p['records']],list(range(100)))

    def test_invalid_clock_or_pose_is_never_coerced(self):
        for step in (True,-1,2**32,1.5):
            with self.assertRaises(ValueError):encode_packets([],local_step=step)
        for offset in (True,0x7fffffff,-0x80000000,math.nan):
            with self.assertRaises(ValueError):encode_packets([],local_step=0,remote_to_local_offset=offset)
        reflected=IDENTITY[:];reflected[0]=-1
        with self.assertRaises(ValueError):encode_pv((1.,2.,3.),reflected,(0.,0.,0.),(0.,0.,0.))
        with self.assertRaises(ValueError):encode_pv((math.nan,2.,3.),IDENTITY,(0.,0.,0.),(0.,0.,0.))
        with self.assertRaises(ValueError):encode_record(schema_fixture().schema,(0xffffffff,1),0,b'\0')

    def test_only_current_owned_unanchored_isolated_parts_are_sent(self):
        for changed in ('NetworkOwnerV3','Anchored',None):
            world,key=prepared_world();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
            if changed:world.instances[key]['properties'][changed] = 2 if changed=='NetworkOwnerV3' else True
            before=copy.deepcopy(world.instances);sent=[]
            s=OwnedIsrSender(q)
            s.send(world,local_step=12,legacy_pv_enabled=True,
                   send_unreliable=lambda channel,payload,**kw:sent.append((channel,payload,kw)))
            self.assertEqual(world.instances,before)
            self.assertEqual(len(sent),int(changed is None))
            if sent:
                self.assertEqual((sent[0][0],sent[0][2]),(5,{'application':4}))
                decoded=decode_packet(sent[0][1],world.schema,legacy_pv_enabled=True)
                self.assertEqual(decoded['records'][0]['key'],key)
                self.assertFalse(q.pending)
                self.assertFalse(s.report()['server_acceptance_confirmed'])

    def test_feature_gate_and_ambiguous_send_do_not_commit_or_retry(self):
        world,key=prepared_world();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
        s=OwnedIsrSender(q);calls=[]
        def fail(*a,**kw):calls.append(1);raise OSError('transport')
        s.send(world,local_step=1,legacy_pv_enabled=False,send_unreliable=fail)
        self.assertFalse(calls)
        for step in (1,2):s.send(world,local_step=step,legacy_pv_enabled=True,send_unreliable=fail)
        self.assertEqual(calls,[1]);self.assertIn(key,q.pending)
        self.assertEqual(s.report()['packets_submitted'],0)

    def test_clock_and_session_boundaries(self):
        world,key=prepared_world();q=OwnedPartPoses();s=OwnedIsrSender(q)
        for step in (0xfffffff0,5):
            s.send(world,local_step=step,legacy_pv_enabled=True,send_unreliable=lambda *a,**kw:None)
        with self.assertRaises(ValueError):
            s.send(world,local_step=4,legacy_pv_enabled=True,send_unreliable=lambda *a,**kw:None)
        other,_=prepared_world()
        with self.assertRaises(ValueError):
            s.send(other,local_step=6,legacy_pv_enabled=True,send_unreliable=lambda *a,**kw:None)

    def test_warming_up_prediction_preserves_queue_until_clock_exists(self):
        world,key=prepared_world();q=OwnedPartPoses();q.observe(world,[mutation(key)],world.revision)
        sender=OwnedIsrSender(q);calls=[]
        for prediction in (None,{}, {'controller':None}):
            r=sender.tick_prediction(world,prediction,legacy_pv_enabled=True,
                                     send_unreliable=lambda *a,**kw:calls.append(a))
            self.assertIsNone(r['clock']);self.assertIn(key,q.pending)
        self.assertFalse(calls)
        sender.tick_prediction(world,{'simulation_step_clock':{'step':42}},legacy_pv_enabled=True,
                               send_unreliable=lambda *a,**kw:calls.append(a))
        self.assertEqual(len(calls),1);self.assertFalse(q.pending)

    def test_header_offset_changes_only_after_real_delivery_callback_and_remote_step(self):
        world,key=prepared_world();q=OwnedPartPoses();sender=OwnedIsrSender(q)
        callbacks=[];packets=[]
        def transport(channel,payload,**kwargs):
            packets.append(payload);callbacks.append(kwargs['on_delivery'])
        sender.observe_remote(world,1500)
        q.observe(world,[mutation(key)],world.revision)
        sender.send(world,local_step=1000,legacy_pv_enabled=True,send_unreliable=transport,track_delivery=True)
        self.assertEqual(decode_packet(packets[-1],world.schema,legacy_pv_enabled=True)['header_value'],UNKNOWN_OFFSET)
        self.assertIsNone(sender.clock.offset)
        callbacks[0]('acknowledged')
        sender.observe_remote(world,1510)
        q.observe(world,[mutation(key)],world.revision)
        sender.send(world,local_step=1010,legacy_pv_enabled=True,send_unreliable=transport,track_delivery=True)
        self.assertEqual(decode_packet(packets[-1],world.schema,legacy_pv_enabled=True)['header_value'],-510)
        self.assertEqual(sender.report()['clock_estimator']['acknowledgments'],1)
        self.assertFalse(sender.report()['server_acceptance_confirmed'])


if __name__=='__main__':unittest.main()
