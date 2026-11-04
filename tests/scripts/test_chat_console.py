import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chat_console import ChatConsole
from script_console import send_request
from test_chat_protocol import fixture, received


class FakeClient:
    def __init__(self):
        self.sent = []
        self.report = {}

    def send(self, channel, data):
        self.sent.append((channel, data))


class ChatConsoleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rbx-chat-')
        self.path = Path(self.directory.name)/'chat.sock'
        self.console = ChatConsole(self.path, 42)
        self.client = FakeClient()
        self.world, self.keys = fixture()
        self.console.tick(self.client, self.world)

    def tearDown(self):
        self.console.close()
        self.directory.cleanup()

    def request(self, request):
        response = []
        def send():
            try: response.append(send_request(self.path, request))
            except Exception as error: response.append(error)
        worker = threading.Thread(target=send)
        worker.start()
        deadline = time.monotonic()+3
        while worker.is_alive() and time.monotonic() < deadline:
            self.console.tick(self.client, self.world)
            worker.join(.001)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(response), 1)
        if isinstance(response[0], Exception): raise response[0]
        return response[0]

    def test_socket_send_channels_read_and_status(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        listing = self.request({'op': 'channels'})
        self.assertTrue(listing['initialized'])
        self.assertEqual(listing['channels'], [{'name': 'RBXGeneral', 'member': True,
                                              'can_send_received': True}])
        sent = self.request({'op': 'send', 'text': 'hello'})
        self.assertTrue(sent['ok'])
        self.assertEqual(sent['state'], 'submitted')
        self.assertFalse(sent['server_confirmed'])
        received(self.world, self.console.chat, sent['message_id'])
        status = self.request({'op': 'status', 'message_id': sent['message_id']})
        self.assertEqual(status['state'], 'server_success')
        receipt = self.request({'op': 'read', 'after': 0})
        self.assertEqual(receipt['messages'][0]['text'], 'filtered text')
        self.assertEqual(len(self.client.sent), 2)
        self.assertEqual(self.request({'op': 'read', 'after': receipt['cursor']})['messages'], [])

    def test_rejects_malformed_and_script_requests_without_sending(self):
        for request in ({'source': 'print(1)'}, {'op': 'send', 'text': 'hello', 'source': 'x'},
                        {'op': 'send'}, {'op': 'read', 'after': -1},
                        {'op': 'status', 'message_id': 'unknown'}):
            self.assertFalse(self.request(request)['ok'])
        self.assertEqual(len(self.client.sent), 1)

    def test_disabled_sending_preserves_membership_and_is_not_overridden(self):
        self.world.instances[self.keys[5]]['properties']['CanSend'] = False
        listing = self.request({'op': 'channels'})
        self.assertTrue(listing['channels'][0]['member'])
        self.assertFalse(listing['channels'][0]['can_send_received'])
        response = self.request({'op': 'send', 'text': 'hello'})
        self.assertFalse(response['ok'])
        self.assertEqual(len(self.client.sent), 1)

    def test_session_protocol_failure_stops_chat_and_summary_has_no_content(self):
        sent = self.request({'op': 'send', 'text': 'private content'})
        received(self.world, self.console.chat, sent['message_id'], text='filtered private content')
        self.console.tick(self.client, self.world)
        self.assertNotIn('private content', json.dumps(self.client.report))
        self.assertNotIn(sent['message_id'], json.dumps(self.client.report))
        other, _ = fixture()
        self.console.tick(self.client, other)
        self.assertFalse(self.client.report['chat']['healthy'])
        with self.assertRaises(RuntimeError): self.console.dispatch({'op': 'send', 'text': 'hello'})

    def test_close_cleans_up_socket_and_active_console_is_not_replaced(self):
        with self.assertRaises(ValueError): ChatConsole(self.path, 42)
        self.console.close()
        self.assertFalse(self.path.exists())
        # The regular tearDown closes a replacement listener, not an old inode.
        self.console = ChatConsole(self.path, 42)


if __name__ == '__main__':
    unittest.main()
