"""Local measurement/availability tests; these never establish server acceptance."""
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from channels import reliable_header
from game_wire import frame
from native_session import NativeSession
from ping_responder import SessionPingHandler, TelemetryUnavailable
from ping_telemetry import SessionPingTelemetry
from test_ping_responder import ping


class SessionPingTelemetryTests(unittest.TestCase):
    def test_measured_pipeline_deltas_are_local_and_not_native_wire_fields(self):
        telemetry = SessionPingTelemetry(clock_ns=lambda: 21_000_000)
        telemetry.observe_dispatch(4, 3, ping(), received_ns=10_000_000, frame_ready_ns=13_000_000)
        report = telemetry.public_report()
        self.assertEqual(report['local_receive_pipeline']['last'],
                         {'receive_to_frame_ms': 3.0, 'frame_to_dispatch_ms': 8.0})
        self.assertEqual(report['local_receive_pipeline']['timed_packets'], 1)
        self.assertFalse(report['local_receive_pipeline']['native_field_equivalence_verified'])
        self.assertFalse(report['wire_telemetry_ready'])
        with self.assertRaises(TelemetryUnavailable) as raised:
            telemetry.replication_stats()
        self.assertEqual(raised.exception.code, 'native_replication_statistics_unavailable')
        self.assertEqual(telemetry.integrity_status(), 0)

    def test_new_connection_has_no_reused_observations_and_uses_native_initial_status(self):
        first = SessionPingTelemetry(clock_ns=lambda: 300)
        first.observe_dispatch(4, 3, ping(), received_ns=100, frame_ready_ns=200)
        second = SessionPingTelemetry()
        report = second.public_report()
        self.assertIsNone(report['local_receive_pipeline']['last'])
        self.assertIsNone(report['local_receive_pipeline']['max_receive_to_frame_ms'])
        self.assertEqual(report['local_receive_pipeline']['timed_packets'], 0)
        self.assertEqual(second.integrity_status(), 0)

    def test_quic_legacy_words_keep_evidenced_constructor_state(self):
        telemetry = SessionPingTelemetry(transport='quic_without_raknet', capabilities=0x100000,
                                         clock_ns=lambda: 25_000_000)
        telemetry.observe_dispatch(4, 3, ping(), received_ns=1_000_000, frame_ready_ns=9_000_000)
        stats = telemetry.replication_stats()
        self.assertEqual((stats.replication_word, stats.replication_tail_words), (0, (0, 0)))
        report = telemetry.public_report()
        self.assertEqual(report['local_receive_pipeline']['last'],
                         {'receive_to_frame_ms': 8.0, 'frame_to_dispatch_ms': 16.0})
        self.assertEqual(report['replication_statistics']['source'],
                         'native_constructor_state_without_raknet')
        self.assertEqual(report['missing_wire_fields'], [])
        self.assertTrue(report['wire_telemetry_ready'])
        self.assertEqual(telemetry.integrity_status(), 0)

    def test_transport_profile_requires_supported_explicit_capabilities(self):
        for capabilities in (None, True, -1, 0x10, 0x101000):
            with self.subTest(capabilities=capabilities), self.assertRaises(ValueError):
                SessionPingTelemetry(transport='quic_without_raknet', capabilities=capabilities)
        with self.assertRaises(ValueError):
            SessionPingTelemetry(transport='raknet', capabilities=0)
        with self.assertRaises(ValueError):
            SessionPingTelemetry(capabilities=0)
        for capabilities in (0, 0x100000):
            self.assertTrue(SessionPingTelemetry(transport='quic_without_raknet',
                                                capabilities=capabilities).public_report()
                            ['replication_statistics']['available'])

    def test_unsupported_packet_family_invalidates_only_its_session(self):
        first = SessionPingTelemetry(transport='quic_without_raknet', capabilities=0)
        second = SessionPingTelemetry(transport='quic_without_raknet', capabilities=0)
        first.observe_dispatch(4, 1, b'\xe3\0', received_ns=None, frame_ready_ns=1)
        with self.assertRaises(TelemetryUnavailable):
            first.replication_stats()
        # Receiving a supported packet later must not erase the incompatibility.
        first.observe_dispatch(4, 1, b'\x83\0', received_ns=None, frame_ready_ns=1)
        with self.assertRaises(TelemetryUnavailable):
            first.replication_stats()
        self.assertTrue(second.public_report()['replication_statistics']['available'])

    def test_available_quic_statistics_and_native_status_send_a_reply(self):
        telemetry = SessionPingTelemetry(transport='quic_without_raknet', capabilities=0)
        handler = SessionPingHandler(stats_provider=telemetry.replication_stats,
                                     status_provider=telemetry.integrity_status,
                                     telemetry_report_provider=telemetry.public_report)
        client = SimpleNamespace(report={}, sent=[])
        client.send = lambda channel, payload, application: client.sent.append(
            (False, channel, application, payload))
        client.send_unreliable = lambda channel, payload, application: client.sent.append(
            (True, channel, application, payload))
        self.assertEqual(handler.receive(client, ping()), 1)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.report['application_ping_telemetry_blocks_by_provider'],
                         {'statistics': 0, 'integrity': 0})
        self.assertTrue(client.report['application_ping_replies_implemented'])
        self.assertEqual(client.report['application_ping_replies_pending'], 0)

    def test_missing_ingress_is_not_replaced_with_dispatch_clock(self):
        telemetry = SessionPingTelemetry(clock_ns=lambda: 20)
        telemetry.observe_dispatch(4, 1, b'\x83\0', received_ns=None, frame_ready_ns=10)
        report = telemetry.public_report()['local_receive_pipeline']
        self.assertEqual(report['replication_packets'], 1)
        self.assertEqual(report['timed_packets'], 0)
        self.assertIsNone(report['last'])

    def test_invalid_order_and_other_applications_do_not_produce_samples(self):
        telemetry = SessionPingTelemetry(clock_ns=lambda: 20)
        telemetry.observe_dispatch(6, 3, ping(), received_ns=1, frame_ready_ns=2)
        self.assertEqual(telemetry.public_report()['local_receive_pipeline']['replication_packets'], 0)
        with self.assertRaises(ValueError):
            telemetry.observe_dispatch(4, 3, ping(), received_ns=21, frame_ready_ns=22)
        self.assertEqual(telemetry.public_report()['local_receive_pipeline']['timed_packets'], 0)

    def test_native_stream_dispatch_observes_current_connection_before_callback(self):
        session = NativeSession.__new__(NativeSession)
        session.connect_stream = 0
        session.incoming = {}
        session.duration = 1
        session.capture = []
        session.capture_bytes = 0
        session.started = time.monotonic()
        session.report = {'channels_received': [], 'messages': []}
        session._received_datagram_at_ns = 10_000_000
        dispatch_times = iter((19_000_000, 50_000_000))
        session.ping_telemetry = SessionPingTelemetry(clock_ns=lambda: next(dispatch_times))
        events = []

        def receive(client, app, channel, payload):
            events.append((app, channel, payload, client.ping_telemetry.public_report()))

        payload = ping()
        event = SimpleNamespace(session_id=0, stream_id=7, stream_ended=False,
                                data=reliable_header(4, 3) + frame(payload) + frame(payload))
        with patch('native_session.time.monotonic_ns', return_value=12_000_000):
            session.receive_stream(event, receive)
        self.assertEqual(events[0][:3], (4, 3, payload))
        self.assertEqual(events[0][3]['local_receive_pipeline']['last'],
                         {'receive_to_frame_ms': 2.0, 'frame_to_dispatch_ms': 7.0})
        # Both complete frames were ready before either callback. The second
        # observation must include time spent in the earlier callback.
        self.assertEqual(events[1][3]['local_receive_pipeline']['last'],
                         {'receive_to_frame_ms': 2.0, 'frame_to_dispatch_ms': 38.0})

    def test_registered_but_unavailable_providers_do_not_claim_implemented_or_send(self):
        telemetry = SessionPingTelemetry()
        handler = SessionPingHandler(stats_provider=telemetry.replication_stats,
                                     status_provider=telemetry.integrity_status,
                                     telemetry_report_provider=telemetry.public_report)
        client = SimpleNamespace(report={})  # Any attempted send would fail this test.
        self.assertEqual(handler.receive(client, ping()), 0)
        self.assertTrue(client.report['application_ping_provider_callbacks_configured'])
        self.assertFalse(client.report['application_ping_replies_implemented'])
        self.assertEqual(client.report['application_ping_replies_sent'], 0)
        self.assertEqual(client.report['application_ping_telemetry_blocks_by_provider'],
                         {'statistics': 1, 'integrity': 0})
        self.assertEqual(client.report['application_ping_last_telemetry_block'],
                         {'provider': 'statistics', 'reason': 'native_replication_statistics_unavailable'})

    def test_exception_text_cannot_escape_through_public_block_diagnostics(self):
        def unavailable():
            raise TelemetryUnavailable('private provider contents', code='private code contents')

        client = SimpleNamespace(report={})
        handler = SessionPingHandler(stats_provider=unavailable, status_provider=unavailable)
        handler.receive(client, ping())
        self.assertNotIn('private', str(client.report))
        self.assertEqual(client.report['application_ping_last_telemetry_block']['reason'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
