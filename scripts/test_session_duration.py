from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import native_session as native


class SessionDurationTests(unittest.TestCase):
    def session(self, duration):
        allocation = (123, ('127.0.0.1', 12345), '127.0.0.1', bytes(16), [])
        with patch.object(native, 'allocation', return_value=allocation), \
                patch.object(native, 'QuicConnection') as factory:
            factory.return_value.datagrams_to_send.return_value = []
            factory.return_value.next_event.return_value = None
            factory.return_value.get_timer.return_value = None
            factory.return_value.datagram_delivery_report.return_value = {}
            session = native.NativeSession(duration=duration)
        return session

    def run_with_clock(self, session, tick):
        clock = [0.0]
        with tempfile.TemporaryDirectory(prefix='session-duration-') as directory:
            root = Path(directory)
            (root / 'outputs/gamejoin').mkdir(parents=True)
            sock = Mock()
            # The session may proactively drain its nonblocking socket before
            # select. This fixture has no incoming datagrams.
            sock.recvfrom.side_effect = BlockingIOError

            def advance(*args):
                clock[0] += 60
                return ([], [], [])

            with patch.object(native, 'ROOT', root), \
                    patch.object(native, 'write_private') as private_write, \
                    patch.object(native, 'certificate_pins', return_value=nullcontext()), \
                    patch.object(native.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(native.socket, 'socket', return_value=sock), \
                    patch.object(native.select, 'select', side_effect=advance):
                report = session.run(lambda *_: None, lambda *_: None, on_tick=tick)
            self.assertTrue((root / 'outputs/gamejoin/game-session.json').is_file())
            self.assertIsInstance(private_write.call_args_list[0].args[1], list)
            sock.close.assert_called_once()
        return report

    def test_unlimited_survives_time_and_traffic_caps_until_explicit_close(self):
        session = self.session(0)
        visited = []

        def tick(client, now):
            visited.append(now)
            client.report['datagrams_sent'] = 10001
            client.report['bytes_received'] = 65 * 1024 * 1024
            if now >= 240:
                client.close('Test user quit')

        report = self.run_with_clock(session, tick)
        self.assertIn(240, visited)
        self.assertNotIn('duration_limit_reached', report)
        self.assertNotIn('traffic_limit_reached', report)
        self.assertTrue(report['local_close_requested'])

    def test_positive_duration_still_stops_and_can_exceed_120_seconds(self):
        session = self.session(180)
        report = self.run_with_clock(session, lambda *_: None)
        self.assertTrue(report['duration_limit_reached'])
        self.assertEqual(report['elapsed_seconds'], 180)
        with self.assertRaises(ValueError):
            self.session(-1)

    def test_unlimited_diagnostics_retain_a_bounded_tail(self):
        session = self.session(0)
        session.started = 0
        for _ in range(5000):
            session.record('received', 4, 1, b'fixture')
        self.assertEqual(len(session.capture), 4096)
        self.assertEqual(len(session.report['messages']), 4096)
        self.assertEqual(session.report['capture_entries_evicted'], 904)
        # Byte budget applies independently of the entry count.
        session.capture_entry({'data_base64': 'x' * (16 * 1024 * 1024 + 1)})
        self.assertEqual(len(session.capture), 0)
        self.assertEqual(session.capture_bytes, 0)


if __name__ == '__main__':
    unittest.main()
