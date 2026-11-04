import collections
import copy
import json
import struct
import unittest
import uuid

from chat_protocol import (ChatProtocol, READY_EVENT, RECEIVE_EVENT, SEND_EVENT,
                           SEND_EVENT_V3, SIGNATURES, V4_FEATURE)
from game_wire import encode_string, encode_unsigned
from login_packets import encode_signed
from replication_out import guid
from world_replication import World


def fixture():
    names = ['TextChatService', 'Players', 'Player', 'Folder', 'TextChannel', 'TextSource']
    schema = {'classes': [{'id': i, 'name': name, 'events': []} for i, name in enumerate(names)],
              'events': [], 'enums': [], 'properties': []}
    for name, types in SIGNATURES.items():
        index = len(schema['events'])
        schema['classes'][0]['events'].append(index)
        schema['events'].append({'id': index, 'class': 0, 'name': name,
                                 'arguments': [{'type': t} for t in types]})
    world = World(schema)
    keys = [(123, i) for i in range(1, 7)]
    world.instances = {
        keys[0]: {'class': 0, 'properties': {}, 'parent': None},
        keys[1]: {'class': 1, 'properties': {}, 'parent': None},
        keys[2]: {'class': 2, 'properties': {'UserId': 42}, 'parent': keys[1]},
        keys[3]: {'class': 3, 'properties': {'Name': b'TextChannels'}, 'parent': keys[0]},
        keys[4]: {'class': 4, 'properties': {'Name': b'RBXGeneral'}, 'parent': keys[3]},
        keys[5]: {'class': 5, 'properties': {'UserIdReplicated': 42, 'CanSend': True}, 'parent': keys[4]},
    }
    world.client_scope = 123
    world.top_containers = {keys[0]: {}, keys[1]: {}}
    world.tags = [13]
    return world, keys


def received(world, chat, message_id, *, text='filtered text', status=2, source=None, channel=None):
    source = source or (123, 6)
    channel = channel or (123, 5)
    eid = chat.event_ids[RECEIVE_EVENT]
    # Independently feed a real typed replication item through World.packet.
    world.packet(b'\x83\x07' + guid(chat.service) + struct.pack('<H', eid)
                 + b''.join(encode_string(t) for t in (text, 'Fixture:', '', message_id))
                 + guid(source) + guid(channel) + encode_signed(123456)
                 + bytes([status]) + b'\0')


def dictionary_received(world, chat, message_id, *, text='filtered text', translation='', status=2):
    fields = [(b'text',2,text), (b'translation',2,translation),
              (b'prefixText',2,'Fixture:'), (b'metadata',2,''),
              (b'messageId',2,message_id), (b'textSource',28,(123,6)),
              (b'textChannel',28,(123,5)), (b'timestamp',44,123456), (b'status',7,status)]
    encoded = bytearray(encode_unsigned(len(fields)))
    for name, kind, value in fields:
        encoded.extend(encode_unsigned(len(name))+name+bytes([kind]))
        if kind==2: encoded.extend(encode_string(value))
        elif kind==28: encoded.extend(guid(value))
        elif kind==44: encoded.extend(encode_signed(value))
        else: encoded.extend(struct.pack('<H',65535)+encode_unsigned(value))
    eid = chat.event_ids['ServerToClientMessageReplicateSignalV2']
    world.packet(b'\x83\x07'+guid(chat.service)+struct.pack('<H',eid)+encoded+b'\0')


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.world, self.keys = fixture()
        self.now = [100.]
        self.chat = ChatProtocol(42, clock=lambda: self.now[0])
        self.sent = []
        self.send = lambda channel, payload: self.sent.append((channel, payload))

    def initialize(self):
        self.assertTrue(self.chat.ready(self.world, self.send))

    def post(self):
        self.initialize()
        return self.chat.send('hello', self.send)

    def test_waits_for_initial_world_and_own_player_then_signals_once(self):
        self.world.tags = []
        self.assertFalse(self.chat.ready(self.world, self.send))
        self.assertEqual(self.sent, [])
        self.world.tags = [13]
        self.world.instances[self.keys[2]]['properties']['UserId'] = 43
        self.assertFalse(self.chat.ready(self.world, self.send))
        self.world.instances[self.keys[2]]['properties']['UserId'] = 42
        self.initialize()
        self.initialize()
        self.assertEqual(len(self.sent), 1)
        decoded = World(copy.deepcopy(self.world.schema))
        decoded.packet(self.sent[0][1])
        self.assertEqual(list(decoded.events), [{'key': self.keys[0],
                         'event': self.chat.event_ids[READY_EVENT], 'arguments': []}])

    def test_send_native_signature_decodes_with_independent_reader(self):
        result = self.post()
        self.assertEqual(self.sent[-1][0], 1)
        decoded = World(copy.deepcopy(self.world.schema))
        decoded.packet(self.sent[-1][1])
        self.assertEqual(list(decoded.events), [{'key': self.keys[0],
                         'event': self.chat.event_ids[SEND_EVENT_V3],
                         'arguments': [b'hello', b'', result['message_id'].encode(),
                                       self.keys[5], self.keys[4], False, False]}])
        self.assertEqual(result['state'], 'submitted')
        self.assertFalse(result['server_confirmed'])
        self.assertEqual(self.chat.read()['messages'], [])
        self.assertEqual(self.chat.summary()['send_event_selection_source'], 'native_default')

    def test_message_ids_match_native_braced_uuid_and_issued_account_prefix(self):
        first = self.post()['message_id']
        self.now[0] += 2
        second = self.chat.send('next', self.send)['message_id']
        self.assertNotEqual(first, second)
        for message_id in (first, second):
            account, unique = message_id.split('-', 1)
            self.assertEqual(account, '42')
            self.assertEqual(unique, '{' + str(uuid.UUID(unique)) + '}')
            self.assertEqual(uuid.UUID(unique).version, 4)
        # The corrected ID must still correlate an actual server response.
        dictionary_received(self.world, self.chat, second)
        self.chat.poll(self.world)
        self.assertTrue(self.chat.status(second)['server_confirmed'])
        self.assertFalse(self.chat.status(first)['server_confirmed'])

    def test_no_post_before_ready_or_with_wrong_identity_or_channel(self):
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)
        self.initialize()
        self.world.instances[self.keys[5]]['properties']['UserIdReplicated'] = 43
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)
        self.world.instances[self.keys[5]]['properties']['UserIdReplicated'] = 42
        with self.assertRaises(ValueError): self.chat.send('hello', self.send, channel='missing')
        self.assertEqual(len(self.sent), 1)

    def test_channel_membership_and_explicit_permission_are_enforced(self):
        self.initialize()
        self.world.instances[self.keys[5]]['properties']['CanSend'] = False
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)
        self.world.instances[self.keys[5]]['properties']['CanSend'] = True
        self.world.instances[self.keys[4]]['parent'] = None
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)

    def test_removed_local_player_prevents_new_submissions(self):
        self.initialize()
        self.world.instances.pop(self.keys[2])
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)
        self.assertEqual(len(self.sent), 1)

    def test_text_and_metadata_validate_before_transport(self):
        self.initialize()
        for value in ('', '  ', 'x'*201, '\0', '\ud800', None, 123):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                self.chat.send(value, self.send)
        with self.assertRaises(ValueError): self.chat.send('hello', self.send, metadata='x'*201)
        self.assertEqual(len(self.sent), 1)
        self.chat.send('hi 🌍', self.send, metadata='user-entered')

    def test_ambiguous_channel_or_source_rejected(self):
        self.initialize()
        self.world.instances[(123, 7)] = copy.deepcopy(self.world.instances[self.keys[4]])
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)
        self.world.instances[(123, 7)] = copy.deepcopy(self.world.instances[self.keys[5]])
        with self.assertRaises(ValueError): self.chat.send('hello', self.send)

    def test_schema_mismatch_and_cross_session_rejected(self):
        v3 = next(i for i in self.world.schema['classes'][0]['events']
                  if self.world.schema['events'][i]['name'] == SEND_EVENT_V3)
        self.world.schema['events'][v3]['arguments'].reverse()
        with self.assertRaises(ValueError): self.initialize()
        self.world, self.keys = fixture()
        self.initialize()
        with self.assertRaises(ValueError): self.chat.attach(fixture()[0])
        for user in (True, 0, '42'):
            with self.assertRaises(ValueError): ChatProtocol(user)
        with self.assertRaises(ValueError): ChatProtocol(42, capability_bits=0)

    def test_server_success_correlates_id_source_channel_and_filtered_text(self):
        result = self.post()
        received(self.world, self.chat, 'other-message', text='another user')
        received(self.world, self.chat, result['message_id'], text='filtered by server')
        self.chat.poll(self.world)
        row = self.chat.status(result['message_id'])
        self.assertEqual(row['state'], 'server_success')
        self.assertTrue(row['server_confirmed'])
        self.assertEqual(row['response']['text'], 'filtered by server')
        self.assertEqual(len(self.chat.read()['messages']), 2)
        self.chat.poll(self.world)
        self.assertEqual(len(self.chat.read()['messages']), 2)
        self.assertEqual(len(self.chat.read(after=1)['messages']), 1)

    def test_unknown_server_status_is_a_terminal_response_not_a_waiter(self):
        result = self.post()
        received(self.world, self.chat, result['message_id'], status=1)
        self.chat.poll(self.world)
        row = self.chat.status(result['message_id'])
        self.assertEqual(row['state'], 'server_status_unknown')
        self.assertEqual(row['server_status'], 'Unknown')
        self.assertEqual(row['response']['status_value'], 1)
        self.now[0] += 16
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['state'], 'server_status_unknown')
        self.assertEqual(self.chat.summary()['response_timeouts'], 0)

    def test_server_rejection_is_not_reported_as_delivery(self):
        result = self.post()
        received(self.world, self.chat, result['message_id'], text='', status=6)
        self.chat.poll(self.world)
        row = self.chat.status(result['message_id'])
        self.assertEqual(row['state'], 'server_rejected')
        self.assertEqual(row['server_status'], 'InvalidPrivacySettings')
        self.assertFalse(row['server_confirmed'])

    def test_dictionary_reply_from_independent_wire_decoder_confirms_filtered_message(self):
        result = self.post()
        dictionary_received(self.world,self.chat,result['message_id'],text='new-format filtered')
        self.chat.poll(self.world)
        row = self.chat.status(result['message_id'])
        self.assertTrue(row['server_confirmed'])
        self.assertEqual(row['response']['text'],'new-format filtered')

    def test_dictionary_translation_is_preserved_without_replacing_filtered_text(self):
        result = self.post()
        dictionary_received(self.world, self.chat, result['message_id'],
                            text='server-filtered', translation='translated decoration')
        self.chat.poll(self.world)
        response = self.chat.status(result['message_id'])['response']
        self.assertEqual(response['text'], 'server-filtered')
        self.assertEqual(response['translation'], 'translated decoration')
        self.assertEqual(self.chat.read()['messages'][-1]['translation'], 'translated decoration')

    def test_dictionary_permission_rejection_is_reported(self):
        result = self.post()
        dictionary_received(self.world,self.chat,result['message_id'],text='',status=6)
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['server_status'],'InvalidPrivacySettings')

    def test_server_enabled_v4_selects_matching_nine_argument_descriptor(self):
        self.chat.configure_server_features({V4_FEATURE: 'true'})
        result = self.post()
        decoded = World(copy.deepcopy(self.world.schema));decoded.packet(self.sent[-1][1])
        self.assertEqual(self.chat.send_event, SEND_EVENT)
        self.assertEqual(list(decoded.events)[0]['event'], self.chat.event_ids[SEND_EVENT])
        self.assertEqual(list(decoded.events)[0]['arguments'], [
            b'hello', b'', result['message_id'].encode(), self.keys[5], self.keys[4],
            False, False, b'', b''])
        self.assertEqual(self.chat.summary()['send_event_selection_source'], 'server_feature')
        self.assertEqual(len(self.sent), 2)  # ready signal and one chat event; no retry

    def test_server_feature_false_and_absent_keep_native_v3_default(self):
        self.chat.configure_server_features({V4_FEATURE: 'false'})
        self.chat.configure_server_features({'UnrelatedFeature': 'true'})
        result = self.post()
        decoded = World(copy.deepcopy(self.world.schema));decoded.packet(self.sent[-1][1])
        self.assertEqual(self.chat.send_event, SEND_EVENT_V3)
        self.assertEqual(len(list(decoded.events)[0]['arguments']), 7)
        self.assertEqual(self.chat.summary()['send_event_selection_source'], 'server_feature')

    def test_config_after_attach_before_ready_selects_server_event(self):
        self.assertTrue(self.chat.attach(self.world))
        self.chat.configure_server_features({V4_FEATURE: '1'})
        self.assertEqual(self.chat.send_event, SEND_EVENT)
        self.post()
        self.assertEqual(self.chat.summary()['send_event_selection_source'], 'server_feature')

    def test_unrelated_later_config_preserves_explicit_v4_choice(self):
        self.chat.configure_server_features({V4_FEATURE: 'true'})
        self.assertEqual(self.chat.configure_server_features({}), SEND_EVENT)
        self.post()
        self.chat.configure_server_features({})
        self.assertEqual(self.chat.send_event, SEND_EVENT)
        self.assertEqual(self.chat.summary()['send_event_selection_source'], 'server_feature')

    def test_malformed_server_feature_is_rejected_without_sending(self):
        for config in ({V4_FEATURE: 'yes'}, {V4_FEATURE: ''},
                       {V4_FEATURE: 1}, None, []):
            with self.subTest(config_type=type(config).__name__):
                chat = ChatProtocol(42, clock=lambda: self.now[0])
                with self.assertRaises(ValueError):
                    chat.configure_server_features(config)
                self.assertEqual(chat.summary()['send_event'], None)
        self.assertEqual(self.sent, [])

    def test_selected_event_must_exist_and_match_metadata(self):
        v3 = next(i for i in self.world.schema['classes'][0]['events']
                  if self.world.schema['events'][i]['name'] == SEND_EVENT_V3)
        self.world.schema['classes'][0]['events'].remove(v3)
        with self.assertRaisesRegex(ValueError, 'Server-selected chat event is unavailable'):
            self.chat.attach(self.world)
        self.world, self.keys = fixture()
        self.chat = ChatProtocol(42, clock=lambda: self.now[0])
        self.chat.configure_server_features({V4_FEATURE: 'true'})
        v4 = next(i for i in self.world.schema['classes'][0]['events']
                  if self.world.schema['events'][i]['name'] == SEND_EVENT)
        self.world.schema['events'][v4]['arguments'][0]['type'] = 28
        with self.assertRaisesRegex(ValueError, 'Unsupported chat event signature'):
            self.chat.attach(self.world)

    def test_selected_event_choice_is_frozen_after_ready(self):
        self.initialize()
        with self.assertRaisesRegex(ValueError, 'frozen after readiness'):
            self.chat.configure_server_features({V4_FEATURE: 'true'})
        self.chat.configure_server_features({V4_FEATURE: 'false'})
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.chat.send_event, SEND_EVENT_V3)

    def test_wrong_references_do_not_confirm_a_message(self):
        result = self.post()
        received(self.world, self.chat, result['message_id'], source=(123, 99))
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['state'], 'submitted')
        self.assertEqual(self.chat.summary()['malformed_responses'], 1)

    def test_sending_unknown_status_timeout_and_late_response(self):
        result = self.post()
        received(self.world, self.chat, result['message_id'], status=3)
        self.chat.poll(self.world)
        interim = self.chat.status(result['message_id'])
        self.assertEqual(interim['state'], 'submitted')
        self.assertEqual(interim['server_status'], 'Sending')
        self.assertEqual(interim['response']['status_value'], 3)
        self.now[0] += 16
        self.chat.poll(self.world)
        timed_out = self.chat.status(result['message_id'])
        self.assertEqual(timed_out['state'], 'response_timeout')
        self.assertEqual(timed_out['server_status'], 'Sending')
        self.assertEqual(timed_out['response']['status_value'], 3)
        self.assertEqual(len(self.sent), 2)
        received(self.world, self.chat, result['message_id'])
        self.chat.poll(self.world)
        self.assertEqual(self.chat.status(result['message_id'])['state'], 'server_success')

    def test_throttles_does_not_retry_and_keeps_content_out_of_summary(self):
        result = self.post()
        with self.assertRaises(ValueError): self.chat.send('again', self.send)
        self.now[0] += 1
        def broken(*args): raise OSError('transport')
        with self.assertRaises(OSError): self.chat.send('private text', broken)
        self.chat.poll(self.world)
        self.assertEqual(len(self.sent), 2)
        self.assertNotIn('private text', json.dumps(self.chat.summary()))
        self.assertNotIn(result['message_id'], json.dumps(self.chat.summary()))

    def test_public_summary_counts_attempts_transport_and_responses_without_content(self):
        typed = {'request_id': 1, 'text': 'human text', 'channel_id': '123:5', 'metadata': ''}
        self.chat.record_typed_submission(typed)
        self.initialize()
        self.chat.send('human text', self.send, input_request_id=1)
        self.now[0] += 2
        def broken(*args): raise OSError('private transport detail')
        with self.assertRaises(OSError): self.chat.send('script text', broken)
        received(self.world, self.chat, self.chat.pending.keys().__iter__().__next__(),
                 text='server filtered text')
        self.chat.poll(self.world)
        summary = self.chat.summary()
        self.assertEqual(summary['send_attempts'], 2)
        self.assertEqual(summary['typed_send_attempts'], 1)
        self.assertEqual(summary['unverified_send_attempts'], 1)
        self.assertEqual(summary['submitted_typed'], 1)
        self.assertEqual(summary['submitted_unverified'], 0)
        self.assertEqual(summary['transport_errors'], 1)
        self.assertEqual(summary['responses_observed'], 1)
        self.assertNotIn('human text', json.dumps(summary))
        self.assertNotIn('script text', json.dumps(summary))
        self.assertNotIn('server filtered text', json.dumps(summary))
        self.assertNotIn('private transport detail', json.dumps(summary))

    def test_journal_overrun_and_unknown_reply_never_confirm(self):
        result = self.post()
        self.world.event_sequence += 1
        self.world.event_journal.append((self.world.event_sequence, {
            'key': self.keys[0], 'event': self.chat.event_ids['ServerToClientMessageReplicateSignalV2'],
            'arguments': [[]]}))
        self.chat.poll(self.world)
        self.assertFalse(self.chat.status(result['message_id'])['server_confirmed'])
        self.assertEqual(self.chat.summary()['malformed_responses'], 1)
        self.world.event_journal = collections.deque([(100, {'key': None})])
        with self.assertRaises(ValueError): self.chat.poll(self.world)


if __name__ == '__main__':
    unittest.main()
