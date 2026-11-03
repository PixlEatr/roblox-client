"""Read-only progress probe for one byte range in an aioquic send stream.

The probe inspects QuicStreamSender bookkeeping only. A transport ACK means
the peer's QUIC stack acknowledged those bytes; it does not confirm Roblox
accepted or delivered an application message. No payload or stream identifier
is retained or returned.
"""

from __future__ import annotations


MAX_RANGE_BYTES = 16 * 1024 * 1024
MAX_STREAM_OFFSET = (1 << 62) - 1


class ReliableDeliveryProbe:
    """Classify progress for a known half-open stream range [start, stop)."""

    def __init__(self, start: int, stop: int) -> None:
        if (type(start) is not int or type(stop) is not int
                or start < 0 or stop <= start or stop > MAX_STREAM_OFFSET
                or stop - start > MAX_RANGE_BYTES):
            raise ValueError("Invalid or oversized QUIC stream byte range")
        self._start = start
        self._stop = stop

    @staticmethod
    def _valid_offset(value: object) -> bool:
        return type(value) is int and 0 <= value <= MAX_STREAM_OFFSET

    def _fully_acknowledged(
        self, sender: object, buffer_start: int, highest_offset: int
    ) -> bool | None:
        acked = getattr(sender, "_acked", None)
        if acked is None:
            return None
        try:
            ranges = iter(acked)
        except (TypeError, AttributeError):
            return None

        cursor = self._start
        if cursor < buffer_start:
            cursor = min(self._stop, buffer_start)
        if cursor == self._stop:
            return True

        previous_stop = buffer_start
        for item in ranges:
            left = getattr(item, "start", None)
            right = getattr(item, "stop", None)
            if (not self._valid_offset(left) or not self._valid_offset(right)
                    or right <= left or left < previous_stop
                    or right > highest_offset):
                return None
            previous_stop = right
            if right <= cursor:
                continue
            if left > cursor:
                return False
            cursor = min(self._stop, right)
            if cursor == self._stop:
                return True
        return False

    def classify(self, sender: object | None) -> str:
        """Return queued, sent, transport_acknowledged, reset, or unknown."""
        if sender is None:
            return "unknown"
        try:
            buffer_start = getattr(sender, "_buffer_start")
            buffer_stop = getattr(sender, "_buffer_stop")
            highest_offset = getattr(sender, "highest_offset")
            reset_error = getattr(sender, "_reset_error_code")
        except (AttributeError, TypeError):
            return "unknown"

        if (not self._valid_offset(buffer_start)
                or not self._valid_offset(buffer_stop)
                or not self._valid_offset(highest_offset)
                or buffer_start > buffer_stop or buffer_start > highest_offset
                or highest_offset > buffer_stop):
            return "unknown"
        if reset_error is not None:
            return "reset"

        # The sender retains the full written-stream end even as its buffer
        # advances over contiguous ACKs. If our interval exceeds it, we cannot
        # safely infer anything about the requested range.
        if self._stop > buffer_stop:
            return "unknown"
        acknowledged = self._fully_acknowledged(sender, buffer_start, highest_offset)
        if acknowledged is None:
            return "unknown"
        if acknowledged:
            return "transport_acknowledged"

        # highest_offset is a sent high-water mark, never an ACK indicator.
        # Only call the whole watched range sent when it lies below that mark.
        if self._stop <= highest_offset:
            return "sent"
        return "queued"

    def summary(self, sender: object | None) -> dict[str, object]:
        state = self.classify(sender)
        return {
            "state": state,
            "range_bytes": self._stop - self._start,
            "transport_acknowledged": state == "transport_acknowledged",
            "application_result_observed": False,
        }
