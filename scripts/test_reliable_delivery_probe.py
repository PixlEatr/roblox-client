import unittest

from aioquic.quic.packet_builder import QuicDeliveryState
from aioquic.quic.stream import QuicStreamSender

from reliable_delivery_probe import ReliableDeliveryProbe


class ReliableDeliveryProbeTests(unittest.TestCase):
    def sender_with_data(self, size=8):
        sender = QuicStreamSender(stream_id=0, writable=True)
        sender.write(b"x" * size)
        return sender

    def send(self, sender, max_size=64):
        frame = sender.get_frame(max_size=max_size)
        self.assertIsNotNone(frame)
        return frame

    def ack(self, sender, frame):
        sender.on_data_delivery(
            QuicDeliveryState.ACKED,
            frame.offset,
            frame.offset + len(frame.data),
            frame.fin,
        )

    def lose(self, sender, frame):
        sender.on_data_delivery(
            QuicDeliveryState.LOST,
            frame.offset,
            frame.offset + len(frame.data),
            frame.fin,
        )

    def test_queued_then_sent_then_transport_acknowledged(self):
        sender = self.sender_with_data()
        probe = ReliableDeliveryProbe(2, 6)
        self.assertEqual(probe.classify(sender), "queued")
        frame = self.send(sender)
        self.assertEqual(probe.classify(sender), "sent")
        self.ack(sender, frame)
        self.assertEqual(probe.classify(sender), "transport_acknowledged")

    def test_partial_ack_does_not_confirm_full_range(self):
        sender = self.sender_with_data()
        probe = ReliableDeliveryProbe(0, 8)
        first = self.send(sender, max_size=4)
        second = self.send(sender, max_size=4)
        self.ack(sender, first)
        self.assertEqual(probe.classify(sender), "sent")
        self.ack(sender, second)
        self.assertEqual(probe.classify(sender), "transport_acknowledged")

    def test_out_of_order_ack_requires_gap_to_be_filled(self):
        sender = self.sender_with_data()
        probe = ReliableDeliveryProbe(0, 8)
        first = self.send(sender, max_size=4)
        second = self.send(sender, max_size=4)
        self.ack(sender, second)
        self.assertEqual(probe.classify(sender), "sent")
        self.ack(sender, first)
        self.assertEqual(probe.classify(sender), "transport_acknowledged")

    def test_acked_watched_subrange_does_not_require_unwatched_prefix(self):
        sender = self.sender_with_data()
        first = self.send(sender, max_size=4)
        second = self.send(sender, max_size=4)
        self.ack(sender, second)
        self.assertEqual(ReliableDeliveryProbe(4, 8).classify(sender),
                         "transport_acknowledged")
        self.assertEqual(ReliableDeliveryProbe(0, 8).classify(sender), "sent")
        # Keep the first frame live in the test setup; its prefix is still open.
        self.assertEqual((first.offset, len(first.data)), (0, 4))

    def test_ack_ranges_do_not_bridge_a_gap(self):
        sender = self.sender_with_data()
        probe = ReliableDeliveryProbe(0, 8)
        first = self.send(sender, max_size=3)
        second = self.send(sender, max_size=3)
        third = self.send(sender, max_size=3)
        self.ack(sender, first)
        self.ack(sender, third)
        self.assertEqual(probe.classify(sender), "sent")
        self.ack(sender, second)
        self.assertEqual(probe.classify(sender), "transport_acknowledged")

    def test_loss_is_not_acknowledgement_and_resend_can_be_acked(self):
        sender = self.sender_with_data()
        probe = ReliableDeliveryProbe(0, 8)
        frame = self.send(sender)
        self.lose(sender, frame)
        self.assertEqual(probe.classify(sender), "sent")
        resent = self.send(sender)
        self.ack(sender, resent)
        self.assertEqual(probe.classify(sender), "transport_acknowledged")

    def test_reset_and_missing_state(self):
        sender = self.sender_with_data()
        sender.reset(error_code=7)
        self.assertEqual(ReliableDeliveryProbe(0, 4).classify(sender), "reset")
        probe = ReliableDeliveryProbe(0, 4)
        self.assertEqual(probe.classify(None), "unknown")
        self.assertEqual(probe.classify(object()), "unknown")

    def test_missing_acked_ranges_are_unknown(self):
        class IncompleteState:
            _buffer_start = 0
            _buffer_stop = 4
            highest_offset = 4
            _reset_error_code = None

        self.assertEqual(ReliableDeliveryProbe(0, 4).classify(IncompleteState()),
                         "unknown")

    def test_ack_range_beyond_sent_high_water_is_unknown(self):
        sender = self.sender_with_data()
        self.send(sender, max_size=4)
        sender._acked.add(0, 8)
        self.assertEqual(ReliableDeliveryProbe(0, 8).classify(sender), "unknown")

    def test_unknown_for_range_not_present_in_sender(self):
        sender = self.sender_with_data(size=4)
        self.assertEqual(ReliableDeliveryProbe(4, 5).classify(sender), "unknown")

    def test_summary_is_bounded_and_transport_only(self):
        sender = self.sender_with_data(size=4)
        summary = ReliableDeliveryProbe(0, 4).summary(sender)
        self.assertEqual(summary, {
            "state": "queued",
            "range_bytes": 4,
            "transport_acknowledged": False,
            "application_result_observed": False,
        })
        self.assertFalse({"payload", "stream_id", "user_id", "account_id"} & set(summary))

    def test_rejects_invalid_or_oversized_range(self):
        for start, stop in ((-1, 1), (1, 1), (2, 1), (0, 1 << 62),
                            (0, 16 * 1024 * 1024 + 1)):
            with self.subTest(start=start, stop=stop):
                with self.assertRaises(ValueError):
                    ReliableDeliveryProbe(start, stop)


if __name__ == "__main__":
    unittest.main()
