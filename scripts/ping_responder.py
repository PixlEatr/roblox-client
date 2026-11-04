"""Bounded ping receive/queue/send path with explicit telemetry dependencies.

Timing and routing follow native 104dd1434 / 104d948b4 / 104d949c4.
Callers must provide actual replication statistics and client status. No zero
defaults, server-word substitution or recorded-session telemetry are used.
"""
from collections import deque
from dataclasses import dataclass
import time

from ping_back import PingBackTelemetry, _unsigned, encode_ping_back_item
from replication_ping import ping_packet
from session_diagnostics import current


class TelemetryUnavailable(RuntimeError):
    """An actual observation is unavailable; no reply may be constructed."""

    def __init__(self, message='Session telemetry is unavailable', *, code='unavailable'):
        super().__init__(message)
        # Publish only known codes, never provider exception text or raw state.
        self.code = code if code in ('unavailable', 'native_replication_statistics_unavailable',
                                     'native_integrity_state_unavailable') else 'unavailable'


@dataclass(frozen=True)
class ReplicationPingStats:
    replication_word: int
    replication_tail_words: tuple

    def __post_init__(self):
        _unsigned(self.replication_word, 32, 'replication_word')
        if (not isinstance(self.replication_tail_words, tuple)
                or len(self.replication_tail_words) != 2):
            raise ValueError('replication_tail_words must contain two actual uint32 words')
        for word in self.replication_tail_words:
            _unsigned(word, 32, 'replication_tail_words component')


@dataclass(frozen=True)
class PendingPing:
    request: dict
    received_ms: int
    queued_ms: int
    stats: ReplicationPingStats


class PingResponder:
    def __init__(self, *, stats_provider, status_provider, clock_ms=None, max_pending=128):
        if not callable(stats_provider) or not callable(status_provider):
            raise TypeError('actual stats and status providers are required')
        if type(max_pending) is not int or not 1 <= max_pending <= 1024:
            raise ValueError('max_pending must be 1..1024')
        if clock_ms is not None and not callable(clock_ms):
            raise TypeError('clock_ms must be callable')
        self.stats_provider = stats_provider
        self.status_provider = status_provider
        self.clock_ms = clock_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self.pending = deque()
        self.max_pending = max_pending
        self.requests_received = self.replies_received = self.replies_sent = 0
        self.unsupported_requests = self.queue_overflows = self.telemetry_blocks = 0
        self.telemetry_blocks_by_provider = {'statistics': 0, 'integrity': 0}
        self.last_telemetry_block = None

    def _telemetry_blocked(self, provider, error):
        self.telemetry_blocks += 1
        self.telemetry_blocks_by_provider[provider] += 1
        self.last_telemetry_block = {'provider': provider, 'reason': error.code}

    def _now(self):
        # Native clock words are uint32 and naturally wrap. Their local epoch
        # need not match the server's; the opaque server timestamp is echoed.
        return _unsigned(self.clock_ms(), 64, 'monotonic milliseconds') & 0xffffffff

    def receive(self, payload):
        received_ms = self._now()
        if not isinstance(payload, bytes) or len(payload) > 4096:
            raise ValueError('ping packet must be bounded bytes')
        # Validate the entire frame before queuing any reply.
        items = ping_packet(payload)
        return self.receive_items(items, received_ms=received_ms)

    def receive_items(self, items, *, received_ms=None):
        """Accept ping records from a fully validated mixed replication packet."""
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError('parsed ping batch must contain at most 64 items')
        if received_ms is None:
            received_ms = self._now()
        accepted = 0
        for item in items:
            if item['kind'] == 6 or item['flags'] & 1:
                self.replies_received += 1
                continue  # Never answer an answer.
            self.requests_received += 1
            if item['flags'] != 0:
                self.unsupported_requests += 1
                continue
            if len(self.pending) >= self.max_pending:
                self.queue_overflows += 1
                continue
            try:
                stats = self.stats_provider()
            except TelemetryUnavailable as error:
                self._telemetry_blocked('statistics', error)
                continue
            if not isinstance(stats, ReplicationPingStats):
                raise TypeError('stats provider must return ReplicationPingStats')
            self.pending.append(PendingPing(item, received_ms, self._now(), stats))
            accepted += 1
        return accepted

    def flush(self, client, *, limit=64):
        """Send bounded queued replies using NativeSession's normal transport."""
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('flush limit must be 1..64')
        sent = processed = 0
        while self.pending and processed < limit:
            processed += 1
            pending = self.pending[0]
            try:
                status = self.status_provider()
            except TelemetryUnavailable as error:
                self._telemetry_blocked('integrity', error)
                # This request cannot produce a valid reply. Do not retain stale
                # queue entries indefinitely or substitute a clean status word.
                self.pending.popleft()
                continue
            _unsigned(status, 32, 'actual client status word')
            words = (pending.received_ms, pending.queued_ms, self._now(),
                     pending.stats.replication_word, *pending.stats.replication_tail_words)
            # Native receive 104dd1434 supplies literal zero as the response-word
            # constructor argument. The writer performs the conditional inversion.
            telemetry = PingBackTelemetry(words, status, 0)
            payload = encode_ping_back_item(pending.request, telemetry)
            sender = client.send_unreliable if pending.request['unreliable'] else client.send
            sender(3, payload, application=4)
            self.pending.popleft()
            self.replies_sent += 1
            sent += 1
        return sent

    def public_report(self):
        return {'application_ping_requests_received': self.requests_received,
                'application_ping_responses_received': self.replies_received,
                'application_ping_replies_sent': self.replies_sent,
                'application_ping_replies_pending': len(self.pending),
                'application_ping_requests_unsupported': self.unsupported_requests,
                'application_ping_queue_overflows': self.queue_overflows,
                'application_ping_telemetry_blocks': self.telemetry_blocks,
                'application_ping_telemetry_blocks_by_provider': dict(self.telemetry_blocks_by_provider),
                'application_ping_last_telemetry_block': (dict(self.last_telemetry_block)
                                                        if self.last_telemetry_block else None)}


class SessionPingHandler:
    """Connect the responder to live packet callbacks without inventing state.

    Providers belong to this session's replication/integrity implementations.
    Missing providers are observable and never imply a zero-valued report.
    Supplying these callbacks enables replies through the normal transport;
    it does not establish that their measurements match the native client.
    """

    def __init__(self, *, stats_provider=None, status_provider=None, clock_ms=None,
                 telemetry_report_provider=None):
        for provider in (stats_provider, status_provider):
            if provider is not None and not callable(provider):
                raise TypeError('session telemetry providers must be callable')
        self.missing = tuple(name for name, provider in (
            ('replication_statistics', stats_provider),
            ('integrity_status', status_provider)) if provider is None)
        if telemetry_report_provider is not None and not callable(telemetry_report_provider):
            raise TypeError('telemetry report provider must be callable')
        self.telemetry_report_provider = telemetry_report_provider

        def unavailable():
            raise TelemetryUnavailable('session telemetry provider unavailable')

        self.responder = PingResponder(
            stats_provider=stats_provider if stats_provider is not None else unavailable,
            status_provider=status_provider if status_provider is not None else unavailable,
            clock_ms=clock_ms)

    def update_report(self, client):
        client.report.update(self.responder.public_report())
        client.report['application_pings_received'] = (
            self.responder.requests_received + self.responder.replies_received)
        client.report['application_ping_reply_transport_implemented'] = True
        client.report['application_ping_provider_callbacks_configured'] = not self.missing
        ready = not self.missing
        if self.telemetry_report_provider is not None:
            telemetry = self.telemetry_report_provider()
            client.report['application_ping_session_telemetry'] = telemetry
            ready = ready and telemetry['wire_telemetry_ready'] is True
        client.report['application_ping_replies_implemented'] = ready
        client.report['application_ping_missing_providers'] = list(self.missing)
        state = self.responder.public_report()
        if state != getattr(self, '_last_diagnostics', None):
            current().emit('game.ping_state', **state)
            self._last_diagnostics = state

    def receive(self, client, payload):
        try:
            self.responder.receive(payload)
            sent = self.responder.flush(client)
        finally:
            self.update_report(client)
        return sent

    def flush(self, client):
        try:
            sent = self.responder.flush(client)
        finally:
            self.update_report(client)
        return sent

    def receive_items(self, client, items):
        """Dispatch only after World.packet has validated its whole payload."""
        try:
            self.responder.receive_items(items)
            sent = self.responder.flush(client)
        finally:
            self.update_report(client)
        return sent
