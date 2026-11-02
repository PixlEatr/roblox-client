import struct
import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dummy_sync import TimeSyncProbe, request_packet, response_echo
from game_wire import CONTROL_CHANNEL
from native_session import NativeSession


class DummySyncTests(unittest.TestCase):
    def test_native_request_vector_and_bounds(self):
        self.assertEqual(request_packet(0x0102030405060708),
                         bytes.fromhex('030807060504030201') + bytes(16))
        for bad in (-1, 1 << 64, True, 1.5):
            with self.assertRaises(ValueError): request_packet(bad)

    def test_request_bound_and_delayed_ticks_do_not_burst(self):
        probe = TimeSyncProbe()
        self.assertIsNone(probe.due(1.0, 1000000))
        probe.start(1.0)
        self.assertIsNotNone(probe.due(1.0, 1000000))
        self.assertIsNone(probe.due(1.999, 1999000))
        self.assertIsNotNone(probe.due(5.0, 5000000))
        self.assertIsNone(probe.due(5.0, 5000001))
        for now in (6., 7., 8.):
            self.assertIsNotNone(probe.due(now, int(now * 1000000)))
        self.assertIsNone(probe.due(9., 9000000))
        self.assertEqual(probe.public_report()['time_sync_requests_sent'], 5)

    def test_echo_match_rtt_duplicates_and_opaque_words(self):
        for length in (25, 33):
            probe = TimeSyncProbe()
            probe.start(1.0)
            probe.due(1.0, 1234)
            response = b'\3' + struct.pack('<Q', 1234) + b'\xff' * (length - 9)
            self.assertEqual(response_echo(response), 1234)
            self.assertFalse(probe.receive(response, .9))
            self.assertTrue(probe.receive(response, 1.025))
            self.assertFalse(probe.receive(response, 1.030))
            self.assertEqual(probe.public_report(), {'time_sync_requests_sent': 1,
                             'time_sync_echoed_responses': 1, 'time_sync_measured_rtt_ms': [25.0]})
            self.assertNotIn('opaque', str(probe.public_report()))

    def test_unsolicited_and_invalid_replies_are_rejected(self):
        probe = TimeSyncProbe()
        probe.start(1.0)
        probe.due(1.0, 1234)
        for response in (b'', b'\3' * 24, b'\3' * 26, b'\2' + bytes(24),
                         request_packet(4321)):
            self.assertFalse(probe.receive(response, 1.1))
        self.assertEqual(probe.public_report()['time_sync_echoed_responses'], 0)

    def test_channel_registration_without_empty_datagram(self):
        client = NativeSession.__new__(NativeSession)
        client.unreliable_outgoing = {}
        client.next_wire_id = 2
        client.report = {'channels_sent': []}
        sent = []
        client.send = lambda channel, payload, application=4: sent.append((application, channel, payload))
        first = client.open_unreliable_channel(6, 2)
        self.assertIs(client.open_unreliable_channel(6, 2), first)
        second = client.open_unreliable_channel(6, 3)
        self.assertEqual((first['wire_id'], second['wire_id']), (2, 4))
        self.assertEqual([item[2].hex() for item in sent],
                         ['02060000000200000002', '02060000000300000004'])

    def test_optional_tick_wakes_idle_transport_without_network(self):
        # The fake select clock advances to the requested deadline. No socket is
        # created and no credentials/allocation are read by this fixture.
        clock = [0.0]
        waits, ticks = [], []
        def select_wait(read, write, error, timeout):
            self.assertGreater(timeout, 0)
            waits.append(timeout)
            clock[0] += timeout
            return [], [], []
        fake_socket = SimpleNamespace(bind=lambda address: None, setblocking=lambda flag: None,
                                      close=lambda: None)
        client = NativeSession.__new__(NativeSession)
        client.duration = 1
        client.endpoint = ('127.0.0.1', 1)
        client.pins, client.capture, client.finished = [], [], False
        client.report = {'datagrams_sent': 0, 'bytes_received': 0, 'run_id': 'synthetic'}
        client.quic = SimpleNamespace(connect=lambda endpoint, now: None, datagrams_to_send=lambda now: [],
                                      get_timer=lambda: None, next_event=lambda: None,
                                      close=lambda **kwargs: None)
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outputs/gamejoin').mkdir(parents=True)
            with patch('native_session.socket.socket', return_value=fake_socket), \
                    patch('native_session.select.select', side_effect=select_wait), \
                    patch('native_session.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('native_session.certificate_pins', side_effect=lambda *args: nullcontext()), \
                    patch('native_session.write_private'), patch('native_session.ROOT', root):
                client.run(lambda client: None, lambda *args: None, report_name='synthetic',
                           on_tick=lambda client, now: ticks.append(now), tick_interval=.2)
        self.assertEqual(len(ticks), 5)
        self.assertEqual(len(waits), 5)
        self.assertAlmostEqual(ticks[-1], .8)
        self.assertEqual(client.report['elapsed_seconds'], 1.0)

    def test_standalone_probe_native_startup_order_and_unique_report(self):
        import probe_time_sync
        clock, calls = [0.0], []
        class FakeSession:
            def __init__(self, duration):
                self.report = {}
                self.duration = duration
            def send(self, channel, payload, application=4):
                calls.append(('control', application, channel, payload))
            def open_channel(self, application, channel):
                calls.append(('reliable', application, channel))
            def open_unreliable_channel(self, application, channel):
                calls.append(('unreliable', application, channel))
            def send_unreliable(self, channel, payload, application=4):
                calls.append(('request', application, channel, clock[0]))
                clock[0] += .025
                self.on_packet(self, application, channel, payload + b'\xff' * 8)
                clock[0] -= .025
            def run(self, on_ready, on_packet, report_name, *, on_tick):
                calls.append(('report', report_name, self.duration))
                self.on_packet = on_packet
                on_ready(self)
                for now in (0., 1., 2., 3., 4., 5.):
                    clock[0] = now
                    on_tick(self, now)
                return self.report
        with patch('probe_time_sync.NativeSession', FakeSession), \
                patch('probe_time_sync.time.monotonic', side_effect=lambda: clock[0]), \
                patch('probe_time_sync.time.monotonic_ns', side_effect=lambda: int(clock[0] * 1000000000)), \
                patch('sys.argv', ['probe_time_sync.py']), redirect_stdout(io.StringIO()):
            self.assertEqual(probe_time_sync.main(), 0)
        self.assertEqual(calls[:5], [('report', 'time-sync-probe', 8),
                                    ('control', 6, CONTROL_CHANNEL, b'\0\0'),
                                    ('reliable', 6, 1), ('unreliable', 6, 2), ('unreliable', 6, 3)])
        self.assertEqual([item[3] for item in calls if item[0] == 'request'], [0., 1., 2., 3., 4.])

    def test_tick_interval_validation_precedes_network(self):
        client = NativeSession.__new__(NativeSession)
        for interval in (0, -1, 2, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                client.run(None, None, on_tick=lambda *args: None, tick_interval=interval)


if __name__ == '__main__':
    unittest.main()
