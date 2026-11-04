"""Offline authored-runtime tests for TextChatService universe chat."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost

BASE_SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())
SERVICE_ID = 'fixture:text-chat-service'


def snapshot(*, service_server_owned=True):
    value = copy.deepcopy(BASE_SNAPSHOT)
    value['chat_enabled'] = True
    value['class_parents']['TextChatService'] = 'Instance'
    value['nodes'].append({
        'id': SERVICE_ID,
        'class_name': 'TextChatService',
        'name': 'TextChatService',
        'parent': None,
        'server_owned': service_server_owned,
        'properties': {},
    })
    value['services']['TextChatService'] = SERVICE_ID
    return value


@unittest.skipUnless(RUST_HOST.is_file(), 'build the offline persistent Luau host first')
class UniverseChatTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(snapshot())
        self.addCleanup(self.host.close)

    def start_request(self, source=None):
        source = source or '''
local service = game:GetService("TextChatService")
service.UniverseChatMessageReceived:Connect(function(message)
    getgenv().universe_event = {
        text = message.Text,
        channel_is_nil = message.TextChannel == nil,
        source_is_nil = message.TextSource == nil,
    }
end)
task.spawn(function()
    local message = service:SendUniverseChatMessageAsync("fixture message", "fixture metadata")
    getgenv().universe_result = {
        text = message.Text,
        channel_is_nil = message.TextChannel == nil,
        source_is_nil = message.TextSource == nil,
        status = message.Status.Name,
    }
end)
return true
'''
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['chat_requests']), 1, result)
        return result['chat_requests'][0]

    @staticmethod
    def successful_message(channel_id=SERVICE_ID, *, route='universe'):
        return {
            'route': route,
            'channel_id': channel_id,
            'status_value': 2,
            'text': 'fixture server response',
            'metadata': 'fixture metadata',
            'prefix_text': '',
            'message_id': 'fixture-message-id',
        }

    def read_result(self):
        result = self.host.call({'op': 'execute', 'source': '''
local value = getgenv().universe_result
local event = getgenv().universe_event
return value.text, value.channel_is_nil, value.source_is_nil, value.status,
       event.text, event.channel_is_nil, event.source_is_nil
''', 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        return result['values']

    def test_queues_universe_route_and_receives_server_response_without_channel_refs(self):
        request = self.start_request()
        self.assertEqual(request, {
            'route': 'universe',
            'channel_id': SERVICE_ID,
            'request_id': request['request_id'],
            'text': 'fixture message',
            'metadata': 'fixture metadata',
        })
        delivered = self.host.call({'op': 'chat', 'responses': [{
            'request_id': request['request_id'],
            'message': self.successful_message(),
        }]})
        self.assertTrue(delivered['ok'], delivered)
        self.assertEqual(self.read_result(), ['fixture server response', True, True, 'Success',
                                               'fixture server response', True, True])

    def test_surface_error_resumes_yielding_luau_with_failure(self):
        request = self.start_request('''
local service = game:GetService("TextChatService")
task.spawn(function()
    local ok, err = pcall(function()
        service:SendUniverseChatMessageAsync("fixture message")
    end)
    getgenv().universe_error = {ok, err}
end)
return true
''')
        result = self.host.call({'op': 'chat', 'responses': [{
            'request_id': request['request_id'], 'error': 'fixture server failure',
        }]})
        self.assertTrue(result['ok'], result)
        observed = self.host.call({'op': 'execute', 'source': '''
local value = getgenv().universe_error
return value[1], value[2]
''', 'timeout_ms': 1000})
        self.assertTrue(observed['ok'], observed)
        self.assertFalse(observed['values'][0])
        self.assertRegex(observed['values'][1],
                         r'^(?:persistent-source:\d+: )?SendUniverseChatMessageAsync failed: fixture server failure$')

    def test_local_or_unregistered_receiver_is_rejected(self):
        self.host.close()
        self.host = PersistentLuauHost(snapshot(service_server_owned=False))
        self.addCleanup(self.host.close)
        result = self.host.call({'op': 'execute', 'source': '''
local service = game:GetService("TextChatService")
local ok, err = pcall(function() service:SendUniverseChatMessageAsync("fixture message") end)
return ok, err
''', 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['chat_requests']), 0)
        self.assertFalse(result['values'][0])
        self.assertIn('live server-replicated TextChatService', result['values'][1])

    def test_universe_route_and_service_correlation_are_enforced(self):
        request = self.start_request()
        bad_route = self.host.call({'op': 'chat', 'responses': [{
            'request_id': request['request_id'],
            'message': self.successful_message(route='channel'),
        }]})
        self.assertFalse(bad_route['ok'], bad_route)
        self.assertIn('chat response route mismatch', bad_route['error'])

        bad_channel = self.host.call({'op': 'chat', 'responses': [{
            'request_id': request['request_id'],
            'message': self.successful_message(channel_id='fixture:wrong-service'),
        }]})
        self.assertFalse(bad_channel['ok'], bad_channel)
        self.assertIn('chat response channel mismatch', bad_channel['error'])

        delivered = self.host.call({'op': 'chat', 'responses': [{
            'request_id': request['request_id'],
            'message': self.successful_message(),
        }]})
        self.assertTrue(delivered['ok'], delivered)
        self.assertEqual(self.read_result(), ['fixture server response', True, True, 'Success',
                                               'fixture server response', True, True])


if __name__ == '__main__':
    unittest.main()
