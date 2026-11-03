import tempfile
from pathlib import Path
import threading
import time
import unittest

from chat_console import ChatConsole
from chat_input import user_chat_source
from persistent_runtime import PersistentClientScriptRuntime
from script_console import ScriptConsole, send_request


class FakeClient:
    def __init__(self):
        self.sent = []

    def send(self, channel, payload):
        self.sent.append((channel, payload))


class FakeChat:
    def __init__(self):
        self.ready_sent = True
        self.service = (123, 1)
        self.global_available = True
        self.global_checks = 0
        self.receipts = []
        self.submissions = []

    def require_global_route(self):
        self.global_checks += 1
        if not self.global_available:
            raise ValueError('The server has not allocated global chat')

    def _channel(self, name):
        if name != 'RBXGeneral':
            raise ValueError('unknown channel')
        return (123, 5)

    def _source(self, target):
        return (123, 6)

    def record_typed_submission(self, request):
        saved = dict(request)
        saved['route'] = saved.get('route', 'channel')
        self.receipts.append(saved)

    def send(self, text, send, **kwargs):
        self.submissions.append((text, kwargs))
        return {'state': 'submitted', 'message_id': 'private'}


class FakeBridge:
    def __init__(self, chat):
        self.chat = chat
        self.failure = None


class ChatGlobalInputTests(unittest.TestCase):
    def test_source_generation_supports_only_two_routes_and_escapes_text(self):
        text = 'hi 🌍 "); error("injected") --\\\n'
        general = user_chat_source(text)
        self.assertIn('TextChannels.RBXGeneral', general)
        self.assertIn('channel:SendAsync(', general)
        self.assertNotIn('SendUniverseChatMessageAsync', general)
        global_source = user_chat_source(text, 'RBXGlobal')
        self.assertIn('service:SendUniverseChatMessageAsync(', global_source)
        self.assertNotIn('TextChannels.RBXGeneral', global_source)
        self.assertNotIn('error("injected")', global_source)
        for channel in ('RBXPrivate', '', None, 7):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                user_chat_source('hello', channel)

    @staticmethod
    def runtime(chat, *, channel_id, route=None, result_ok=True):
        runtime = PersistentClientScriptRuntime.__new__(PersistentClientScriptRuntime)
        runtime.chat_bridge = FakeBridge(chat)
        runtime.chat_jobs = {1: {'request': {}, 'message_id': None}}
        def execute(source):
            runtime.executed_source = source
            return {
                'ok': result_ok,
                'chat_requests': [{'request_id': 1, 'text': 'hello', 'metadata': '',
                                   'channel_id': channel_id,
                                   **({'route': route} if route is not None else {})}],
            }
        runtime.execute = execute
        return runtime

    def test_typed_global_preflights_and_binds_route_and_service_target(self):
        chat = FakeChat()
        runtime = self.runtime(chat, channel_id='123:1', route='universe')
        result = runtime.execute_user_chat('hello', 'RBXGlobal')
        self.assertTrue(result['ok'])
        self.assertEqual(chat.global_checks, 1)
        self.assertEqual(chat.receipts[0]['route'], 'universe')
        self.assertEqual(chat.receipts[0]['channel_id'], '123:1')
        self.assertIn('SendUniverseChatMessageAsync', runtime.executed_source)

    def test_global_preflight_and_route_mismatch_fail_before_receipt(self):
        chat = FakeChat()
        chat.global_available = False
        runtime = self.runtime(chat, channel_id='123:1', route='universe')
        calls = []
        runtime.execute = lambda source: calls.append(source) or {'ok': True, 'chat_requests': []}
        with self.assertRaisesRegex(ValueError, 'allocated'):
            runtime.execute_user_chat('hello', 'RBXGlobal')
        self.assertEqual(calls, [])
        self.assertEqual(chat.receipts, [])

        chat.global_available = True
        runtime = self.runtime(chat, channel_id='123:5', route='channel')
        with self.assertRaisesRegex(ValueError, 'bind'):
            runtime.execute_user_chat('hello', 'RBXGlobal')
        self.assertEqual(chat.receipts, [])
        self.assertEqual(runtime.chat_jobs[1]['local_error'],
                         'Could not bind the typed chat input')

    def test_general_route_keeps_legacy_request_shape(self):
        chat = FakeChat()
        runtime = self.runtime(chat, channel_id='123:5')
        runtime.execute_user_chat('hello')
        self.assertEqual(chat.receipts[0]['channel_id'], '123:5')
        self.assertEqual(chat.receipts[0]['route'], 'channel')
        self.assertEqual(chat.global_checks, 0)

    def test_runtime_submit_routes_global_only_to_exact_service_reference(self):
        bridge = ChatConsole.__new__(ChatConsole)
        bridge.failure = None
        bridge.client = FakeClient()
        bridge.chat = FakeChat()
        request = {'request_id': 9, 'text': 'hello', 'metadata': '',
                   'channel_id': '123:1', 'route': 'universe'}
        bridge.runtime_submit(request)
        self.assertEqual(bridge.chat.submissions[0][1]['channel'], 'RBXGlobal')
        self.assertNotIn('channel_key', bridge.chat.submissions[0][1])
        self.assertEqual(bridge.chat.global_checks, 1)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            bridge.runtime_submit({**request, 'channel_id': '123:5'})
        self.assertEqual(len(bridge.chat.submissions), 1)

    def test_script_console_accepts_optional_channel_and_preserves_default(self):
        temp = tempfile.TemporaryDirectory(prefix='chat-input-')
        self.addCleanup(temp.cleanup)
        calls = []
        console = ScriptConsole(Path(temp.name) / 'user.sock', None,
                                user_chat=lambda text, channel='RBXGeneral':
                                calls.append((text, channel)) or {'ok': True})
        self.addCleanup(console.close)

        def request(obj):
            answer = []
            thread = threading.Thread(target=lambda: answer.append(
                send_request(console.path, obj)))
            thread.start()
            deadline = time.monotonic() + 2
            while thread.is_alive() and time.monotonic() < deadline:
                console.poll()
                thread.join(.001)
            self.assertFalse(thread.is_alive())
            return answer[0]

        self.assertTrue(request({'op': 'user_chat', 'text': 'default'})['ok'])
        self.assertTrue(request({'op': 'user_chat', 'text': 'global',
                                 'channel': 'RBXGlobal'})['ok'])
        self.assertFalse(request({'op': 'user_chat', 'text': 'bad',
                                  'channel': 'RBXPrivate'})['ok'])
        self.assertEqual(calls, [('default', 'RBXGeneral'), ('global', 'RBXGlobal')])


if __name__ == '__main__':
    unittest.main()
