import struct
import unittest
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dummy_sync import TimeSyncProbe
from session_wire_clock import SessionWireClock


class SessionWireClockTests(unittest.TestCase):
    def test_actual_game_callbacks_emit_relative_requests_and_decode_matching_replies(self):
        import game_session
        from test_ping_session import FakeSession
        session = FakeSession()
        session.finished = False
        driver = Mock()
        driver.tick.return_value = {'network_send_attempt_count':0,'stop_reason':None}
        now_ns = [38_000_000_000_000]
        session.open_channel = lambda *args: None
        session.open_unreliable_channel = lambda *args: None
        def run(ready, receive, *, on_tick, **kwargs):
            # NativeSession drives on_tick before the transport-ready callback.
            on_tick(session, now_ns[0] / 1e9)
            driver.tick.assert_not_called()
            ready(session)
            now_ns[0] += 1_000_000_000
            on_tick(session, now_ns[0] / 1e9)
            self.assertEqual(driver.tick.call_args.kwargs['timestamp_ms'],1000)
            request = next(data for _,app,channel,data in session.sent if app==6 and channel==3)
            self.assertEqual(struct.unpack_from('<Q', request, 1)[0], 1_000_000)
            now_ns[0] += 20_000_000
            receive(session, 6, 3, request[:9] + struct.pack('<QQ', 0, 1_020_000))
            return session.report
        session.run = run
        args = game_session.parse_args(['--time-sync'])
        # Isolate callback ordering from ticket parsing and physics modeling.
        args.physics = True
        with TemporaryDirectory() as folder:
            root = Path(folder);(root/'secrets').mkdir()
            (root/'secrets/last-join.json').write_text(json.dumps({'body':{'joinScript':{
                'ClientTicket':'synthetic','UserId':1,'GameId':'synthetic'}}}))
            with patch.object(game_session,'ROOT',root), \
                 patch.object(game_session,'parse_args',return_value=args), \
                 patch.object(game_session,'NativeSession',return_value=session), \
                 patch.object(game_session,'issued_early_auth',return_value=b'early'), \
                 patch.object(game_session,'ClientScriptRuntime'), \
                 patch.object(game_session,'_create_physics_driver',return_value=(driver,None,'character_physics')), \
                 patch('session_wire_clock.time.monotonic_ns',side_effect=lambda:now_ns[0]), \
                 patch('game_session.time.monotonic',side_effect=lambda:now_ns[0]/1e9), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(game_session.main(),0)
        self.assertEqual(session.report['time_sync_echoed_responses'],1)
        self.assertEqual(session.report['time_sync_measured_rtt_ms'],[20.])
        self.assertEqual(session.report['time_sync_native_callback_samples'][0]['callback_offset_ms'],10)
        self.assertTrue(session.report['wire_clock']['replicator_initialized'])

    def test_wire_time_excludes_absolute_host_uptime(self):
        origin = 38_000_000_000_000
        clock = SessionWireClock(clock_ns=lambda: origin)
        clock.initialize_replicator(origin)
        self.assertEqual(clock.microseconds(origin + 1_250_000_000), 1_250_000)
        self.assertEqual(clock.physics_milliseconds(origin + 1_250_000_000), 1250)

    def test_replicator_retains_fractional_millisecond_correction(self):
        clock = SessionWireClock(clock_ns=lambda: 10_000_000_000)
        clock.initialize_replicator(10_123_750_000)
        self.assertAlmostEqual(clock.report()['replicator_quantization_offset_seconds'], -.00075)
        self.assertEqual(clock.physics_milliseconds(11_234_750_000), 1234)
        self.assertEqual(clock.microseconds(11_234_750_000), 1_234_750)
        with self.assertRaises(ValueError):clock.physics_milliseconds(10_100_000_000)
        with self.assertRaises(ValueError):clock.initialize_replicator()

    def test_time_sync_uses_same_origin_and_preserves_actual_rtt(self):
        origin = 38_000_000_000_000
        clock = SessionWireClock(clock_ns=lambda: origin)
        clock.initialize_replicator(origin)
        probe = TimeSyncProbe();probe.start(38_000.)
        request = probe.due(38_001., clock.microseconds(origin + 1_000_000_000))
        self.assertEqual(struct.unpack_from('<Q', request, 1)[0], 1_000_000)
        response = request[:9] + struct.pack('<QQ', 0, 1_020_000)
        self.assertTrue(probe.receive(response, 38_001.02,
            local_receive_us=clock.microseconds(origin + 1_020_000_000)))
        self.assertAlmostEqual(probe.rtt_ms[0], 20, places=5)
        self.assertEqual(probe.native_timing_samples[0].local_receive_us, 1_020_000)
        self.assertEqual(clock.physics_milliseconds(origin + 1_020_000_000), 1020)

    def test_sessions_have_independent_origins_and_reject_invalid_samples(self):
        first = SessionWireClock(clock_ns=lambda: 1000)
        second = SessionWireClock(clock_ns=lambda: 11_000)
        self.assertEqual(first.microseconds(12_000), 11)
        self.assertEqual(second.microseconds(12_000), 1)
        with self.assertRaises(ValueError):first.physics_milliseconds(12_000)
        for sample in (999, True, 1.2, float('nan')):
            with self.subTest(sample=sample), self.assertRaises(ValueError):first.microseconds(sample)


if __name__ == '__main__':unittest.main()
