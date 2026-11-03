"""Bounded local staging for explicitly configured unreliable payloads.

This module does not construct Roblox physics messages or perform network I/O.
Callers must supply an application/channel/wire-id binding and its evidence,
along with already-encoded payload bytes. Prepared records use the existing
unreliable datagram framing only; no channel or payload defaults are inferred.
"""
from collections import deque
from dataclasses import dataclass

from unreliable import control_open, encode_datagram


MAX_DATAGRAM_PAYLOAD = 1100
MAX_SEQUENCE = (1 << 62) - 1
MAX_QUEUE_SIZE = 4096


@dataclass(frozen=True)
class PreparedPhysicsDatagram:
    """A locally framed payload ready for an explicitly chosen test harness."""

    application: int
    channel: int
    wire_id: int
    sequence: int
    payload: bytes
    encoded: bytes


class PhysicsTransportAdapter:
    """Stage payloads in FIFO order without opening channels or sending them.

    ``application``, ``channel``, ``wire_id`` and ``evidence`` are mandatory:
    a caller must identify its captured/otherwise documented channel binding.
    ``evidence`` is retained as provenance only, not independently validated.
    Sequence numbering follows the existing sender's first sequence of one.
    """

    def __init__(self, application, channel, wire_id, *, evidence,
                 max_payload=MAX_DATAGRAM_PAYLOAD, max_queue=256):
        # Reuse the repository's observed channel binding bounds.
        control_open(application, channel, wire_id)
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("evidence must identify the source of the channel binding")
        if (isinstance(max_payload, bool) or not isinstance(max_payload, int)
                or not 1 <= max_payload <= MAX_DATAGRAM_PAYLOAD):
            raise ValueError("max_payload must be from 1 through 1100 bytes")
        if (isinstance(max_queue, bool) or not isinstance(max_queue, int)
                or not 1 <= max_queue <= MAX_QUEUE_SIZE):
            raise ValueError("max_queue must be from 1 through 4096 records")
        self.application = application
        self.channel = channel
        self.wire_id = wire_id
        self.evidence = evidence.strip()
        self.max_payload = max_payload
        self.max_queue = max_queue
        self._queue = deque()
        self._next_sequence = 1

    def __len__(self):
        return len(self._queue)

    def stage(self, payload):
        """Queue opaque application bytes; return their monotonic sequence."""
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes already encoded by the caller")
        if len(payload) > self.max_payload:
            raise ValueError("payload exceeds configured unreliable datagram bound")
        if len(self._queue) >= self.max_queue:
            raise BufferError("physics transport staging queue is full")
        if self._next_sequence > MAX_SEQUENCE:
            raise OverflowError("unreliable sequence number space is exhausted")
        sequence = self._next_sequence
        encoded = encode_datagram(self.wire_id, sequence, payload)
        self._queue.append(PreparedPhysicsDatagram(
            self.application, self.channel, self.wire_id, sequence,
            payload, encoded))
        self._next_sequence += 1
        return sequence

    def drain(self, limit=None):
        """Return and remove the next staged records in assigned order."""
        if limit is None:
            limit = len(self._queue)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        return [self._queue.popleft()
                for _ in range(min(limit, len(self._queue)))]
