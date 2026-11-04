"""Events and pings share replication channels in larger games."""
import struct
import unittest
from unittest.mock import patch

import game_session
from world_replication import World
from wire_reader import WireError
from ping_responder import SessionPingHandler, ReplicationPingStats
from test_ping_session import PingSessionTests, FakeSession, request


SCHEMA = {'classes': [{'name': 'RemoteEvent', 'properties': []}],
          'properties': [], 'parent_property_id': 0,
          'events': [{'name': 'OnClientEvent', 'arguments': [{'type': 10}]}]}
EVENT = b'\x07\x01' + struct.pack('<IH', 42, 0) + b'\x0e'  # argument 7


class MixedReplicationTests(unittest.TestCase):
    def handler(self):
        return SessionPingHandler(stats_provider=lambda: ReplicationPingStats(17,(23,29)),
                                  status_provider=lambda: 0x12345678)

    def test_event_only_is_not_misidentified_as_ping(self):
        w = World(SCHEMA)
        self.assertEqual(w.packet(b'\x83'+EVENT+b'\x00'), [])
        self.assertEqual(w.events[-1]['arguments'], [7])

    def test_mixed_packet_preserves_events_and_returns_only_pings(self):
        w = World(SCHEMA)
        pings = w.packet(b'\x83'+EVENT+request(40)[1:-1]+EVENT+b'\x00')
        self.assertEqual(len(w.events), 2)
        self.assertEqual([p['timestamp'] for p in pings], [40])
        client=FakeSession()
        self.assertEqual(self.handler().receive_items(client,pings),1)
        self.assertEqual(len(client.sent),1)

    def test_stream_replication_focus_item(self):
        w = World(SCHEMA)
        payload = (b'\x83\x1a' + struct.pack('<I', 7) + b'\x01'
                   + struct.pack('<fff', 1., 2., 3.) + struct.pack('<f', 64.)
                   + struct.pack('<f', -1.) + b'\x00\x01' + struct.pack('<I', 30) + b'\x00')
        self.assertEqual(w.packet(payload), [])
        self.assertEqual(w.replication_foci, {7: {
            'center': [1., 2., 3.], 'radius': 64., 'optional_float': None,
            'flags': [False, True], 'timeout': 30,
        }})

    def test_stream_replication_focus_compact_center(self):
        w = World(SCHEMA)
        payload = (b'\x83\x1a' + struct.pack('<I', 8) + b'\x00\x01\x02\x03'
                   + struct.pack('<f', 32.) + struct.pack('<f', 12.5)
                   + b'\x01\x00' + struct.pack('<I', 10) + b'\x00')
        w.packet(payload)
        self.assertEqual(w.replication_foci[8]['center'], [1, 2, 3])
        self.assertEqual(w.replication_foci[8]['optional_float'], 12.5)

    def test_stream_prefetch_items(self):
        w = World(SCHEMA)
        data_info = (b'\x18' + b'\x00\x01\x02\x03'
                     + struct.pack('<ff', 64., 30.))
        request_item = (b'\x19' + struct.pack('<fffdf', 1., 2., 3., 100., 5.))
        self.assertEqual(w.packet(b'\x83' + data_info + request_item + b'\x00'), [])
        self.assertEqual(w.prefetch_data_info, [{
            'center': [1, 2, 3], 'values': [64., 30.],
        }])
        self.assertEqual(w.prefetch_requests, [{
            'center': [1., 2., 3.], 'radius': 100., 'timeout': 5.,
        }])

    def test_persistent_player_add_and_remove(self):
        w = World(SCHEMA)
        key = b'\x01' + struct.pack('<I', 42)
        w.packet(b'\x83\x1d' + key + b'\x1e' + key + b'\x00')
        self.assertEqual(w.persistent_player_changes, [
            ('add', (1, 42)), ('remove', (1, 42)),
        ])

    def test_client_qos_item_and_ignored_type(self):
        w = World(SCHEMA)
        text = b'quality'
        payload = (b'\x83\x1f\x01\x02' + struct.pack('<I', len(text)) + text
                   + b'\x1f\x03\x04\x00')
        w.packet(payload)
        self.assertEqual(w.qos_changes, [{'type': 1, 'value': 2, 'text': text}])

    def test_replicator_data_item(self):
        w = World(SCHEMA)
        w.packet(b'\x83\x20' + struct.pack('<II', 9, 3) + b'abc\x00')
        self.assertEqual(w.replicator_data, [{'id': 9, 'data': b'abc'}])

    def test_focus_frustum_and_remove_items(self):
        w = World(SCHEMA)
        w.replication_foci = {4: {}}
        values = [float(index) for index in range(15)]
        w.packet(b'\x83\x27' + struct.pack('<I', 5) + struct.pack('<15f', *values)
                 + b'\x24' + struct.pack('<I', 4)
                 + b'\x28' + struct.pack('<I', 5) + b'\x00')
        self.assertEqual(w.replication_foci, {})
        self.assertEqual(w.replication_frusta, {})

    def test_focus_frustum_values(self):
        w = World(SCHEMA)
        values = [float(index) for index in range(15)]
        w.packet(b'\x83\x27' + struct.pack('<I', 5) + struct.pack('<15f', *values) + b'\x00')
        self.assertEqual(w.replication_frusta, {5: values})

    def test_stream_pin_data_item(self):
        w = World(SCHEMA)
        key = b'\x01' + struct.pack('<I', 42)
        payload = (b'\x83\x25' + struct.pack('<I', 0)
                   + b'\x08\x01' + struct.pack('<fff', 1., 2., 3.) + b'\x01'
                   + key + struct.pack('<f', 2.5) + b'\x01\x00')
        w.packet(payload)
        self.assertEqual(w.stream_pin_data, [{
            'points': [[1., 2., 3.]], 'key': (1, 42), 'value': 2.5, 'pinned': True,
        }])

    def test_instance_intent_items(self):
        w = World(SCHEMA)
        entries = (b'\x01' + struct.pack('<I', 42) + b'\x07'
                   + b'\x00\x02')
        w.packet(b'\x83\x29' + struct.pack('<I', 2) + entries + b'\x00')
        self.assertEqual(w.instance_intents, [
            {'key': (1, 42), 'intent': 7}, {'key': None, 'intent': 2},
        ])

    def test_versioned_stats_item(self):
        w = World(SCHEMA)
        job_name, script_name = b'job', b'script'
        payload = (b'\x83\x11' + struct.pack('<I', 3) + b'\x01'
                   + struct.pack('<6I', 1, 2, 3, 4, 5, 6)
                   + b'\x00' + struct.pack('<I', len(job_name)) + job_name
                   + struct.pack('<fff', 1., 2., 3.)
                   + b'\x01\x00' + struct.pack('<I', len(script_name)) + script_name
                   + struct.pack('<fI', 4., 7) + b'\x01'
                   + struct.pack('<5f', 5., 6., 7., 8., 9.) + b'\x00')
        w.packet(payload)
        self.assertEqual(w.statistics[0]['datastore']['counters'], [1, 2, 3, 4, 5, 6])
        self.assertEqual(w.statistics[0]['jobs'], [{'name': job_name, 'values': [1., 2., 3.]}])
        self.assertEqual(w.statistics[0]['scripts'], [{'name': script_name, 'value': 4., 'count': 7}])
        self.assertEqual(w.statistics[0]['final_values'], [5., 6., 7., 8., 9.])

    def test_current_stats_compressed_sections(self):
        import zstandard
        w = World(SCHEMA)
        raw = b'\x00\x00\x00\x00'
        compressed = zstandard.ZstdCompressor().compress(raw)
        stream = struct.pack('<II', len(compressed), len(raw)) + compressed
        payload = (b'\x83\x11' + struct.pack('<I', 9) + struct.pack('<ff', 1., 2.)
                   + b'\x00' + struct.pack('<I', 0) + stream + struct.pack('<dII', 3., 0, 0)
                   + b'\x00\x01\x01' + struct.pack('<5f', 4., 5., 6., 7., 8.) + b'\x00')
        w.packet(payload)
        stats = w.statistics[0]
        self.assertEqual(stats['initial_values'], [1., 2.])
        self.assertEqual(stats['version8_text'], None)
        self.assertEqual(stats['version7_text'], b'')
        self.assertEqual(stats['memory'], {'total': 3., 'categories': [[], []]})
        self.assertEqual(stats['final_values'], [4., 5., 6., 7., 8.])

    def test_no_ping_reply_from_truncated_mixed_packet(self):
        client=FakeSession();handler=self.handler()
        with self.assertRaises(WireError):
            handler.receive_items(client,World(SCHEMA).packet(request(1)[:-1]+EVENT[:-1]))
        self.assertEqual(client.sent,[])

    def test_live_callback_dispatches_events_and_pings_on_channel_three(self):
        w=World(SCHEMA)
        packets=[(4,1,b'\x97'),(4,3,b'\x83'+EVENT+request(1)[1:])]
        with patch.object(game_session,'parse_schema',return_value=SCHEMA), \
             patch.object(game_session,'World',return_value=w):
            session=PingSessionTests.run_main(self,packets,
                ping_stats_provider=lambda:ReplicationPingStats(17,(23,29)),
                ping_status_provider=lambda:0x12345678)
        self.assertEqual(w.events[-1]['arguments'],[7])
        self.assertEqual(session.report['application_ping_replies_sent'],1)
        self.assertEqual(session.report['world_packets_decoded'],1)


if __name__=='__main__':unittest.main()
