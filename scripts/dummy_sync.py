"""Bounded normal DummyService time-sync wire codec and echo/RTT tracker.

104eed790 creates application 6. 104eefbfc sends type 3 plus a little-endian
monotonic-microsecond value and two zero words on unreliable channel 3.
The native callback arithmetic is available separately from echo tracking.
Its callback consumer and relationship to the physics clock remain unresolved;
these values are never applied to outgoing physics timestamps here.
"""
from dataclasses import dataclass
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


@dataclass(frozen=True)
class NativeTimingSample:
    local_receive_us: int
    half_interval_ms: int
    callback_offset_ms: int
    callback_extended_offset_ms: int | None


def _signed64(value):
    value &= (1 << 64)-1
    return value-(1 << 64) if value >= 1 << 63 else value


def _trunc_div(value, divisor):
    return -(abs(value)//divisor) if value < 0 else value//divisor


def response_timing(payload, local_receive_us, *, extended_timing=False):
    """Reproduce 104EEF158's callback arguments without choosing their consumer.

    Signed fixed-width subtraction and truncation toward zero match the native
    integer instructions. ``extended_timing`` is an explicit caller policy;
    payload length alone does not establish the native feature option.
    """
    if type(local_receive_us) is not int or not 0 <= local_receive_us < 1 << 64:
        raise ValueError('Receive timestamp must fit uint64')
    if type(extended_timing) is not bool:
        raise ValueError('Extended timing policy must be a boolean')
    echo = response_echo(payload)
    word9, word17 = struct.unpack_from('<QQ', payload, 9)
    half = _trunc_div(_signed64(local_receive_us-echo-word9), 2000)
    offset = _signed64(half+_trunc_div(_signed64(word17-local_receive_us), 1000))
    extra = None
    if extended_timing and len(payload) == 33:
        word25 = struct.unpack_from('<Q', payload, 25)[0]
        extra = _signed64(half+_trunc_div(_signed64(word25-local_receive_us), 1000))
    return NativeTimingSample(local_receive_us, half, offset, extra)


class TimeSyncProbe:
    def __init__(self):
        self.started_at = None
        self.next_request_at = None
        self.pending = {}
        self.request_count = 0
        self.rtt_ms = []
        self.native_timing_samples = []

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

    def receive(self, payload, now, *, local_receive_us=None, extended_timing=False):
        try:
            echo = response_echo(payload)
        except (ValueError, TypeError):
            return False
        sent_at = self.pending.get(echo)
        if sent_at is None or now < sent_at:
            return False
        sample = None
        if local_receive_us is not None:
            try:
                sample = response_timing(payload, local_receive_us,
                                         extended_timing=extended_timing)
            except (ValueError, TypeError, struct.error):
                return False
        del self.pending[echo]
        self.rtt_ms.append((now - sent_at) * 1000)
        if sample is not None:
            self.native_timing_samples.append(sample)
        return True

    def runtime_statistics(self):
        """Latest matched echo RTT for this probe's session, in milliseconds."""
        return {'network_rtt_ms': self.rtt_ms[-1]} if self.rtt_ms else {}

    def public_report(self):
        result = {'time_sync_requests_sent': self.request_count,
                'time_sync_echoed_responses': len(self.rtt_ms),
                'time_sync_measured_rtt_ms': [round(value, 3) for value in self.rtt_ms]}
        if self.native_timing_samples:
            result['time_sync_native_callback_samples'] = [
                {'half_interval_ms': sample.half_interval_ms,
                 'callback_offset_ms': sample.callback_offset_ms,
                 'callback_extended_offset_ms': sample.callback_extended_offset_ms}
                for sample in self.native_timing_samples]
            result['time_sync_physics_clock_adjustment_applied'] = False
        return result
