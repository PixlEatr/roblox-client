"""Per-datagram delivery observations from aioquic's real packet ACK/loss path.

DATAGRAM frames are never retransmitted. An ACK only proves peer transport
receipt, not application processing. The small writer override preserves
aioquic's frame bytes and attaches its existing packet delivery handler.
"""
from contextlib import contextmanager

from aioquic.buffer import size_uint_var
from aioquic.quic.connection import QuicConnection
from aioquic.quic.packet import QuicFrameType
from aioquic.quic.packet_builder import QuicDeliveryState


class _TrackedDatagram(bytes):
    pass


class DatagramTrackingQuicConnection(QuicConnection):
    MAX_TRACKED_DATAGRAMS = 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._datagram_delivery_scope = None
        self._datagram_delivery_callbacks = {}
        self._datagram_delivery_next_id = 0
        self._datagram_delivery_counts = {'registered':0, 'framed':0,
            'acknowledged':0, 'lost':0, 'callback_errors':0}

    @contextmanager
    def observe_datagram_delivery(self, callback):
        """Observe the single datagram enqueued synchronously inside this scope.

        H3Connection retains responsibility for validation and HTTP/3 framing.
        Scope cleanup on errors prevents a later unrelated packet from being
        assigned this callback.
        """
        if not callable(callback) or self._datagram_delivery_scope is not None:
            raise ValueError('Invalid or nested datagram delivery observation')
        scope = {'callback':callback, 'queued':False}
        self._datagram_delivery_scope = scope
        try:
            yield
            if not scope['queued']:
                raise RuntimeError('Delivery observation did not enqueue a datagram')
        finally:
            self._datagram_delivery_scope = None

    def send_datagram_frame(self, data):
        scope = self._datagram_delivery_scope
        if scope is None:
            return super().send_datagram_frame(data)
        if scope['queued']:
            raise RuntimeError('Delivery observation cannot cover multiple datagrams')
        if len(self._datagram_delivery_callbacks) >= self.MAX_TRACKED_DATAGRAMS:
            raise BufferError('Datagram delivery observation limit reached')
        token = self._datagram_delivery_next_id
        self._datagram_delivery_next_id += 1
        tracked = _TrackedDatagram(data)
        tracked.delivery_token = token
        super().send_datagram_frame(tracked)
        self._datagram_delivery_callbacks[token] = scope['callback']
        self._datagram_delivery_counts['registered'] += 1
        scope['queued'] = True

    def _write_datagram_frame(self, builder, data, frame_type):
        if not isinstance(data, _TrackedDatagram):
            return super()._write_datagram_frame(builder, data, frame_type)
        assert frame_type == QuicFrameType.DATAGRAM_WITH_LENGTH
        length = len(data)
        buf = builder.start_frame(frame_type, capacity=1+size_uint_var(length)+length,
                                  handler=self._on_datagram_delivery,
                                  handler_args=(data.delivery_token,))
        buf.push_uint_var(length)
        buf.push_bytes(data)
        if self._quic_logger is not None:
            builder.quic_logger_frames.append(
                self._quic_logger.encode_datagram_frame(length=length))
        self._datagram_delivery_counts['framed'] += 1
        return True

    def _on_datagram_delivery(self, state, token):
        if state not in (QuicDeliveryState.ACKED, QuicDeliveryState.LOST):
            raise ValueError('Unknown datagram delivery state')
        callback = self._datagram_delivery_callbacks.pop(token, None)
        if callback is None:
            return
        outcome = 'acknowledged' if state == QuicDeliveryState.ACKED else 'lost'
        self._datagram_delivery_counts[outcome] += 1
        try:
            callback(outcome)
        except Exception:
            # A diagnostic consumer must not interrupt QUIC's ACK/loss handling.
            self._datagram_delivery_counts['callback_errors'] += 1

    def datagram_delivery_report(self):
        return {**self._datagram_delivery_counts,
                'pending':len(self._datagram_delivery_callbacks),
                'source':'quic_packet_delivery_handlers',
                'application_acceptance_confirmed':False}
