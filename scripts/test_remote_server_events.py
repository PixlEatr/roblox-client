import collections
import copy
import os
from pathlib import Path
import unittest
from unittest import mock

import persistent_runtime
from remote_server_events import RemoteServerEvents
from test_cframe_transforms import SNAPSHOT
from world_replication import World


def fixture():
    schema={'classes':[{'id':0,'name':'RemoteEvent','properties':[],'events':[0]}],
            'events':[{'id':0,'class':0,'name':'OnRemoteServerEvent','arguments':[{'type':29}]}]}
    world=World(schema);world.instances[(1,77)]={'class':0,'parent':None,'properties':{}}
    return world


class RemoteServerEventTests(unittest.TestCase):
    def host(self, class_name='RemoteEvent'):
        snapshot=copy.deepcopy(SNAPSHOT)
        snapshot['nodes'].append({'id':'1:77','class_name':class_name,'name':'Remote',
                                 'parent':'scope:workspace','properties':{}})
        binary=Path(os.environ.get('ROBLOX_LUAU_RUST_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',binary):
            host=persistent_runtime.PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def test_actual_luau_call_round_trips_tuple_without_hidden_player(self):
        host=self.host()
        result=host.call({'op':'execute','timeout_ms':100,'source': '''
local r=workspace.Remote
assert(select("#",r:FireServer("hello",nil,false,42,string.char(255),r,nil))==0)
return true
'''})
        self.assertTrue(result['ok'],result)
        self.assertEqual(len(result['remote_server_events']),1)
        world=fixture();sender=RemoteServerEvents();sent=[]
        sender.send(world,result['remote_server_events'][0],lambda channel,payload:sent.append((channel,payload)))
        self.assertEqual(sent[0][0],1)
        self.assertTrue(sent[0][1].startswith(bytes.fromhex('8307014d000000000007')))
        received=fixture();received.packet(sent[0][1])
        self.assertEqual(received.events[-1]['arguments'],[[b'hello',None,False,42.,b'\xff',(1,77),None]])
        self.assertEqual(sender.sent,1)
        self.assertFalse(sender.summary()['server_delivery_confirmed'])
        self.assertEqual(host.call({'op':'poll','timeout_ms':20})['remote_server_events'],[])

    def test_unreliable_luau_call_uses_datagram_channel_three_without_yielding(self):
        host=self.host('UnreliableRemoteEvent')
        result=host.call({'op':'execute','timeout_ms':100,'source':
            'return select("#",workspace.Remote:FireServer("startup",{newproxy(true),"message"}))'})
        self.assertEqual(result['values'],[0],result)
        world=fixture();world.schema['classes'][0]['name']='UnreliableRemoteEvent'
        reliable=[];datagrams=[];sender=RemoteServerEvents()
        sender.flush(world,collections.deque(result['remote_server_events']),
                     lambda *args:reliable.append(args),
                     unreliable_transport=lambda *args:datagrams.append(args))
        self.assertFalse(reliable);self.assertEqual(datagrams[0][0],3)
        decoder=World(copy.deepcopy(world.schema));decoder.packet(datagrams[0][1])
        self.assertEqual(decoder.events[-1]['arguments'],[[b'startup',[None,b'message']]])
        self.assertEqual(sender.summary()['unreliable_records_submitted'],1)
        self.assertFalse(sender.summary()['server_delivery_confirmed'])

    def test_unreliable_payload_boundary_drops_oversize_without_reliable_fallback(self):
        world=fixture();world.schema['classes'][0]['name']='UnreliableRemoteEvent'
        queue=collections.deque({'node':'1:77','arguments':['x'*n]} for n in (996,997,1))
        reliable=[];datagrams=[];sender=RemoteServerEvents()
        sender.flush(world,queue,lambda *args:reliable.append(args),
                     unreliable_transport=lambda *args:datagrams.append(args))
        self.assertEqual(len(datagrams),2);self.assertFalse(reliable);self.assertFalse(queue)
        self.assertEqual(sum(sender.rejected.values()),1)
        self.assertIn('1000',next(iter(sender.rejected)))

    def test_unreliable_send_failure_does_not_fall_back_or_report_submission(self):
        world=fixture();world.schema['classes'][0]['name']='UnreliableRemoteEvent'
        reliable=[];sender=RemoteServerEvents()
        with self.assertRaises(OSError):
            sender.send(world,{'node':'1:77','arguments':[]},lambda *args:reliable.append(args),
                        unreliable_transport=lambda *args:False)
        self.assertFalse(reliable);self.assertEqual(sender.sent,0)

    def test_rejected_native_calls_do_not_enqueue_partial_events(self):
        host=self.host()
        result=host.call({'op':'execute','timeout_ms':100,'source': '''
local r=workspace.Remote
for _,value in {function()end,Ray.new(Vector3.new(),Vector3.new(1,0,0)),Instance.new("Folder")} do
    assert(not pcall(function()r:FireServer("before",value)end))
end
assert(not pcall(function()Instance.new("RemoteEvent"):FireServer()end))
assert(not pcall(r.FireServer,workspace))
return true
'''})
        self.assertTrue(result['ok'],result);self.assertEqual(result['remote_server_events'],[])

    def test_bounded_native_queue_and_drain(self):
        host=self.host()
        result=host.call({'op':'execute','timeout_ms':100,'source': '''
for i=1,64 do workspace.Remote:FireServer(i)end
assert(not pcall(function()workspace.Remote:FireServer(65)end))
return true
'''})
        self.assertTrue(result['ok'],result);self.assertEqual(len(result['remote_server_events']),64)
        result=host.call({'op':'execute','timeout_ms':100,'source':'workspace.Remote:FireServer(66)'})
        self.assertTrue(result['ok'],result);self.assertEqual(result['remote_server_events'][0]['arguments'],[66])

    def test_invalid_wire_inputs_and_changed_receiver_are_not_sent(self):
        for change in ('removed','class','schema','local_ref','oversize','nan'):
            with self.subTest(change=change):
                world=fixture();event={'node':'1:77','arguments':[1]};sent=[]
                if change=='removed':world.instances.clear()
                if change=='class':world.schema['classes'][0]['name']='UnreliableRemoteEvent'
                if change=='schema':world.schema['events'][0]['arguments']=[{'type':28},{'type':29}]
                if change=='local_ref':event['arguments']=[{'$ref':'local:1'}]
                if change=='oversize':event['arguments']=['a'*65537]
                if change=='nan':event['arguments']=[float('nan')]
                sender=RemoteServerEvents();queue=collections.deque([event])
                sender.flush(world,queue,lambda *args:sent.append(args))
                self.assertFalse(sent);self.assertFalse(queue);self.assertEqual(sum(sender.rejected.values()),1)

    def test_session_binding_and_transport_failure_do_not_count_delivery(self):
        sender=RemoteServerEvents();world=fixture();event={'node':'1:77','arguments':[]}
        def fail(*args):raise OSError('test transport failure')
        with self.assertRaises(OSError):sender.send(world,event,fail)
        self.assertEqual(sender.sent,0)
        with self.assertRaises(ValueError):sender.send(fixture(),event,lambda *args:None)

    def test_flush_is_bounded_and_keeps_order(self):
        sender=RemoteServerEvents();world=fixture()
        queue=collections.deque({'node':'1:77','arguments':[i]} for i in range(20));sent=[]
        sender.flush(world,queue,lambda channel,payload:sent.append(payload))
        self.assertEqual(len(sent),16);self.assertEqual(len(queue),4)
        for i,payload in enumerate(sent):
            received=fixture();received.packet(payload)
            self.assertEqual(received.events[-1]['arguments'],[[i]])


if __name__=='__main__':unittest.main()
