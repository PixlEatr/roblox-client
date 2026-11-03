"""Measured initialization-relative wire time shared by one game session.

Native 102C923EC subtracts the initial mach tick captured by 102C9271C.
Its counter fast path also subtracts an initialized counter origin. The
clock_gettime sample in that path is calibration state, not the returned epoch.
This models that origin with the current session's real monotonic clock; it
does not synthesize a server offset or the native Workspace adjustment.
"""
import math
import time


class SessionWireClock:
    def __init__(self, *, clock_ns=None):
        self._clock_ns = clock_ns or time.monotonic_ns
        self._origin_ns = self._clock_ns()
        if type(self._origin_ns) is not int or self._origin_ns < 0:
            raise ValueError('wire clock requires nonnegative monotonic nanoseconds')
        self._replicator_offset = None
        self._replicator_elapsed_ns = None

    def _elapsed_ns(self, at_ns=None):
        sample = self._clock_ns() if at_ns is None else at_ns
        if type(sample) is not int or sample < self._origin_ns:
            raise ValueError('wire clock sample predates its initialization')
        return sample - self._origin_ns

    def microseconds(self, at_ns=None):
        return self._elapsed_ns(at_ns) // 1000

    @property
    def replicator_initialized(self):
        return self._replicator_offset is not None

    def initialize_replicator(self, at_ns=None):
        if self._replicator_offset is not None:
            raise ValueError('replicator wire clock already initialized')
        elapsed_ns = self._elapsed_ns(at_ns)
        seconds = elapsed_ns / 1_000_000_000
        # 104DB2FEC quantizes its current local clock sample, retaining only
        # the fractional-millisecond correction rather than subtracting it.
        self._replicator_offset = math.trunc(seconds * 1000) / 1000 - seconds
        self._replicator_elapsed_ns = elapsed_ns

    def physics_milliseconds(self, at_ns=None):
        if self._replicator_offset is None:
            raise ValueError('replicator wire clock is not initialized')
        elapsed_ns = self._elapsed_ns(at_ns)
        if elapsed_ns < self._replicator_elapsed_ns:
            raise ValueError('physics clock sample precedes replicator initialization')
        seconds = elapsed_ns / 1_000_000_000
        result = math.trunc((seconds + self._replicator_offset) * 1000)
        if result < 0:
            raise ValueError('physics clock sample precedes replicator initialization')
        return result

    def report(self):
        return {
            'scope': 'this_session',
            'origin': 'measured_monotonic_sample_at_wire_clock_initialization',
            'elapsed_microseconds': self.microseconds(),
            'replicator_initialized': self._replicator_offset is not None,
            'replicator_quantization_offset_seconds': self._replicator_offset,
            'shared_by': ['dummy_time_sync', 'outgoing_physics'],
            'server_clock_offset_applied': False,
            'workspace_adjustment_applied': False,
            'native_frame_clock_cache_modeled': False,
        }
