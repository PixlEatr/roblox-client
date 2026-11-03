"""Universe chat wire tests using independent typed replication fixtures."""
import copy
import struct
import unittest

from chat_protocol import ChatProtocol, GLOBAL_SEND_EVENT, GLOBAL_RECEIVE_EVENT, SEND_EVENT_V3
from game_wire import encode_string, encode_unsigned
from login_packets import encode_signed
from replication_out import guid
from test_chat_protocol import fixture, received
from world_replication import World


def allocation(world, service, context='global'):
    event = next(e['id'] for e in world.schema['events']
                 if e['name'] == 'ServerToClientUniverseChatChannelAllocatedSignalV1')
    world.packet(b'\x83\x07' + guid(service) + struct.pack('<H', event)
                 + encode_string(context) + b'\0')


def global_reply(world, chat, message_id, *, sender=42, status=2):
    fields = [('text', 2, 'filtered fixture'), ('translation', 2, ''),
              ('prefixText', 2, 'Fixture:'), ('metadata', 2, ''),
              ('messageId', 2, message_id), ('textSource', 28, None),
              ('textChannel', 28, None), ('senderUserId', 44, sender),
              ('timestamp', 44, 123456), ('status', 7, status)]
    data = bytearray(encode_unsigned(len(fields)))
    for name, kind, value in fields:
        data += encode_string(name) + bytes([kind])
        if kind == 2:
            data += encode_string(value)
        elif kind == 28:
            data += guid(value)
        elif kind == 44:
            data += encode_signed(value)
        else:
            data += struct.pack('<H', 65535) + encode_unsigned(value)
    world.packet(b'\x83\x07' + guid(chat.service)
                 + struct.pack('<H', chat.event_ids[GLOBAL_RECEIVE_EVENT]) + data + b'\0')


class UniverseProtocolTests(unittest.TestCase):
    def setUp(self):
        self.world, self.keys = fixture()
        self.now = [100.]
        self.chat = ChatProtocol(42, clock=lambda: self.now[0])
        self.sent = []
        self.send = lambda channel, data: self.sent.append(data)

    def start(self, allocated=True):
        if allocated:
            allocation(self.world, self.keys[0])
        self.chat.ready(self.world, self.send)

    def submit(self):
        self.chat.record_typed_submission({'request_id': 1, 'channel_id': '123:1',
                                          'route': 'universe', 'text': 'fixture'})
        return self.chat.send('fixture', self.send, channel='RBXGlobal', input_request_id=1)

    def decode_last(self):
        decoded = World(copy.deepcopy(self.world.schema))
        decoded.packet(self.sent[-1])
        return list(decoded.events)[0]

    def test_pre_attach_allocation_exposes_global_and_v1_wire_matches(self):
        self.start()
        self.assertIn('RBXGlobal', [c['name'] for c in self.chat.channels()])
        result = self.submit()
        event = self.decode_last()
        self.assertEqual(event['event'], self.chat.event_ids[GLOBAL_SEND_EVENT])
        self.assertEqual(event['key'], self.keys[0])
        self.assertEqual(event['arguments'], [b'fixture', b'', result['message_id'].encode(), 42, True])
        self.assertFalse(result['server_confirmed'])

    def test_no_allocation_means_no_global_send(self):
        self.start(False)
        with self.assertRaisesRegex(ValueError, 'not allocated'):
            self.submit()
        self.assertEqual(len(self.sent), 1)
        allocation(self.world, self.keys[0], 'other')
        with self.assertRaisesRegex(ValueError, 'not allocated'):
            self.chat.require_global_route()

    def test_success_requires_matching_server_message_and_sender(self):
        self.start()
        result = self.submit()
        global_reply(self.world, self.chat, result['message_id'], sender=43)
        self.chat.poll(self.world)
        self.assertFalse(self.chat.status(result['message_id'])['server_confirmed'])
        global_reply(self.world, self.chat, result['message_id'])
        self.chat.poll(self.world)
        status = self.chat.runtime_status(result['message_id'])
        self.assertEqual(status['state'], 'server_success')
        self.assertEqual(status['response']['route'], 'universe')
        self.assertEqual(status['response']['channel'], 'RBXGlobal')
        self.assertEqual(status['response']['channel_id'], '123:1')
        self.assertIsNone(status['response']['source_id'])
        self.assertEqual(self.chat.summary()['malformed_responses'], 1)

    def test_legacy_reply_cannot_confirm_global_request(self):
        self.start()
        result = self.submit()
        received(self.world, self.chat, result['message_id'])
        self.chat.poll(self.world)
        self.assertFalse(self.chat.status(result['message_id'])['server_confirmed'])

    def test_global_reply_cannot_confirm_legacy_request(self):
        self.start()
        result = self.chat.send('fixture', self.send)
        self.assertEqual(self.decode_last()['event'], self.chat.event_ids[SEND_EVENT_V3])
        global_reply(self.world, self.chat, result['message_id'])
        self.chat.poll(self.world)
        self.assertFalse(self.chat.status(result['message_id'])['server_confirmed'])

    def test_rejection_and_timeout_remain_unconfirmed(self):
        self.start()
        result = self.submit()
        global_reply(self.world, self.chat, result['message_id'], status=7)
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['state'], 'server_rejected')
        self.now[0] += 2
        result = self.chat.send('fixture', self.send, channel='RBXGlobal')
        self.assertFalse(self.decode_last()['arguments'][-1])
        self.now[0] += 16
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['state'], 'response_timeout')

    def test_receipt_cannot_cross_routes(self):
        self.start()
        self.chat.record_typed_submission({'request_id': 1, 'channel_id': '123:1', 'text': 'fixture'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.chat.send('fixture', self.send, channel='RBXGlobal', input_request_id=1)
        self.assertEqual(len(self.sent), 1)

    def test_same_receipt_is_one_use(self):
        self.start()
        self.submit()
        self.now[0] += 2
        self.chat.send('fixture', self.send, channel='RBXGlobal', input_request_id=1)
        self.assertFalse(self.decode_last()['arguments'][-1])


if __name__ == '__main__':
    unittest.main()
