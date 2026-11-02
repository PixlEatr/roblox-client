"""Bounded normal DummyService time-sync wire codec and echo/RTT tracker.

104eed790 creates application 6. 104eefbfc sends type 3 plus a little-endian
monotonic-microsecond value and two zero words on unreliable channel 3.
Other response words are deliberately opaque; this module estimates no clock
offset and implements no authentication or security state.
"""
import struct

APPLICATION = 6
REQUEST_CHANNEL = 3
MAX_REQUESTS = 5
INTERVAL_SECONDS = 1.0


def request_packet(monotonic_us):
    if type(monotonic_us) is not int or not 0 <= monotonic_us < 1 << 64:
        raise ValueError('Monotonic timestamp must fit uint64')
    return b'\3' + struct.pack('<QQQ', monotonic_us, 0, 0)


def response_echo(payload):
    """Read only the request echo; leave all other numeric words unclassified."""
    if len(payload) not in (25, 33) or payload[0] != 3:
        raise ValueError('Unrecognized DummyService time-sync response')
    return struct.unpack_from('<Q', payload, 1)[0]


class TimeSyncProbe:
    def __init__(self):
        self.started_at = None
        self.next_request_at = None
        self.pending = {}
        self.request_count = 0
        self.rtt_ms = []

    def start(self, now):
        if self.started_at is not None:
            raise ValueError('Time-sync probe already started')
        self.started_at = now
        self.next_request_at = now

    def due(self, now, monotonic_us):
        if self.next_request_at is None or now < self.next_request_at or self.request_count >= MAX_REQUESTS:
            return None
        payload = request_packet(monotonic_us)
        if monotonic_us in self.pending:
            raise ValueError('Duplicate request timestamp')
        self.pending[monotonic_us] = now
        self.request_count += 1
        # Schedule from actual emission time: delayed ticks never produce bursts.
        self.next_request_at = now + INTERVAL_SECONDS
        return payload

    def receive(self, payload, now):
        try:
            echo = response_echo(payload)
        except (ValueError, TypeError):
            return False
        sent_at = self.pending.get(echo)
        if sent_at is None or now < sent_at:
            return False
        del self.pending[echo]
        self.rtt_ms.append((now - sent_at) * 1000)
        return True

    def public_report(self):
        return {'time_sync_requests_sent': self.request_count,
                'time_sync_echoed_responses': len(self.rtt_ms),
                'time_sync_measured_rtt_ms': [round(value, 3) for value in self.rtt_ms]}
