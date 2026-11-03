import copy
import struct
import os
import time
import unittest
from unittest import mock

from persistent_runtime import PersistentLuauHost
from test_cframe_transforms import SNAPSHOT


class RemoteClientCallbackTests(unittest.TestCase):
    def setUp(self):
        snapshot=copy.deepcopy(SNAPSHOT)
        snapshot['nodes'].append({'id':'1:77','class_name':'RemoteFunction','name':'Callback',
                                 'parent':'scope:workspace','properties':{}})
        self.snapshot=snapshot
        self.host=PersistentLuauHost(snapshot)
        self.addCleanup(self.host.close)
        self.sequence=int(snapshot.get('sequence',0))

    def execute(self,source):
        result=self.host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertTrue(result['ok'],result)
        return result

    def invoke(self,request,args):
        self.sequence+=1
        result=self.host.call({'op':'update','timeout_ms':50,'delta':{
            'version':1,'delta':True,'actions':[{'sequence':self.sequence,'kind':'event',
            'id':'1:77','name':'RemoteOnInvokeClient','arguments':[request,args]}]}})
        self.assertTrue(result['ok'],result)
        return result

    def reply(self,result):
        for _ in range(30):
            replies=result.get('remote_client_replies',[])
            if replies:
                self.assertEqual(len(replies),1)
                return replies[0]
            time.sleep(.002)
            result=self.host.call({'op':'poll','timeout_ms':20})
        self.fail('received callback produced no reply')

    def test_callback_storage_replacement_nil_and_write_only_property(self):
        self.execute('''
local r=workspace.Callback
local function first()return 1 end
r.OnClientInvoke=first
assert(getcallbackvalue(r,"OnClientInvoke")==first)
assert(not pcall(function()return r.OnClientInvoke end))
assert(not pcall(function()r.OnClientInvoke=7 end))
assert(getcallbackvalue(r,"OnClientInvoke")==first)
r.OnClientInvoke=function()return 2 end
assert(getcallbackvalue(r,"OnClientInvoke")()==2)
r.OnClientInvoke=nil;assert(getcallbackvalue(r,"OnClientInvoke")==nil)
''')

    def test_received_callback_yields_and_preserves_tuple_holes_and_bytes(self):
        self.execute('''workspace.Callback.OnClientInvoke=function(...)
assert(select("#",...)==5)
local a,b,c,d,e=...
assert(a=="hello" and b==nil and c==false and d==42 and e==string.char(255))
task.wait(.005)
return a,nil,c,d,e,nil
end''')
        reply=self.reply(self.invoke(17,['hello',None,False,42,{'$bytes_hex':'ff'}]))
        self.assertEqual(reply,{'node':'1:77','request_id':17,'ok':True,
                               'values':['hello',None,False,42,{'$bytes_hex':'ff'},None]})
        self.assertEqual(self.host.call({'op':'poll','timeout_ms':20})['remote_client_replies'],[])

    def test_request_waits_for_late_callback_registration(self):
        result=self.invoke(18,['late'])
        self.assertEqual(result['remote_client_replies'],[])
        result=self.execute('workspace.Callback.OnClientInvoke=function(x)return x end')
        self.assertEqual(self.reply(result)['values'],['late'])

    def test_actual_errors_and_unsupported_return_types_are_error_replies(self):
        self.execute('workspace.Callback.OnClientInvoke=function()error("actual callback error")end')
        reply=self.reply(self.invoke(-10,[]));self.assertFalse(reply['ok'])
        self.assertIn('actual callback error',reply['error']);self.assertNotIn('values',reply)
        self.execute('workspace.Callback.OnClientInvoke=function()return {} end')
        reply=self.reply(self.invoke(19,[]));self.assertTrue(reply['ok'])
        self.assertEqual(reply['values'],[{'$remote_array':[]}])
        self.execute('workspace.Callback.OnClientInvoke=function()return Ray.new(Vector3.zero,Vector3.xAxis) end')
        reply=self.reply(self.invoke(190,[]));self.assertFalse(reply['ok'])
        self.assertIn('datatype',reply['error'])

    def test_unsupported_argument_does_not_run_callback(self):
        self.execute('calls=0;workspace.Callback.OnClientInvoke=function()calls+=1 end')
        reply=self.reply(self.invoke(20,[[1,2,3]]));self.assertFalse(reply['ok'])
        self.execute('assert(calls==0)')

    def test_callback_replacement_does_not_change_received_inflight_call(self):
        self.execute('workspace.Callback.OnClientInvoke=function()task.wait(.01);return "old" end')
        first=self.invoke(21,[])
        replacement=self.execute('workspace.Callback.OnClientInvoke=function()return "new" end')
        result=first if first['remote_client_replies'] else replacement
        self.assertEqual(self.reply(result)['values'],['old'])
        self.assertEqual(self.reply(self.invoke(22,[]))['values'],['new'])

    def test_wire_request_executes_luau_and_correlated_reply_round_trips(self):
        from test_replication_rpc import fixture
        from remote_client_rpc import RemoteClientRpc,variant
        from replication_out import guid
        from login_packets import encode_signed
        from world_snapshot import _encode
        from world_replication import World
        world,_,keys=fixture();target=(1,77)
        world.instances[target]=world.instances.pop(keys[3])
        rpc=RemoteClientRpc()
        packet=b'\x83\x07'+guid(target)+struct.pack('<H',0)+encode_signed(23)+b'\x01'+variant(world,42)+b'\0'
        world.packet(packet);rpc.observe(world)
        self.execute('workspace.Callback.OnClientInvoke=function(x)task.wait(.001);return x+1,nil,"actual" end')
        args=world.events[-1]['arguments']
        reply=self.reply(self.invoke(args[0],_encode(args[1],29)))
        sent=[];rpc.send_reply(world,reply,lambda channel,payload:sent.append(payload))
        decoded=World(copy.deepcopy(world.schema));decoded.packet(sent[0])
        self.assertEqual(decoded.events[-1]['arguments'],[23,[43,None,b'actual']])

    def test_incoming_callback_gets_a_turn_with_full_background_batch(self):
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            self.host=PersistentLuauHost(self.snapshot)
        self.addCleanup(self.host.close)
        self.execute('''workspace.Callback.OnClientInvoke=function()return "reply" end
for i=1,128 do task.defer(function()while true do end end)end''')
        self.assertEqual(self.reply(self.invoke(24,[]))['values'],['reply'])


if __name__=='__main__':unittest.main()
