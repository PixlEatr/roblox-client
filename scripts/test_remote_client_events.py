"""RemoteEvent delivery from wire parsing through real Luau signal callbacks."""
import unittest

import test_remote_server_events as events_tests
from game_wire import encode_unsigned
from remote_client_rpc import variant
from world_replication import World
from world_snapshot import make_world_delta


class RemoteClientEventTests(unittest.TestCase):
    host = events_tests.RemoteServerEventTests.host

    def world(self, class_name):
        schema={'classes':[{'id':0,'name':class_name,'properties':[],'events':[0]}],
                'events':[{'id':0,'class':0,'name':'OnClientEvent','arguments':[{'type':29}]}]}
        world=World(schema)
        world.instances[(1,77)]={'class':0,'parent':None,'properties':{}}
        return world

    def deliver(self, host, world, values, sequence):
        before=world.revision
        world.packet(bytes.fromhex('8307014d0000000000')+encode_unsigned(len(values))+
                     b''.join(variant(world,value) for value in values)+b'\0')
        delta=make_world_delta(world,before)
        delta['sequence']=sequence+1
        for action in delta['actions']:action['sequence']=sequence+1
        return host.call({'op':'update','delta':delta,'timeout_ms':100})

    def test_callbacks_and_waiters_receive_expanded_typed_tuple_with_nil(self):
        for class_name in ('RemoteEvent','UnreliableRemoteEvent'):
            with self.subTest(class_name=class_name):
                host=self.host(class_name);world=self.world(class_name)
                job=host.call({'op':'execute','timeout_ms':100,'source': '''
local function check(...)
 assert(select("#",...)==4)
 local first,empty,t,last=...
 assert(first==false and empty==nil and last==nil)
 assert(typeof(t.v)=="Vector3" and t.v.Y==2)
 assert(t.a[1]==7 and t.a[2]==nil and t.a[3]=="end")
 assert(typeof(t.bytes)=="buffer" and buffer.tostring(t.bytes)==string.char(255,0))
end
local called=false
workspace.Remote.OnClientEvent:Once(function(...)check(...);called=true end)
check(workspace.Remote.OnClientEvent:Wait())
while not called do task.wait()end
return true
'''})
                self.assertEqual(job['state'],'running',job)
                table={'$remote_map':[['v',{'$vector3':[1,2,3]}],
                    ['a',{'$remote_array':[7,None,'end']}],['bytes',{'$buffer_hex':'ff00'}]]}
                result=self.deliver(host,world,[False,None,table,None],job['world_revision'])
                completed=[]
                for _ in range(20):
                    completed.extend(result.get('completed_jobs',[]))
                    if any(item['job_id']==job['job_id'] for item in completed):break
                    result=host.call({'op':'poll','timeout_ms':20})
                done=next(item for item in completed if item['job_id']==job['job_id'])
                self.assertEqual(done['values'],[True],done)
                self.assertFalse(result.get('task_errors'),result)

    def test_zero_argument_event_does_not_add_an_empty_table(self):
        host=self.host();world=self.world('RemoteEvent')
        job=host.call({'op':'execute','timeout_ms':100,
                       'source':'return select("#",workspace.Remote.OnClientEvent:Wait())'})
        result=self.deliver(host,world,[],job['world_revision'])
        done=next(item for item in result['completed_jobs'] if item['job_id']==job['job_id'])
        self.assertEqual(done['values'],[0],done)


if __name__=='__main__':unittest.main()
