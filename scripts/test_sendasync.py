"""Actual Luau coroutine -> chat bridge -> typed server reply, with no network."""
import copy
from pathlib import Path
import tempfile
import unittest

from chat_console import ChatConsole
from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_chat_console import FakeClient
from test_chat_protocol import fixture, received
from test_universe_chat_protocol import allocation, global_reply
from world_snapshot import make_world_snapshot


CHANNEL = 'game:GetService("TextChatService").TextChannels.RBXGeneral'


def snapshot(enabled=True):
    world, _ = fixture()
    result = make_world_snapshot(world, 42)
    result['chat_enabled'] = enabled
    return result


def reply(request, *, status=2, text='server-filtered', message_id='fixture-message'):
    return {'request_id': request['request_id'], 'message': {
        'channel_id': request['channel_id'], 'source_id': '123:6',
        'text': text, 'prefix_text': 'Fixture:', 'metadata': 'server metadata',
        'message_id': message_id, 'status_value': status}}


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust source host first')
class SendAsyncNativeTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(snapshot())
        self.addCleanup(self.host.close)

    def execute(self, source):
        return self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 100})

    def test_yields_until_reply_and_returns_readonly_textchatmessage_and_enum(self):
        first = self.execute('''
local channel = '''+CHANNEL+'''
getgenv().message = channel:SendAsync("hello 🌍", "client metadata")
local writable = pcall(function() message.Text = "fake" end)
return message.ClassName, message.Text, message.Metadata, message.MessageId,
       message.TextChannel == channel, message.TextSource.UserId,
       message.Status == Enum.TextChatMessageStatus.Success,
       message.Status.Name, message.Status.Value, typeof(message.Status),
       typeof(message), tostring(message.Status), writable
''')
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['state'], 'running')
        self.assertFalse(first['completed'])
        request = first['chat_requests'][0]
        self.assertEqual(request['text'], 'hello 🌍')
        self.assertEqual(request['metadata'], 'client metadata')
        self.assertEqual(request['channel_id'], '123:5')
        self.assertEqual(first['mutations'], [])
        response = self.host.call({'op': 'chat', 'responses': [reply(request)], 'timeout_ms': 100})
        result = next(row for row in response['completed_jobs'] if row['job_id'] == first['job_id'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['TextChatMessage', 'server-filtered', 'server metadata',
                        'fixture-message', True, 42, True, 'Success', 2, 'EnumItem', 'Instance',
                        'Enum.TextChatMessageStatus.Success', False])
        self.assertEqual(response['active_chat_requests'], [])
        duplicate = self.host.call({'op': 'chat', 'responses': [reply(request)], 'timeout_ms': 100})
        self.assertEqual(duplicate['completed_jobs'], [])

    def test_server_permission_rejection_returns_its_actual_status(self):
        first = self.execute('local m='+CHANNEL+':SendAsync("hello");return m.Text,m.Status==Enum.TextChatMessageStatus.InvalidPrivacySettings,m.Status.Value')
        response = self.host.call({'op': 'chat', 'responses': [reply(first['chat_requests'][0], status=6, text='')], 'timeout_ms': 100})
        self.assertEqual(response['completed_jobs'][0]['values'], ['', True, 6])

    def test_transport_error_is_catchable_and_vm_survives(self):
        first = self.execute('getgenv().kept=42;local ok,err=pcall(function()return '+CHANNEL+':SendAsync("hello")end);return ok,string.find(err,"timed out",1,true)~=nil')
        response = self.host.call({'op': 'chat', 'responses': [{'request_id': first['chat_requests'][0]['request_id'], 'error': 'server chat response timed out'}], 'timeout_ms': 100})
        self.assertEqual(response['completed_jobs'][0]['values'], [False, True])
        self.assertEqual(self.execute('return kept')['values'], [42])

    def test_concurrent_requests_are_correlated_when_replies_arrive_out_of_order(self):
        one = self.execute('return ('+CHANNEL+':SendAsync("one")).Text')
        two = self.execute('return ('+CHANNEL+':SendAsync("two")).Text')
        response = self.host.call({'op': 'chat', 'responses': [reply(two['chat_requests'][0], text='second'), reply(one['chat_requests'][0], text='first')], 'timeout_ms': 100})
        results = {row['job_id']: row['values'] for row in response['completed_jobs']}
        self.assertEqual(results, {one['job_id']: ['first'], two['job_id']: ['second']})

    def test_enum_members_immutable_and_have_matching_enumtype(self):
        result = self.execute('''
local e=Enum.TextChatMessageStatus
local count=#e:GetEnumItems()
local ok=pcall(function()e.Success.Value=99 end)
return count, e.Success.EnumType==e, tostring(e),typeof(e),ok,e.Floodchecked.Value
''')
        self.assertEqual(result['values'], [9, True, 'TextChatMessageStatus', 'Enum', False, 5])

    def test_message_received_signals_receive_same_result_instance(self):
        first = self.execute('''
getgenv().serviceMessage=nil;getgenv().channelMessage=nil
local service=game:GetService("TextChatService")
local channel='''+CHANNEL+'''
service.MessageReceived:Once(function(m)serviceMessage=m end)
channel.MessageReceived:Once(function(m)channelMessage=m end)
getgenv().result=channel:SendAsync("hello")
return result.Text
''')
        self.host.call({'op': 'chat', 'responses': [reply(first['chat_requests'][0])], 'timeout_ms': 100})
        result = self.execute('return result==serviceMessage,result==channelMessage')
        self.assertEqual(result['values'], [True, True])

    def test_cancellation_removes_queued_request_and_ignores_late_reply(self):
        first = self.execute('getgenv().worker=task.spawn(function()'+CHANNEL+':SendAsync("hello");error("must not resume")end);return true')
        request = first['chat_requests'][0]
        cancelled = self.execute('task.cancel(worker);return true')
        self.assertEqual(cancelled['active_chat_requests'], [])
        response = self.host.call({'op': 'chat', 'responses': [reply(request)], 'timeout_ms': 100})
        self.assertEqual(response['task_errors'], [])

    def test_argument_validation_and_local_channels_cannot_queue_messages(self):
        for invocation in ('SendAsync()', 'SendAsync(12)', 'SendAsync("x",12)',
                           'SendAsync(string.rep("x",201))', 'SendAsync("x",string.rep("m",201))',
                           'SendAsync("\\0")'):
            result = self.execute('return '+CHANNEL+':'+invocation)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['chat_requests'], [])
        result = self.execute('local c=Instance.new("TextChannel");return c:SendAsync("hello")')
        self.assertFalse(result['ok'])
        self.assertEqual(result['chat_requests'], [])

    def test_offline_host_without_bridge_fails_explicitly(self):
        with PersistentLuauHost(snapshot(False)) as host:
            result = host.call({'op': 'execute', 'source': 'return '+CHANNEL+':SendAsync("hello")', 'timeout_ms': 100})
        self.assertFalse(result['ok'])
        self.assertIn('--chat-console', result['error'])
        self.assertEqual(result['chat_requests'], [])


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust source host first')
class SendAsyncAdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rbx-sendasync-')
        self.addCleanup(self.directory.cleanup)
        self.bridge = ChatConsole(Path(self.directory.name)/'chat.sock', 42)
        self.addCleanup(self.bridge.close)
        self.client = FakeClient()
        self.world, self.keys = fixture()
        self.bridge.tick(self.client, self.world)
        self.runtime = PersistentClientScriptRuntime(lambda: self.world, 42)
        self.runtime.chat_bridge = self.bridge
        self.addCleanup(self.runtime.close)

    def test_typed_global_luau_roundtrip_uses_allocated_service_and_actual_reply(self):
        allocation(self.world, self.keys[0])
        self.bridge.tick(self.client, self.world)
        first = self.runtime.execute_user_chat('global fixture', 'RBXGlobal')
        self.assertFalse(first['completed'])
        self.runtime.poll(force=True)
        self.assertEqual(len(self.client.sent), 2)
        decoded = type(self.world)(copy.deepcopy(self.world.schema))
        decoded.packet(self.client.sent[-1][1])
        event = list(decoded.events)[0]
        self.assertEqual(event['key'], self.keys[0])
        self.assertEqual(event['arguments'][:2], [b'global fixture', b''])
        self.assertEqual(event['arguments'][3:], [42, True])
        message_id = event['arguments'][2].decode()
        self.assertFalse(self.runtime.status(first['job_id'])['completed'])
        global_reply(self.world, self.bridge.chat, message_id)
        self.bridge.tick(self.client, self.world)
        self.runtime.poll(force=True)
        result = self.runtime.status(first['job_id'])
        self.assertTrue(result['completed'], result)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['Success', 'filtered fixture'])
        self.assertEqual(len(self.client.sent), 2)

    def test_global_unavailable_does_not_start_luau_or_send(self):
        with self.assertRaisesRegex(ValueError, 'not allocated'):
            self.runtime.execute_user_chat('global fixture', 'RBXGlobal')
        self.assertIsNone(self.runtime.host)
        self.assertEqual(len(self.client.sent), 1)

    def test_real_luau_request_traverses_adapter_and_returns_server_filtered_text(self):
        first = self.runtime.execute('local c='+CHANNEL+';c.Name="LocalRename";local m=c:SendAsync("user typed text");return m.Text,m.Status==Enum.TextChatMessageStatus.Success,m.TextChannel==c')
        self.assertEqual(first['state'], 'running')
        self.assertEqual(len(self.client.sent), 1)
        self.runtime.poll(force=True)
        self.assertEqual(len(self.client.sent), 2)
        decoded = type(self.world)(copy.deepcopy(self.world.schema))
        decoded.packet(self.client.sent[-1][1])
        event = list(decoded.events)[0]
        self.assertEqual(event['arguments'][0], b'user typed text')
        message_id = event['arguments'][2].decode()
        received(self.world, self.bridge.chat, message_id, text='server filtered')
        self.bridge.tick(self.client, self.world)
        self.runtime.poll(force=True)
        result = self.runtime.status(first['job_id'])
        self.assertTrue(result['completed'], result)
        self.assertEqual(result['values'], ['server filtered', True, True])
        self.assertEqual(len(self.client.sent), 2)

    def test_permission_failure_resumes_pcall_without_sending(self):
        self.world.instances[self.keys[5]]['properties']['CanSend'] = False
        first = self.runtime.execute('local ok,err=pcall(function()return '+CHANNEL+':SendAsync("hello")end);return ok,string.find(err,"disabled",1,true)~=nil')
        self.runtime.poll(force=True)
        self.assertEqual(self.runtime.status(first['job_id'])['values'], [False, True])
        self.assertEqual(len(self.client.sent), 1)

    def test_cancelled_unsent_request_is_not_posted(self):
        self.runtime.execute('getgenv().worker=task.spawn(function()'+CHANNEL+':SendAsync("hello")end);return true')
        self.runtime.execute('task.cancel(worker);return true')
        self.runtime.poll(force=True)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.runtime.chat_jobs, {})

    def test_script_timeout_explains_missing_reply_and_input_without_retry(self):
        now = [100.]
        self.bridge.chat.clock = lambda: now[0]
        first = self.runtime.execute('return '+CHANNEL+':SendAsync("hello")')
        self.runtime._poll_chat(timeout_ms=100)
        self.assertEqual(len(self.client.sent), 2)
        now[0] += 16
        self.bridge.tick(self.client, self.world)
        self.runtime._poll_chat(timeout_ms=100)
        result = self.runtime.status(first['job_id'])
        self.assertTrue(result['completed'])
        self.assertFalse(result['ok'])
        self.assertIn('response_timeout', result['error'])
        self.assertIn('delivery is unconfirmed', result['error'])
        self.assertIn('no typed-input receipt', result['error'])
        self.assertIn(':chat <text>', result['error'])
        self.assertEqual(len(self.client.sent), 2)

    def test_typed_timeout_does_not_blame_missing_input_or_claim_rejection(self):
        now = [100.]
        self.bridge.chat.clock = lambda: now[0]
        first = self.runtime.execute_user_chat('hello')
        self.runtime._poll_chat(timeout_ms=100)
        now[0] += 16
        self.bridge.tick(self.client, self.world)
        self.runtime._poll_chat(timeout_ms=100)
        result = self.runtime.status(first['job_id'])
        self.assertFalse(result['ok'])
        self.assertIn('delivery is unconfirmed', result['error'])
        self.assertNotIn('no typed-input receipt', result['error'])
        self.assertNotIn('rejected', result['error'])
        self.assertEqual(len(self.client.sent), 2)


if __name__ == '__main__':
    unittest.main()
