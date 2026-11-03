"""Connection-owned ping state and separately measured receive diagnostics.

On the supported QUIC transport there is no RakNet peer or RakNet packet
metadata. The three legacy statistics retain their native constructor state;
Python receive timings must not be substituted for those fields. See
outputs/audit/ping-quic-statistics-evidence.md for initialization/write paths.
Integrity status comes from this session's native producer observations. No
observations come from disk or another connection.
"""
import time

from ping_responder import ReplicationPingStats, TelemetryUnavailable
from session_integrity import SessionIntegrityState


class SessionPingTelemetry:
    def __init__(self, *, clock_ns=None, transport=None, capabilities=None):
        if transport not in (None, 'quic_without_raknet'):
            raise ValueError('unsupported ping telemetry transport')
        if transport == 'quic_without_raknet' and (
                type(capabilities) is not int or capabilities not in (0, 0x100000)):
            raise ValueError('unsupported QUIC replication capabilities')
        if transport is None and capabilities is not None:
            raise ValueError('capabilities require an identified transport')
        self.clock_ns = clock_ns or time.monotonic_ns
        self.transport = transport
        self.integrity = SessionIntegrityState()
        # 104db2fec -> 104e0641c/104e05bd0 -> 104cc09a4 initializes
        # +5964; 104e06204 clears +9736/+9740/+9744. The QUIC packet
        # representation has neither a legacy peer nor raw RakNet metadata,
        # so the legacy writers are inactive. This is transport-specific
        # constructor state, NOT a missing-measurement fallback.
        self._legacy_stats = (ReplicationPingStats(0, (0, 0))
                              if transport == 'quic_without_raknet' else None)
        self._unsupported_packet_family = False
        self.replication_packets = 0
        self.ping_packets = 0
        self.timed_packets = 0
        self.last_timing = None
        self.max_receive_to_frame_ms = None
        self.max_frame_to_dispatch_ms = None

    def observe_dispatch(self, application, channel, payload, *, received_ns, frame_ready_ns):
        if application == 4 and payload[:1] == b'\xe3':
            # The new packet family requires capabilities this implementation
            # does not negotiate. Do not retain the old profile if it appears.
            self._unsupported_packet_family = True
        if application != 4 or payload[:1] != b'\x83':
            return
        self.replication_packets += 1
        self.ping_packets += int(channel == 3)
        dispatch_ns = self.clock_ns()
        # Missing ingress observations remain missing (e.g. a direct inert
        # test call). Never assign a new ingress timestamp at dispatch time.
        if received_ns is None:
            return
        if any(type(t) is not int or t < 0 for t in (received_ns, frame_ready_ns, dispatch_ns)):
            raise ValueError('receive pipeline clocks must be nonnegative nanoseconds')
        if not received_ns <= frame_ready_ns <= dispatch_ns:
            raise ValueError('receive pipeline clocks are out of order')
        receive_ms = (frame_ready_ns - received_ns) / 1_000_000
        dispatch_ms = (dispatch_ns - frame_ready_ns) / 1_000_000
        self.timed_packets += 1
        self.last_timing = {'receive_to_frame_ms': round(receive_ms, 6),
                            'frame_to_dispatch_ms': round(dispatch_ms, 6)}
        self.max_receive_to_frame_ms = max(self.max_receive_to_frame_ms or 0, receive_ms)
        self.max_frame_to_dispatch_ms = max(self.max_frame_to_dispatch_ms or 0, dispatch_ms)

    def replication_stats(self):
        if self._legacy_stats is not None and not self._unsupported_packet_family:
            return self._legacy_stats
        raise TelemetryUnavailable('Native replication statistics have no verified live source',
                                   code='native_replication_statistics_unavailable')

    def integrity_status(self):
        return self.integrity.status_word()

    def public_report(self):
        stats_available = self._legacy_stats is not None and not self._unsupported_packet_family
        integrity = self.integrity.public_report()
        integrity_available = integrity['wire_status_available']
        missing = [] if stats_available else ['replicator_5964_bcs_queue_time',
                                               'replicator_9736_9740_native_packet_timing']
        if not integrity_available:
            missing.append('datamodel_and_global_integrity_flags')
        return {
            'scope': 'this_native_session',
            'wire_telemetry_ready': stats_available and integrity_available,
            'missing_wire_fields': missing,
            'integrity_producers': integrity,
            'replication_statistics': {
                'available': stats_available,
                'transport': self.transport,
                'source': ('native_constructor_state_without_raknet'
                           if stats_available else None),
                'legacy_bcs_queue_time': self._legacy_stats.replication_word if stats_available else None,
                'legacy_packet_timing': (list(self._legacy_stats.replication_tail_words)
                                         if stats_available else None),
                'unsupported_packet_family_observed': self._unsupported_packet_family,
            },
            'local_receive_pipeline': {
                'replication_packets': self.replication_packets,
                'ping_packets': self.ping_packets,
                'timed_packets': self.timed_packets,
                'last': dict(self.last_timing) if self.last_timing is not None else None,
                'max_receive_to_frame_ms': (round(self.max_receive_to_frame_ms, 6)
                                           if self.max_receive_to_frame_ms is not None else None),
                'max_frame_to_dispatch_ms': (round(self.max_frame_to_dispatch_ms, 6)
                                            if self.max_frame_to_dispatch_ms is not None else None),
                'native_field_equivalence_verified': False,
            },
        }
