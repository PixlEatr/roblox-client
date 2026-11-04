"""Exercise the game-session callback path without credentials or sockets."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import game_session
from ping_back import PingBackTelemetry, encode_ping_back
from ping_responder import ReplicationPingStats, SessionPingHandler, TelemetryUnavailable
from ping_telemetry import SessionPingTelemetry
from replication_ping import ping_packet


def request(index, unreliable=True):
    return (b'\x83\x05\x00' + struct.pack('<QIII', index, 123, 456, 789)
            + bytes((unreliable, 0)))


class FakeSession:
    def __init__(self, duration=12, capabilities=0):
        self.report = {'server_feature_configuration_received': True}
        self.started = time.monotonic()
        self.sent = []
        self.fail = False

    def send(self, channel, payload, application=4):
        if self.fail:
            raise OSError('synthetic send failure')
        self.sent.append((False, application, channel, payload))

    def send_unreliable(self, channel, payload, application=4):
        if self.fail:
            raise OSError('synthetic send failure')
        self.sent.append((True, application, channel, payload))


class PingSessionTests(unittest.TestCase):
    def run_main(self, packets, *, with_session_telemetry=False, session_action=None, **providers):
        session = FakeSession()
        if with_session_telemetry:
            session.ping_telemetry = SessionPingTelemetry(transport='quic_without_raknet', capabilities=0)

        def run(ready, receive, **kwargs):
            ready(session)
            if session_action is not None:
                session_action(session)
            for application, channel, payload in packets:
                receive(session, application, channel, payload)
            return session.report

        session.run = run
        args = game_session.parse_args([])
        # A synthetic allocation is enough for main's glue. No real ticket is
        # read, allocation requested, native worker launched, or socket opened.
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'secrets').mkdir()
            (root / 'secrets/last-join.json').write_text(json.dumps({
                'body': {'joinScript': {'ClientTicket': 'synthetic',
                                        'UserId': 1, 'GameId': 'synthetic'}}}))
            with patch.object(game_session, 'ROOT', root), \
                    patch.object(game_session, 'parse_args', return_value=args), \
                    patch.object(game_session, 'NativeSession', return_value=session), \
                    patch.object(game_session, 'issued_early_auth', return_value=b'early'), \
                    patch.object(game_session, 'ClientScriptRuntime'), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(game_session.main(**providers), 0)
        return session

    def test_actual_game_callback_sends_176_replies_on_requested_transport(self):
        packets = [(4, 3, request(index, bool(index % 2))) for index in range(176)]
        session = self.run_main(
            packets, ping_stats_provider=lambda: ReplicationPingStats(17, (23, 29)),
            ping_status_provider=lambda: 0x12345678)
        replies = [message for message in session.sent if message[2] == 3]
        self.assertEqual(len(replies), 176)
        for index, (unreliable, application, channel, payload) in enumerate(replies):
            self.assertEqual((unreliable, application, channel), (bool(index % 2), 4, 3))
            decoded = ping_packet(payload)[0]
            self.assertEqual((decoded['kind'], decoded['flags'], decoded['timestamp']),
                             (6, 1, index))
            self.assertEqual(decoded['first_word'], 123)
            self.assertEqual(decoded['reply_words'][3:], [17, 23, 29])
            self.assertEqual(decoded['trailer_words'],
                             [0x12345678, 0xffffffff if index & 0x20 else 0])
        self.assertEqual(session.report['application_ping_replies_sent'], 176)
        self.assertEqual(session.report['application_ping_replies_pending'], 0)
        self.assertTrue(session.report['application_ping_replies_implemented'])

    def test_missing_live_providers_are_reported_without_fabricated_replies(self):
        session = self.run_main([(4, 3, request(1))])
        self.assertEqual([message for message in session.sent if message[2] == 3], [])
        self.assertFalse(session.report['application_ping_replies_implemented'])
        self.assertTrue(session.report['application_ping_reply_transport_implemented'])
        self.assertEqual(session.report['application_ping_missing_providers'],
                         ['replication_statistics', 'integrity_status'])
        self.assertEqual(session.report['application_pings_received'], 1)
        self.assertEqual(session.report['application_ping_telemetry_blocks'], 1)

    def test_a_response_does_not_trigger_another_reply(self):
        reply = encode_ping_back(request(40), PingBackTelemetry((1, 2, 3, 4, 5, 6), 7, 0))
        session = self.run_main([(4, 3, reply)],
                                ping_stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                ping_status_provider=lambda: 7)
        self.assertEqual([message for message in session.sent if message[2] == 3], [])
        self.assertEqual(session.report['application_ping_responses_received'], 1)

    def test_default_session_owned_providers_send_real_status_reply(self):
        session = self.run_main([(4, 3, request(1))], with_session_telemetry=True)
        replies = [message for message in session.sent if message[2] == 3]
        self.assertEqual(len(replies), 1)
        decoded = ping_packet(replies[0][3])[0]
        self.assertEqual((decoded['kind'], decoded['flags'], decoded['timestamp']), (6, 1, 1))
        self.assertEqual(decoded['reply_words'][3:], [0, 0, 0])
        self.assertEqual(decoded['trailer_words'], [0, 0])
        self.assertEqual(session.report['application_ping_missing_providers'], [])
        self.assertTrue(session.report['application_ping_provider_callbacks_configured'])
        self.assertTrue(session.report['application_ping_replies_implemented'])
        self.assertTrue(session.report['application_ping_session_telemetry']['wire_telemetry_ready'])
        self.assertEqual(session.report['application_ping_telemetry_blocks_by_provider'],
                         {'statistics': 0, 'integrity': 0})
        self.assertEqual(session.report['application_ping_session_telemetry']['missing_wire_fields'], [])

    def test_observed_integrity_failure_reaches_the_default_wire_status(self):
        from session_integrity import observe_physics_world_bounds

        def fail_bounds(session):
            observe_physics_world_bounds(False)

        session = self.run_main([(4, 3, request(1))], with_session_telemetry=True,
                                session_action=fail_bounds)
        replies = [message for message in session.sent if message[2] == 3]
        self.assertEqual(len(replies), 1)
        self.assertEqual(ping_packet(replies[0][3])[0]['trailer_words'], [0x80, 0])
        self.assertEqual(session.ping_telemetry.integrity.status_word(), 0x80)

    def test_main_records_real_container_checks_only_inside_its_session(self):
        from bytecode_container import unpack_container
        from test_bytecode_container import container_fixture

        valid = container_fixture(b'inert session input')

        def check(session):
            self.assertEqual(unpack_container(valid), b'inert session input')

        session = self.run_main([(4, 3, request(1))], with_session_telemetry=True,
                                session_action=check)
        state = session.ping_telemetry.integrity
        self.assertEqual(state.public_report()['container_checksum_checks'], 1)
        self.assertEqual(session.report['application_ping_session_telemetry']
                         ['integrity_producers']['container_checksum_checks'], 1)
        unpack_container(valid)
        self.assertEqual(state.public_report()['container_checksum_checks'], 1)
        self.assertEqual(session.report['application_ping_replies_sent'], 1)

    def test_main_restores_observer_when_session_raises(self):
        from bytecode_container import unpack_container
        from test_bytecode_container import container_fixture

        valid = container_fixture(b'inert session input')
        states = []

        def fail(session):
            states.append(session.ping_telemetry.integrity)
            unpack_container(valid)
            raise RuntimeError('inert session failed')

        with self.assertRaisesRegex(RuntimeError, 'inert session failed'):
            self.run_main([], with_session_telemetry=True, session_action=fail)
        unpack_container(valid)
        self.assertEqual(states[0].public_report()['container_checksum_checks'], 1)

    def test_other_applications_do_not_trigger_replies(self):
        session = self.run_main([(6, 3, request(1)), (4, 2, request(2))],
                                ping_stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                ping_status_provider=lambda: 7)
        self.assertEqual(session.report['application_pings_received'], 0)

    def test_transient_missing_status_never_substitutes_a_success_value(self):
        def unavailable():
            raise TelemetryUnavailable('synthetic unavailable integrity state')

        session = FakeSession()
        handler = SessionPingHandler(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                     status_provider=unavailable)
        self.assertEqual(handler.receive(session, request(1)), 0)
        self.assertEqual(session.sent, [])
        self.assertEqual(session.report['application_ping_telemetry_blocks'], 1)

    def test_transport_failure_keeps_pending_reply_and_updates_diagnostics(self):
        session = FakeSession()
        handler = SessionPingHandler(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                     status_provider=lambda: 7)
        session.fail = True
        with self.assertRaises(OSError):
            handler.receive(session, request(1))
        self.assertEqual(session.report['application_ping_replies_sent'], 0)
        self.assertEqual(session.report['application_ping_replies_pending'], 1)
        session.fail = False
        self.assertEqual(handler.flush(session), 1)
        self.assertEqual(session.report['application_ping_replies_sent'], 1)
        self.assertEqual(session.report['application_ping_replies_pending'], 0)


if __name__ == '__main__':
    unittest.main()
