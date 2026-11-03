import copy
import struct
import unittest

from remote_client_rpc import RemoteClientRpc
from test_replication_rpc import fixture
from world_replication import World
from world_snapshot import _encode
from replication_out import guid
from login_packets import encode_signed


class RemoteClientRpcTests(unittest.TestCase):
    def setUp(self):
        self.world,_,self.keys=fixture()
        self.target=self.keys[3]
        self.rpc=RemoteClientRpc()
        self.sent=[]

    def receive(self,request=7):
        self.world.events.append({'key':self.target,'event':0,'arguments':[request,[]]})
        self.rpc.observe(self.world)

    def reply(self,**extra):
        return {'node':f'{self.target[0]}:{self.target[1]}','request_id':7,'ok':True,'values':[],**extra}

    def send(self,channel,payload):
        self.sent.append((channel,payload))

    def decoded(self):
        world=World(copy.deepcopy(self.world.schema));world.packet(self.sent[-1][1])
        return world.events[-1]

    def test_correlated_primitive_reply_round_trips_and_cannot_be_sent_twice(self):
        self.receive();self.rpc.observe(self.world)
        values=[None,False,True,12.5,'hello',{'$bytes_hex':'00ff'},
                {'$ref':f'{self.keys[5][0]}:{self.keys[5][1]}'},None]
        reply=self.reply(values=values);self.rpc.send_reply(self.world,reply,self.send)
        self.assertEqual(self.decoded(),{'key':self.target,'event':3,
            'arguments':[7,[None,False,True,12.5,b'hello',b'\x00\xff',self.keys[5],None]]})
        self.assertEqual(self.sent[0][0],1)
        with self.assertRaises(ValueError):self.rpc.send_reply(self.world,reply,self.send)
        self.assertEqual(self.rpc.summary(),{'requests_received':1,'replies_sent':1,
                                            'error_replies_sent':0,'pending_requests':0})

    def test_error_reply_preserves_actual_callback_error(self):
        self.receive();self.rpc.send_reply(self.world,self.reply(ok=False,error='callback failed'),self.send)
        self.assertEqual(self.decoded()['arguments'],[7,b'callback failed'])
        self.assertEqual(self.decoded()['event'],1)
        self.assertEqual(self.rpc.errors_sent,1)

    def test_unsupported_values_reply_with_explicit_error(self):
        self.receive();self.rpc.send_reply(self.world,self.reply(values=[{'$unknown_datatype':[1,2,3]}]),self.send)
        self.assertEqual(self.decoded()['event'],1)
        self.assertIn(b'datatype',self.decoded()['arguments'][1])

    def test_unreceived_and_cross_session_replies_are_not_sent(self):
        self.rpc.observe(self.world)
        with self.assertRaises(ValueError):self.rpc.send_reply(self.world,self.reply(),self.send)
        self.receive()
        other,_,_=fixture()
        with self.assertRaises(ValueError):self.rpc.send_reply(other,self.reply(),self.send)
        with self.assertRaises(ValueError):self.rpc.send_reply(self.world,self.reply(request_id=8),self.send)
        with self.assertRaises(ValueError):self.rpc.send_reply(self.world,self.reply(node='local:1'),self.send)
        self.assertFalse(self.sent)

    def test_send_failure_does_not_acknowledge_or_lose_request(self):
        self.receive()
        def fail(*args):raise OSError('transport unavailable')
        with self.assertRaises(OSError):self.rpc.send_reply(self.world,self.reply(),fail)
        self.assertEqual(len(self.rpc.pending),1);self.assertEqual(self.rpc.sent,0)
        self.rpc.send_reply(self.world,self.reply(),self.send)
        self.assertEqual(self.rpc.sent,1)

    def test_unknown_reference_returns_error_not_unrelated_instance(self):
        self.receive();self.rpc.send_reply(self.world,self.reply(values=[{'$ref':'999:999'}]),self.send)
        self.assertEqual(self.decoded()['event'],1)
        self.assertIn(b'not received',self.decoded()['arguments'][1])

    def test_schema_mismatch_does_not_send(self):
        self.receive();self.world.schema['events'][3]['arguments'][1]['type']=30
        with self.assertRaises(ValueError):self.rpc.send_reply(self.world,self.reply(),self.send)
        self.assertFalse(self.sent)

    def test_duplicate_pending_request_and_journal_overrun_are_explicit(self):
        self.receive()
        with self.assertRaises(ValueError):self.receive()
        self.world.events.clear()
        with self.assertRaises(ValueError):self.rpc.observe(self.world)

    def test_received_variant_types_are_not_silently_changed(self):
        # Two received variants: an enum and a Vector3. Neither may become a
        # primitive number/table while crossing into the callback VM.
        body=b'\x02\x07'+struct.pack('<H',0)+b'\x05\x16'+struct.pack('<fff',1,2,3)
        packet=b'\x83\x07'+guid(self.target)+struct.pack('<H',0)+encode_signed(7)+body+b'\0'
        self.world.packet(packet)
        self.rpc.observe(self.world)
        args=self.world.events[-1]['arguments']
        self.assertEqual(_encode(args[1],29),[{'$unsupported_remote_type':7},
                                             {'$vector3':[1.,2.,3.]}])


if __name__=='__main__':unittest.main()
