"""Bounded reassembly for the generic unreliable-datagram fragment envelope.

This module only handles the fields returned by ``unreliable.parse_datagram``;
it makes no assumptions about the application payload.
"""
from collections import OrderedDict
import time


class FragmentError(ValueError):
    """A fragment is malformed or conflicts with its pending message."""


class FragmentReassembler:
    """Reassemble fragments keyed by (wire_id, sequence).

    ``push`` takes a parsed datagram mapping and returns completed bytes, or
    ``None`` while the message is incomplete. Identical duplicates and
    byte-identical overlaps are accepted. Conflicting overlaps or totals drop
    the pending message and raise ``FragmentError``. When limits are reached,
    the oldest incomplete messages are evicted.
    """

    def __init__(self, *, max_pending=128, max_pending_bytes=2 * 1024 * 1024,
                 max_message_bytes=65536, expiry_seconds=10.0,
                 clock=time.monotonic):
        if max_pending < 1 or max_pending_bytes < 1 or max_message_bytes < 1:
            raise ValueError("reassembly limits must be positive")
        if expiry_seconds <= 0:
            raise ValueError("expiry_seconds must be positive")
        self.max_pending = max_pending
        self.max_pending_bytes = max_pending_bytes
        self.max_message_bytes = max_message_bytes
        self.expiry_seconds = expiry_seconds
        self._clock = clock
        self._pending = OrderedDict()
        self._allocated = 0

    @property
    def pending_count(self):
        return len(self._pending)

    @property
    def pending_bytes(self):
        return self._allocated

    def expire(self, now=None):
        """Discard expired entries and return the number removed."""
        now = self._clock() if now is None else now
        expired = [key for key, item in self._pending.items()
                   if now - item["created"] >= self.expiry_seconds]
        for key in expired:
            self._drop(key)
        return len(expired)

    def _drop(self, key):
        item = self._pending.pop(key, None)
        if item is not None:
            self._allocated -= item["total"] * 2  # data and coverage bitmap

    def push(self, fragment, *, now=None):
        """Add a mapping returned by ``unreliable.parse_datagram``."""
        now = self._clock() if now is None else now
        self.expire(now)
        try:
            wire_id = fragment["wire_id"]
            sequence = fragment["sequence"]
            total = fragment["total"]
            offset = fragment["offset"]
            payload = bytes(fragment["payload"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FragmentError("invalid fragment mapping") from exc
        if (not isinstance(wire_id, int) or not 0 < wire_id < 2**31 or
                not isinstance(sequence, int) or not 0 <= sequence < 2**62):
            raise FragmentError("invalid fragment key")
        if not isinstance(total, int) or total <= 0 or total > self.max_message_bytes:
            raise FragmentError("invalid fragmented message size")
        if not isinstance(offset, int) or offset < 0 or offset + len(payload) > total:
            raise FragmentError("fragment is outside declared message bounds")
        if not payload:
            raise FragmentError("empty fragment cannot make progress")

        key = (wire_id, sequence)
        item = self._pending.get(key)
        if item is not None and item["total"] != total:
            self._drop(key)
            raise FragmentError("inconsistent total for pending message")
        if item is None:
            allocation = total * 2
            if allocation > self.max_pending_bytes:
                raise FragmentError("message exceeds pending-byte limit")
            while (len(self._pending) >= self.max_pending or
                   self._allocated + allocation > self.max_pending_bytes):
                oldest = next(iter(self._pending))
                self._drop(oldest)
            item = {"total": total, "created": now,
                    "data": bytearray(total), "covered": bytearray(total),
                    "received": 0}
            self._pending[key] = item
            self._allocated += allocation
        else:
            self._pending.move_to_end(key)

        data, covered = item["data"], item["covered"]
        for index, value in enumerate(payload, offset):
            if covered[index]:
                if data[index] != value:
                    self._drop(key)
                    raise FragmentError("overlapping fragments disagree")
            else:
                data[index] = value
                covered[index] = 1
                item["received"] += 1

        if item["received"] == total:
            result = bytes(data)
            self._drop(key)
            return result
        return None
