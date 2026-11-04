"""Offline tests of the live harness's observation boundary; no allocation."""
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aioquic.quic.packet_builder import QuicDeliveryState
from aioquic.quic.stream import QuicStreamSender
from game_wire import frame
from validate_chat_delivery_live import ObservedSession, ObservedChatConsole
from chat_console import ChatConsole


class LiveObservationTests(unittest.TestCase):
    def test_diagnostic_ready_delay_waits_for_spawn_and_emits_only_once(self):
        console = object.__new__(ObservedChatConsole)
        console.ready_after_spawn_seconds = 2
        console.chat = Mock(ready_sent=False)
        console.chat.summary.return_value = {}
        console.failure = None
        console.console = Mock()
        client = SimpleNamespace(report={}, started=100.)
        world = object()
        def ready(instance, client, world):
            instance.chat.ready_sent = True
        with patch('validate_chat_delivery_live.time.monotonic', return_value=105.), \
             patch.object(ChatConsole, 'tick', autospec=True, side_effect=ready) as base:
            console.tick(client, world)
            self.assertEqual(base.call_count, 0)
            console.chat.poll.assert_called_once_with(world)
            client.report['spawn_verified_at_seconds'] = 4.
            console.tick(client, world)
            self.assertEqual(base.call_count, 0)
        with patch('validate_chat_delivery_live.time.monotonic', return_value=106.), \
             patch.object(ChatConsole, 'tick', autospec=True, side_effect=ready) as base:
            console.tick(client, world)
            self.assertEqual(base.call_count, 1)
            self.assertEqual(client.report['chat_ready_sent_at_seconds'], 6.)
        with patch('validate_chat_delivery_live.time.monotonic', return_value=108.), \
             patch.object(ChatConsole, 'tick', autospec=True, side_effect=ready):
            console.tick(client, world)
            self.assertEqual(client.report['chat_ready_sent_at_seconds'], 6.)

    def make_session(self):
        client = object.__new__(ObservedSession)
        sender = QuicStreamSender(stream_id=7, writable=True)
        sender.write(b'prior framing')
        client.quic = SimpleNamespace(
            _streams={7: SimpleNamespace(sender=sender)},
            send_stream_data=Mock(side_effect=lambda stream, data: sender.write(data)))
        client.outgoing = {(4, 1): 7}
        client.record = Mock()
        client.report = {}
        client.started = time.monotonic()
        client._chat_watch = None
        client._watch_chat_send = True
        return client, sender

    def test_tracks_only_message_frame_and_preserves_observed_ack(self):
        client, sender = self.make_session()
        payload = b'authored-test-frame'
        client.send(1, payload)
        report = client.report['chat_transport_delivery']
        self.assertEqual(report['range_bytes'], len(frame(payload)))
        self.assertEqual(report['state'], 'queued')
        packet = sender.get_frame(4096)
        client.update_chat_delivery()
        self.assertEqual(client.report['chat_transport_delivery']['state'], 'sent')
        sender.on_data_delivery(QuicDeliveryState.ACKED, packet.offset,
                                packet.offset+len(packet.data), packet.fin)
        client.update_chat_delivery()
        report = client.report['chat_transport_delivery'].copy()
        self.assertTrue(report['transport_acknowledged'])
        self.assertFalse(report['application_result_observed'])
        sender.reset(1)
        client.update_chat_delivery()
        self.assertEqual(client.report['chat_transport_delivery'], report)

    def test_additional_message_is_rejected_before_transport_write(self):
        client, sender = self.make_session()
        client.send(1, b'first')
        before = sender._buffer_stop
        with self.assertRaises(RuntimeError):
            client.send(1, b'second')
        self.assertEqual(sender._buffer_stop, before)
        self.assertEqual(client.quic.send_stream_data.call_count, 1)

    def test_other_sends_are_unmodified_and_not_watched(self):
        client, sender = self.make_session()
        client._watch_chat_send = False
        client.send(1, b'other')
        self.assertIsNone(client._chat_watch)
        self.assertEqual(client.report, {})
        client.quic.send_stream_data.assert_called_once_with(7, frame(b'other'))


if __name__ == '__main__':
    unittest.main()
