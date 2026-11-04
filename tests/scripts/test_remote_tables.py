"""Remote table/proxy serialization against native Luau and wire decoding."""
import copy
import unittest

from remote_client_rpc import variant
import test_remote_server_rpc as rpc_tests
from world_replication import World


class RemoteTableTests(unittest.TestCase):
    setUp=rpc_tests.InvokeServerTests.setUp
    host=rpc_tests.InvokeServerTests.host
    execute=rpc_tests.InvokeServerTests.execute
    transmit=rpc_tests.InvokeServerTests.transmit
    reply=rpc_tests.InvokeServerTests.reply
    complete=rpc_tests.InvokeServerTests.complete
    def test_game_shaped_proxy_array_keeps_null_position_without_tostring(self):
        host=self.host()
        job=self.execute(host, '''
local proxy=newproxy(true)
getmetatable(proxy).__tostring=function()error("must not call tostring")end
local result=workspace.Remote:InvokeServer({proxy,"first-string","second-string"})
return result
''')
        self.assertEqual(job['state'],'running',job)
        self.assertEqual(job['remote_server_calls'][0]['arguments'],[
            {'$remote_array':[None,'first-string','second-string']}])
        self.rpc.send(self.world,job['remote_server_calls'][0],self.transmit)
        received=World(copy.deepcopy(self.world.schema));received.packet(self.sent[0][1])
        self.assertEqual(received.events[-1]['arguments'],[1,self.keys[5],[[None,b'first-string',b'second-string']]])
        self.reply([True]);results=[];self.rpc.observe(self.world,results)
        self.assertEqual(self.complete(host,job,results)['values'],[True])

    def test_nested_dictionary_array_binary_keys_and_reference_return(self):
        host=self.host()
        job=self.execute(host, '''
local t=workspace.Remote:InvokeServer({rows={{true,false},{name="a"}},empty={},[string.char(255)]="binary"})
return t.rows[1][1],t.rows[1][2],t.rows[2].name,next(t.empty)==nil,t[string.char(255)],t.ref==workspace.Remote
''')
        self.assertEqual(job['state'],'running',job)
        call=job['remote_server_calls'][0]
        self.rpc.send(self.world,call,self.transmit)
        value=copy.deepcopy(call['arguments'][0]);value['$remote_map'].append(['ref',{'$ref':self.node}])
        self.reply([value]);results=[];self.rpc.observe(self.world,results)
        done=self.complete(host,job,results)
        self.assertEqual(done['values'],[True,False,'a',True,'binary',True])

    def test_raw_array_ignores_hash_members_and_never_calls_metamethods(self):
        host=self.host()
        job=self.execute(host, '''
local x=setmetatable({11,22,hash="ignored"},{__len=function()error("len")end,__index=function()error("index")end})
return workspace.Remote:InvokeServer(x)
''')
        self.assertEqual(job['remote_server_calls'][0]['arguments'],[{'$remote_array':[11,22]}])

    def test_cyclic_oversized_and_nested_unreceived_references_are_rejected(self):
        host=self.host()
        result=self.execute(host, '''
local cycle={};cycle.self=cycle
local deep={};local t=deep;for i=1,20 do t.child={};t=t.child end
local huge=table.create(10001,1)
for _,x in {cycle,deep,huge,{part=Instance.new("Folder")},{engine=Ray.new(Vector3.new(),Vector3.new(1,0,0))}} do
 assert(not pcall(function()workspace.Remote:InvokeServer(x)end))
end
return true
''')
        self.assertEqual(result['values'],[True],result)
        self.assertEqual(result['remote_server_calls'],[])
        self.assertEqual(result['active_remote_server_requests'],[])

    def test_numeric_dictionary_keys_follow_native_conversion_and_collisions_fail(self):
        host=self.host()
        job=self.execute(host, '''
local collision={[-2]="number",["-2"]="string"}
assert(not pcall(function()workspace.Remote:InvokeServer(collision)end))
assert(not pcall(function()workspace.Remote:InvokeServer({[1e30]=true})end))
local t=workspace.Remote:InvokeServer({[-7]="integer",[2.75]="fraction"})
return t["-7"],t["2"],t[-7]==nil
''')
        self.assertEqual(job['state'],'running',job)
        call=job['remote_server_calls'][0]
        self.rpc.send(self.world,call,self.transmit)
        self.reply(call['arguments']);results=[];self.rpc.observe(self.world,results)
        self.assertEqual(self.complete(host,job,results)['values'],['integer','fraction',True])

    def test_repeated_acyclic_table_is_copied_twice(self):
        host=self.host()
        job=self.execute(host,'local t={42};return workspace.Remote:InvokeServer({t,t})')
        self.assertEqual(job['remote_server_calls'][0]['arguments'],[
            {'$remote_array':[{'$remote_array':[42]},{'$remote_array':[42]}]}])

    def test_wire_bounds_and_duplicate_keys_fail_explicitly(self):
        deep=None
        for _ in range(20):deep={'$remote_array':[deep]}
        for value in (deep,{'$remote_array':[0]*10001},
                      {'$remote_map':[['a',1],['a',2]]}, {'$remote_map':[[None,2]]}):
            with self.assertRaises(ValueError):variant(self.world,value)

    def test_received_callback_tables_are_real_luau_values_and_can_return(self):
        host=self.host()
        armed=self.execute(host, '''
workspace.Remote.OnClientInvoke=function(value)
 assert(value.children[1]==17 and value.children[2].ok==true)
 return value,newproxy(false),nil
end
''')
        value={'$remote_map':[['children',{'$remote_array':[17,{'$remote_map':[['ok',True]]}]}]]}
        seq=int(armed['world_revision'])+1
        result=host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'sequence':seq,'actions':[{
                'sequence':seq,'kind':'event','id':self.node,
                'name':'RemoteOnInvokeClient','arguments':[71,[value]]}]}})
        replies=result['remote_client_replies']
        for _ in range(10):
            if replies:break
            replies=host.call({'op':'poll','timeout_ms':20})['remote_client_replies']
        self.assertEqual(replies,[dict(node=self.node,request_id=71,ok=True,values=[value,None,None])])


if __name__=='__main__':unittest.main()
