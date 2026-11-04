"""InvokeServer VM/transport integration using actual compiled Luau coroutines."""
import collections
import copy
import os
import struct
import types
import time
import unittest
from unittest import mock

from game_wire import encode_unsigned
from login_packets import encode_signed
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from remote_client_rpc import variant
from remote_server_rpc import RemoteServerRpc
from replication_out import guid
from test_cframe_transforms import SNAPSHOT
from test_replication_rpc import fixture
from world_replication import World


class InvokeServerTests(unittest.TestCase):
    def setUp(self):
        self.world, _, self.keys = fixture()
        self.target = self.keys[3]
        self.node = '%s:%s' % self.target
        self.rpc = RemoteServerRpc(123)
        self.sent = []

    def host(self):
        data = copy.deepcopy(SNAPSHOT)
        data['nodes'].append(dict(id=self.node, class_name='RemoteFunction', name='Remote',
                                  parent='scope:workspace', properties={}))
        host = PersistentLuauHost(data)
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        return host.call(dict(op='execute', source=source, timeout_ms=100))

    def transmit(self, channel, payload):
        self.sent.append((channel, payload))

    def call(self, **changes):
        return dict(node=self.node, request_id=1, arguments=[], **changes)

    def reply(self, values, request=1, target=None, error=None):
        target = target or self.target
        if error is None:
            body = encode_unsigned(len(values)) + b''.join(variant(self.world, v) for v in values)
            event = 3
        else:
            data = error.encode(); body = encode_unsigned(len(data)) + data; event = 1
        self.world.packet(b'\x83\x07'+guid(target)+struct.pack('<H',event)+encode_signed(request)+body+b'\0')

    def complete(self, host, job, responses):
        result = host.call(dict(op='remote_server_results', responses=responses, timeout_ms=100))
        for _ in range(10):
            for completed in result.get('completed_jobs', []):
                if completed['job_id'] == job['job_id']:
                    return completed
            result = host.call(dict(op='poll', timeout_ms=20))
        self.fail('Invocation did not finish after correlated response')

    def test_actual_vm_yields_and_round_trips_primitive_tuple_with_nil(self):
        host = self.host()
        job = self.execute(host, 'local a=table.pack(workspace.Remote:InvokeServer("hi",nil,false,42,workspace.Remote)); return a.n,a[1],a[2],a[3],a[4]==workspace.Remote,a[5]')
        self.assertEqual(job['state'], 'running', job)
        call = job['remote_server_calls'][0]
        self.rpc.send(self.world, call, self.transmit)
        received = World(copy.deepcopy(self.world.schema)); received.packet(self.sent[0][1])
        self.assertEqual(self.sent[0][0], 1)
        self.assertEqual(received.events[-1]['arguments'], [1, self.keys[5], [b'hi',None,False,42.,self.target]])
        self.reply([True,None,'ok',{'$ref':self.node},None])
        results=[]; self.rpc.observe(self.world,results)
        result=self.complete(host,job,results)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['values'],[5,True,None,'ok',True,None])
        self.assertEqual(self.rpc.summary()['success_replies_received'],1)

    def test_server_error_is_raised_in_luau_pcall(self):
        host=self.host();job=self.execute(host,'return pcall(function()return workspace.Remote:InvokeServer()end)')
        self.rpc.send(self.world,job['remote_server_calls'][0],self.transmit)
        self.reply([],error='actual fixture server error')
        results=[];self.rpc.observe(self.world,results)
        result=self.complete(host,job,results)
        self.assertEqual(result['values'],[False,'actual fixture server error'])

    def test_wrong_request_receiver_and_stale_reply_do_not_complete(self):
        self.reply(['stale'])
        self.rpc.send(self.world,self.call(),self.transmit)
        self.reply(['wrong request'],request=2)
        self.reply(['wrong receiver'],target=self.keys[0])
        results=[];self.rpc.observe(self.world,results)
        self.assertFalse(results);self.assertEqual(len(self.rpc.pending),1)
        self.reply(['right']);self.rpc.observe(self.world,results)
        self.assertEqual(results[0]['values'],['right'])
        self.rpc.observe(self.world,results);self.assertEqual(len(results),1)

    def test_concurrent_calls_correlate_out_of_order_and_preserve_zero_results(self):
        host=self.host()
        a=self.execute(host,'return workspace.Remote:InvokeServer("a")')
        b=self.execute(host,'return select("#",workspace.Remote:InvokeServer("b"))')
        for job in (a,b):self.rpc.send(self.world,job['remote_server_calls'][0],self.transmit)
        self.reply([],request=2);results=[];self.rpc.observe(self.world,results)
        self.assertEqual(self.complete(host,b,results)['values'],[0])
        self.reply([23],request=1);results=[];self.rpc.observe(self.world,results)
        self.assertEqual(self.complete(host,a,results)['values'],[23])

    def test_native_rejects_local_instances_and_unsupported_values_without_queueing(self):
        host=self.host()
        result=self.execute(host, '''
for _,v in {function()end,Ray.new(Vector3.new(),Vector3.new(1,0,0)),Instance.new("Folder"),0/0} do
 assert(not pcall(function()workspace.Remote:InvokeServer(v)end))
end
assert(not pcall(function()Instance.new("RemoteFunction"):InvokeServer()end))
return true
''')
        self.assertEqual(result['values'],[True],result)
        self.assertFalse(result['remote_server_calls'])
        self.assertFalse(result['active_remote_server_requests'])

    def test_cancelled_thread_and_destroyed_receiver_release_waiters(self):
        host=self.host()
        result=self.execute(host,'local t=task.spawn(function()workspace.Remote:InvokeServer()end);task.cancel(t);task.wait();return true')
        for _ in range(3):result=host.call(dict(op='poll',timeout_ms=20))
        self.assertFalse(result['active_remote_server_requests'])
        job=self.execute(host,'return pcall(function()workspace.Remote:InvokeServer()end)')
        destroyed=self.execute(host,'workspace.Remote:Destroy()')
        done=next((r for r in destroyed['completed_jobs'] if r['job_id']==job['job_id']),None)
        if done is None:done=self.complete(host,job,[])
        self.assertFalse(done['values'][0]);self.assertIn('destroyed',done['values'][1])

    def test_unsupported_response_variant_is_not_silently_changed(self):
        host=self.host();job=self.execute(host,'return pcall(function()return workspace.Remote:InvokeServer()end)')
        self.rpc.send(self.world,job['remote_server_calls'][0],self.transmit)
        body=b'\x01\x24'+struct.pack('<fff',1,2,3)
        self.world.packet(b'\x83\x07'+guid(self.target)+struct.pack('<H',3)+encode_signed(1)+body+b'\0')
        results=[];self.rpc.observe(self.world,results)
        done=self.complete(host,job,results)
        self.assertFalse(done['values'][0]);self.assertIn('datatype',done['values'][1])

    def test_current_session_player_and_schema_are_required(self):
        for change in ('identity','schema','removed','duplicate_player'):
            with self.subTest(change=change):
                world,_,keys=fixture();rpc=RemoteServerRpc(123)
                if change=='identity':world.instances[keys[5]]['properties']['UserId']=999
                if change=='schema':world.schema['events'][2]['arguments'][1]['type']=2
                if change=='removed':del world.instances[self.target]
                if change=='duplicate_player':world.instances[(9,9)]=copy.deepcopy(world.instances[keys[5]])
                with self.assertRaises(ValueError):rpc.send(world,self.call(),self.transmit)
        self.assertFalse(self.sent)

    def test_session_identity_receiver_identity_and_journal_bounds(self):
        self.rpc.send(self.world,self.call(),self.transmit)
        with self.assertRaises(ValueError):self.rpc.observe(fixture()[0],[])
        self.world.instances[self.target]=copy.deepcopy(self.world.instances[self.target])
        results=[];self.rpc.observe(self.world,results)
        self.assertFalse(results[0]['ok']);self.assertIn('removed',results[0]['error'])
        self.world.event_sequence+=5000
        self.world.event_journal.append((self.world.event_sequence,{'arguments':[],'event':0,'key':self.target}))
        with self.assertRaisesRegex(ValueError,'overrun'):self.rpc.observe(self.world,[])

    def test_transport_failure_is_not_retried_or_counted_sent(self):
        def fail(*args):return False
        with self.assertRaises(OSError):self.rpc.send(self.world,self.call(),fail)
        self.assertEqual(self.rpc.sent,0)
        with self.assertRaises(ValueError):self.rpc.send(self.world,self.call(),self.transmit)
        self.assertFalse(self.sent)

    def test_local_submission_error_reaches_waiting_script(self):
        host=self.host();job=self.execute(host,'return pcall(function()workspace.Remote:InvokeServer()end)')
        runtime=types.SimpleNamespace(remote_server_calls=collections.deque(job['remote_server_calls']),
            remote_server_results=collections.deque(),active_remote_server_requests=set(job['active_remote_server_requests']))
        self.world.schema['events'][2]['arguments'][1]['type']=2
        self.rpc.flush(self.world,runtime,self.transmit)
        done=self.complete(host,job,list(runtime.remote_server_results))
        self.assertFalse(done['values'][0]);self.assertIn('signature',done['values'][1])
        self.assertFalse(self.sent)

    def test_foreground_and_background_adapter_deliver_real_coroutine_results(self):
        for background in (False, True):
            with self.subTest(background=background):
                host=self.host()
                runtime=PersistentClientScriptRuntime(lambda:None,123)
                runtime.host=host;self.addCleanup(runtime.close)
                job=runtime._record(self.execute(host,'return workspace.Remote:InvokeServer()'))
                request=runtime.remote_server_calls.popleft()
                runtime.remote_server_results.append(dict(node=self.node,
                    request_id=request['request_id'],ok=True,values=[123,None]))
                if background:
                    runtime.poll_background()
                    deadline=time.monotonic()+2
                    while runtime._background_context is not None and time.monotonic()<deadline:
                        runtime.poll_background();time.sleep(.001)
                else:runtime.poll(force=True)
                self.assertEqual(runtime.results[job['job_id']]['values'],[123,None])
                self.assertFalse(runtime.remote_server_results)
                self.assertFalse(runtime.active_remote_server_requests)

    def test_native_ignores_mismatched_local_response_tokens(self):
        host=self.host();job=self.execute(host,'return workspace.Remote:InvokeServer()')
        request=job['remote_server_calls'][0]['request_id']
        invalid=[dict(node=self.node,request_id=request+1,ok=True,values=[1]),
                 dict(node='1:99999',request_id=request,ok=True,values=[2])]
        result=host.call(dict(op='remote_server_results',responses=invalid,timeout_ms=100))
        self.assertIn(request,result['active_remote_server_requests'])
        self.assertFalse(any(r['job_id']==job['job_id'] for r in result['completed_jobs']))
        good=dict(node=self.node,request_id=request,ok=True,values=[3])
        self.assertEqual(self.complete(host,job,[good])['values'],[3])

    def test_reply_resumes_spawned_caller_before_busy_background_batch(self):
        with mock.patch.dict(os.environ, {'RCR_LUAU_LARGE_TEST': '1'}):
            host = self.host()
        job = self.execute(host, '''task.spawn(function()
local value=workspace.Remote:InvokeServer("report")
assert(value=="actual response")
print("report continuation completed")
end)''')
        request = job['remote_server_calls'][0]
        self.execute(host, '''for i=1,128 do
task.defer(function()while true do end end)
end''')
        response = host.call({'op': 'remote_server_results', 'timeout_ms': 20,
            'responses': [dict(node=self.node, request_id=request['request_id'],
                               ok=True, values=['actual response'])]})
        self.assertTrue(response['ok'], response)
        self.assertIn('report continuation completed', response['output'])
        self.assertGreater(response['pending_tasks'], 100)
        self.assertFalse(response['active_remote_server_requests'])

    def test_response_batch_preserves_fifo_and_gives_ordinary_tasks_turns(self):
        with mock.patch.dict(os.environ, {'RCR_LUAU_LARGE_TEST': '1'}):
            host = self.host()
        job = self.execute(host, '''for i=1,3 do task.spawn(function()
workspace.Remote:InvokeServer(i)
print("reply-"..i)
end) end''')
        self.assertEqual(len(job['remote_server_calls']), 3)
        self.execute(host, '''for i=1,8 do task.defer(function()
while true do print("ordinary turn");task.wait() end
end) end''')
        time.sleep(.005)
        response = host.call({'op': 'remote_server_results', 'timeout_ms': 100,
            'responses': [dict(node=self.node, request_id=r['request_id'],
                               ok=True, values=[])
                          for r in job['remote_server_calls']]})
        self.assertTrue(response['ok'], response)
        lines = response['output'].splitlines()
        positions = [lines.index('reply-'+str(i)) for i in range(1, 4)]
        self.assertEqual(positions, sorted(positions))
        for first, second in zip(positions, positions[1:]):
            self.assertIn('ordinary turn', lines[first+1:second])


if __name__=='__main__':unittest.main()
