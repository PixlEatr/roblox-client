import struct
import time
import unittest
from types import SimpleNamespace

from ping_back import PingBackTelemetry, encode_ping_back
from ping_responder import PingResponder, ReplicationPingStats, TelemetryUnavailable
from replication_ping import ping_packet


def ping(timestamp=40, *, unreliable=True, flags=0):
    value = b'\x83\x05' + bytes((flags,))
    value += struct.pack('<QIII', timestamp, 123, 456, 789) + bytes((unreliable,))
    if flags & 4:
        value += struct.pack('<I', 9)
    return value + b'\0'


class FakeClient:
    def __init__(self): self.sent = []; self.fail = False
    def send(self, channel, payload, application): self._send(False, channel, payload, application)
    def send_unreliable(self, channel, payload, application): self._send(True, channel, payload, application)
    def _send(self, unreliable, channel, payload, application):
        if self.fail: raise OSError('synthetic transport failure')
        self.sent.append((unreliable, channel, application, payload))


class PingResponderTests(unittest.TestCase):
    def responder(self, times, **kwargs):
        clock = iter(times)
        return PingResponder(stats_provider=lambda: ReplicationPingStats(17, (23, 29)),
                             status_provider=lambda: 0x12345678,
                             clock_ms=lambda: next(clock), **kwargs)

    def test_request_to_normal_transport_preserves_real_observation_times(self):
        handler = self.responder([100, 103, 109]); client = FakeClient()
        self.assertEqual(handler.receive(ping()), 1)
        self.assertEqual(handler.flush(client), 1)
        self.assertEqual(client.sent[0][:3], (True, 3, 4))
        reply = ping_packet(client.sent[0][3])[0]
        self.assertEqual(reply['timestamp'], 40)
        self.assertEqual(reply['first_word'], 123)
        self.assertEqual(reply['reply_words'], [100, 103, 109, 17, 23, 29])
        self.assertEqual(reply['trailer_words'], [0x12345678, 0xffffffff])

    def test_reliable_routing_and_native_zero_response_initialization(self):
        handler = self.responder([200, 203, 209]); client = FakeClient()
        handler.receive(ping(timestamp=1, unreliable=False)); handler.flush(client)
        self.assertEqual(client.sent[0][:3], (False, 3, 4))
        self.assertEqual(ping_packet(client.sent[0][3])[0]['trailer_words'][1], 0)

    def test_missing_observations_never_produce_a_clean_report(self):
        def missing(): raise TelemetryUnavailable('test observation absent')
        handler = PingResponder(stats_provider=missing, status_provider=missing, clock_ms=lambda: 1)
        client = FakeClient()
        self.assertEqual(handler.receive(ping()), 0)
        self.assertEqual(handler.flush(client), 0)
        self.assertEqual(handler.public_report()['application_ping_telemetry_blocks'], 1)
        self.assertEqual(client.sent, [])
        handler = PingResponder(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                status_provider=missing, clock_ms=lambda: 1)
        handler.receive(ping()); self.assertEqual(handler.flush(client), 0)
        self.assertEqual(len(handler.pending), 0)
        self.assertEqual(client.sent, [])

    def test_never_responds_to_reply_or_unsupported_extension(self):
        handler = self.responder([1, 2]); client = FakeClient()
        reply = encode_ping_back(ping(), PingBackTelemetry((1, 2, 3, 4, 5, 6), 7, 0))
        self.assertEqual(handler.receive(reply), 0)
        self.assertEqual(handler.receive(ping(flags=4)), 0)
        self.assertEqual(handler.flush(client), 0)
        self.assertEqual(handler.public_report()['application_ping_responses_received'], 1)
        self.assertEqual(handler.public_report()['application_ping_requests_unsupported'], 1)

    def test_batch_order_bounded_queue_and_flush(self):
        handler = PingResponder(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                status_provider=lambda: 0x80000000,
                                clock_ms=lambda: 1, max_pending=2)
        packet = ping(1)[:-1] + ping(2)[1:-1] + ping(3)[1:]
        self.assertEqual(handler.receive(packet), 2)
        client = FakeClient(); self.assertEqual(handler.flush(client, limit=1), 1)
        self.assertEqual(len(handler.pending), 1)
        self.assertEqual(handler.flush(client), 1)
        self.assertEqual([ping_packet(x[3])[0]['timestamp'] for x in client.sent], [1, 2])
        self.assertEqual(handler.public_report()['application_ping_queue_overflows'], 1)

    def test_clock_words_wrap_without_modifying_echo(self):
        handler = self.responder([(1 << 32)-2, (1 << 32)+1, (1 << 32)+5])
        client = FakeClient(); handler.receive(ping((1 << 63)+1)); handler.flush(client)
        reply = ping_packet(client.sent[0][3])[0]
        self.assertEqual(reply['timestamp'], (1 << 63)+1)
        self.assertEqual(reply['reply_words'][:3], [0xfffffffe, 1, 5])

    def test_malformed_frame_has_no_partial_send_and_transport_error_is_visible(self):
        handler = self.responder([1, 2, 3, 4, 5]); client = FakeClient()
        with self.assertRaises(ValueError): handler.receive(ping()[:-1])
        self.assertEqual(len(handler.pending), 0)
        handler.receive(ping()); client.fail = True
        with self.assertRaises(OSError): handler.flush(client)
        self.assertEqual(handler.replies_sent, 0)
        self.assertEqual(len(handler.pending), 1)
        client.fail = False; handler.flush(client)
        self.assertEqual(handler.replies_sent, 1)

    def test_flush_budget_also_bounds_unavailable_telemetry(self):
        def missing(): raise TelemetryUnavailable('synthetic unavailable state')
        handler = PingResponder(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                status_provider=missing, clock_ms=lambda: 1)
        for _ in range(3): handler.receive(ping())
        self.assertEqual(handler.flush(FakeClient(), limit=1), 0)
        self.assertEqual(len(handler.pending), 2)
        self.assertEqual(handler.telemetry_blocks, 1)

    def test_invalid_provider_values_never_reach_transport(self):
        for value in (None, False, -1, 1 << 32):
            handler = PingResponder(stats_provider=lambda: ReplicationPingStats(1, (2, 3)),
                                    status_provider=lambda: value, clock_ms=lambda: 1)
            handler.receive(ping()); client = FakeClient()
            with self.assertRaises(ValueError): handler.flush(client)
            self.assertEqual(client.sent, [])
        with self.assertRaises(TypeError): PingResponder(stats_provider=None, status_provider=None)

    def test_real_native_session_datagram_framing_without_allocation_or_network(self):
        from native_session import NativeSession
        from unreliable import parse_datagram, parse_control_open
        from game_wire import FrameDecoder
        from channels import parse_reliable_header
        from game_wire import CONTROL_CHANNEL

        # Exercise the existing send/open/record methods themselves. Do not call
        # __init__, which obtains a real allocation; these are inert transport sinks.
        client = NativeSession.__new__(NativeSession)
        streams = []; datagrams = []
        client.quic = SimpleNamespace(send_stream_data=lambda stream, data: streams.append((stream, data)))
        client.http = SimpleNamespace(create_webtransport_stream=lambda *a, **kw: 42,
                                      send_datagram=lambda stream, data: datagrams.append((stream, data)))
        client.connect_stream = 0; client.outgoing = {}; client.unreliable_outgoing = {}
        client.next_wire_id = 2; client.duration = 1; client.capture = []; client.capture_bytes = 0
        client.started = time.monotonic(); client.report = {'channels_sent': [], 'messages': []}

        handler = self.responder([100, 101, 102]); handler.receive(ping())
        self.assertEqual(handler.flush(client), 1)
        self.assertEqual(parse_reliable_header(streams[0][1]), (4, CONTROL_CHANNEL))
        control = FrameDecoder().feed(streams[1][1])[0]
        self.assertEqual(parse_control_open(control), (4, 3, 2))
        datagram = parse_datagram(datagrams[0][1])
        self.assertEqual((datagram['wire_id'], datagram['sequence'], datagram['complete']), (2, 1, True))
        reply = ping_packet(datagram['payload'])[0]
        self.assertEqual(reply['reply_words'][:3], [100, 101, 102])
        self.assertEqual(reply['trailer_words'], [0x12345678, 0xffffffff])
        self.assertEqual(client.report['messages'][-1]['channel'], 3)


if __name__ == '__main__': unittest.main()
