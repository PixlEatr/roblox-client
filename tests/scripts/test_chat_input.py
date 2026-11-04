"""Typed-input provenance and real native SendAsync with an offline peer."""
import copy
from pathlib import Path
import tempfile
import unittest

from chat_console import ChatConsole
from chat_protocol import ChatProtocol, V4_FEATURE
from local_runtime import RUST_HOST
from persistent_runtime import PersistentClientScriptRuntime
from test_chat_console import FakeClient
from test_chat_protocol import fixture, received
from world_replication import World


class TypedReceiptTests(unittest.TestCase):
    def setUp(self):
        self.world, self.keys = fixture()
        self.now = [100.]
        self.chat = ChatProtocol(42, clock=lambda: self.now[0])
        self.client = FakeClient()
        self.chat.ready(self.world, self.client.send)
        self.request = {'request_id': 7, 'text': 'chat test',
                        'channel_id': '123:5', 'metadata': ''}

    def last_arguments(self):
        decoded = World(copy.deepcopy(self.world.schema))
        decoded.packet(self.client.sent[-1][1])
        return list(decoded.events)[0]['arguments']

    def test_input_receipt_is_one_use_and_never_confirms_delivery(self):
        self.chat.record_typed_submission(self.request)
        result = self.chat.send('chat test', self.client.send, input_request_id=7)
        self.assertEqual(self.last_arguments()[5:], [True, False])
        self.assertEqual(result['input_verification'], 'typed_submission')
        self.assertFalse(result['server_confirmed'])
        self.assertEqual(result['state'], 'submitted')
        self.now[0] += 2
        self.chat.send('chat test', self.client.send, input_request_id=7)
        self.assertFalse(self.last_arguments()[5])

    def test_server_enabled_v4_preserves_one_use_input_verification(self):
        self.chat = ChatProtocol(42, clock=lambda: self.now[0])
        self.chat.configure_server_features({V4_FEATURE: 'true'})
        self.chat.ready(self.world, self.client.send)
        self.chat.record_typed_submission(self.request)
        result = self.chat.send('chat test', self.client.send, input_request_id=7)
        self.assertEqual(self.last_arguments()[5:], [True, False, b'', b''])
        self.assertFalse(result['server_confirmed'])
        self.now[0] += 2
        self.chat.send('chat test', self.client.send, input_request_id=7)
        self.assertFalse(self.last_arguments()[5])

    def test_ordinary_script_message_cannot_consume_a_receipt_for_another_request(self):
        self.chat.record_typed_submission(self.request)
        self.chat.send('chat test', self.client.send, input_request_id=8)
        self.assertFalse(self.last_arguments()[5])
        self.assertIn(7, self.chat._typed_submissions)

    def test_expired_or_modified_input_fails_before_transport(self):
        for field, value in (('text', 'different'), ('metadata', 'changed'),
                             ('channel_id', '123:99'), ('expires', 99.)):
            with self.subTest(field=field):
                self.chat.record_typed_submission(self.request)
                self.chat._typed_submissions[7][field] = value
                with self.assertRaisesRegex(ValueError, 'expired or changed'):
                    self.chat.send('chat test', self.client.send, input_request_id=7)
                self.assertEqual(len(self.client.sent), 1)

    def test_a_new_session_does_not_inherit_input_receipts(self):
        self.chat.record_typed_submission(self.request)
        other = ChatProtocol(42, clock=lambda: self.now[0])
        other.ready(self.world, self.client.send)
        other.send('chat test', self.client.send, input_request_id=7)
        self.assertFalse(self.last_arguments()[5])


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust source host first')
class TypedChatNativeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir='/tmp', prefix='typed-chat-')
        self.addCleanup(self.directory.cleanup)
        self.world, self.keys = fixture()
        self.bridge = ChatConsole(Path(self.directory.name) / 'chat.sock', 42)
        self.addCleanup(self.bridge.close)
        self.client = FakeClient()
        self.bridge.tick(self.client, self.world)
        self.runtime = PersistentClientScriptRuntime(lambda: self.world, 42)
        self.runtime.chat_bridge = self.bridge
        self.addCleanup(self.runtime.close)

    def decode_last(self):
        decoded = World(copy.deepcopy(self.world.schema))
        decoded.packet(self.client.sent[-1][1])
        return list(decoded.events)[0]['arguments']

    def test_typed_input_runs_native_luau_and_returns_the_real_peer_result(self):
        first = self.runtime.execute_user_chat('chat test')
        self.assertEqual(first['state'], 'running')
        self.assertEqual(len(self.client.sent), 1)  # Ready signal only.
        self.runtime._poll_chat(timeout_ms=100)
        args = self.decode_last()
        self.assertEqual(args[0], b'chat test')
        self.assertEqual(args[5:], [True, False])
        received(self.world, self.bridge.chat, args[2].decode(), text='peer-filtered')
        self.bridge.chat.poll(self.world)
        final = self.runtime._poll_chat(timeout_ms=100)
        job = next(row for row in final['completed_jobs'] if row['job_id'] == first['job_id'])
        self.assertEqual(job['values'], ['Success', 'peer-filtered'])
        self.assertTrue(job['ok'])
        self.assertEqual(self.bridge.chat._typed_submissions, {})

    def test_source_editor_literal_is_still_unverified(self):
        self.runtime.execute('return game:GetService("TextChatService").TextChannels.RBXGeneral:SendAsync("chat test")')
        self.runtime._poll_chat(timeout_ms=100)
        self.assertFalse(self.decode_last()[5])

    def test_text_cannot_escape_into_luau_source(self):
        text = 'hi 🌍 "); error("injected") --\\\n'
        first = self.runtime.execute_user_chat(text)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['chat_requests'][0]['text'], text)
        self.runtime._poll_chat(timeout_ms=100)
        self.assertEqual(self.decode_last()[0], text.encode('utf-8'))

    def test_server_permissions_are_enforced_before_executing_or_receipting(self):
        self.world.instances[self.keys[5]]['properties']['CanSend'] = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.runtime.execute_user_chat('chat test')
        self.assertEqual(self.runtime.executions, 0)
        self.assertEqual(self.bridge.chat._typed_submissions, {})
        self.assertEqual(len(self.client.sent), 1)

    def test_expired_input_resumes_the_luau_call_with_an_error_without_posting(self):
        now = [100.]
        self.bridge.chat.clock = lambda: now[0]
        first = self.runtime.execute_user_chat('chat test')
        now[0] += 6
        final = self.runtime._poll_chat(timeout_ms=100)
        self.assertEqual(len(self.client.sent), 1)
        job = next(row for row in final['completed_jobs'] if row['job_id'] == first['job_id'])
        self.assertFalse(job['ok'])
        self.assertIn('expired or changed', job['error'])


if __name__ == '__main__':
    unittest.main()
