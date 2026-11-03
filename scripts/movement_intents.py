"""Bounded, validated movement intentions for an owned local runtime.

This queue only records local requests. It does not simulate physics or send
replication data to a server.
"""
from collections import deque
import math
import numbers


_ACTIONS = {
    "move": frozenset(("kind", "direction", "relative_to_camera")),
    "stop": frozenset(("kind",)),
    "move_to": frozenset(("kind", "position")),
}


def _vector3(value, field):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{field} must contain exactly three finite numbers")
    result = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, numbers.Real):
            raise ValueError(f"{field} must contain exactly three finite numbers")
        component = float(component)
        if not math.isfinite(component) or abs(component) > 1_000_000:
            raise ValueError(f"{field} components must be finite and within +/-1,000,000")
        result.append(component)
    return tuple(result)


class MovementIntentQueue:
    """Store local movement requests in FIFO order with monotonic sequence IDs.

    ``submit`` returns the assigned integer sequence. Camera-relative requests
    are retained but marked unsupported until a camera transform is supplied.
    The consumer can inspect ``supported`` and ``unsupported`` before acting.
    """

    def __init__(self, max_size=256):
        if isinstance(max_size, bool) or not isinstance(max_size, int) or not 1 <= max_size <= 4096:
            raise ValueError("max_size must be an integer from 1 through 4096")
        self.max_size = max_size
        self._queue = deque()
        self._next_sequence = 1

    def __len__(self):
        return len(self._queue)

    def submit(self, action):
        """Validate and append an action; raise BufferError when full."""
        if not isinstance(action, dict):
            raise TypeError("movement action must be a dictionary")
        kind = action.get("kind")
        if kind not in _ACTIONS:
            raise ValueError("kind must be 'move', 'stop', or 'move_to'")
        expected = _ACTIONS[kind]
        if set(action) != expected:
            raise ValueError(f"{kind} action fields must be exactly {sorted(expected)}")

        record = {"kind": kind}
        unsupported = []
        if kind == "move":
            record["direction"] = _vector3(action["direction"], "direction")
            relative = action["relative_to_camera"]
            if not isinstance(relative, bool):
                raise ValueError("relative_to_camera must be a boolean")
            record["relative_to_camera"] = relative
            if relative:
                unsupported.append("relative_to_camera")
        elif kind == "move_to":
            record["position"] = _vector3(action["position"], "position")

        if len(self._queue) >= self.max_size:
            raise BufferError("movement intent queue is full")
        sequence = self._next_sequence
        record.update(sequence=sequence, supported=not unsupported,
                      unsupported=tuple(unsupported))
        self._queue.append(record)
        self._next_sequence += 1
        return sequence

    def drain(self, limit=None):
        """Remove and return up to ``limit`` queued records in FIFO order."""
        if limit is None:
            limit = len(self._queue)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        return [self._queue.popleft() for _ in range(min(limit, len(self._queue)))]
