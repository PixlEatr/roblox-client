"""Verified legacy PingBack serialization, without fabricated client telemetry.

Field order: copied ARM64 0.745 writer 104d949c4, constructor 104d948b4.
The native status word includes DataModel anti-tamper state (10482ce08).
All client-maintained words must be supplied explicitly by their actual owner.
This module opens no connection and does not enable live replies.
"""
from dataclasses import dataclass
import struct

from replication_ping import ping_packet


def _unsigned(value, bits, name):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f'{name} must fit uint{bits}')
    return value


@dataclass(frozen=True)
class PingBackTelemetry:
    """Actual client observations; there are deliberately no default values.

    reply_words correspond to constructor +76/+80, writer-time clock, +84,
    and the two raw words at +88/+92. Keeping opaque values as words avoids
    asserting an unverified meaning or numeric conversion for them.
    """
    reply_words: tuple
    status_word: int
    response_word: int

    def __post_init__(self):
        if not isinstance(self.reply_words, tuple) or len(self.reply_words) != 6:
            raise ValueError('reply_words must be a tuple of six actual uint32 values')
        for i, value in enumerate(self.reply_words):
            _unsigned(value, 32, f'reply_words[{i}]')
        _unsigned(self.status_word, 32, 'status_word')
        _unsigned(self.response_word, 32, 'response_word')


def encode_ping_back(request_payload, telemetry):
    """Serialize one unextended request, requiring explicitly sourced telemetry.

    Native writer's timestamp-conditioned complement of the response word is
    applied exactly once to an immutable input. This is packet encoding, not
    an implementation of the native telemetry or integrity-reporting system.
    """
    if not isinstance(telemetry, PingBackTelemetry):
        raise TypeError('actual PingBackTelemetry is required')
    if not isinstance(request_payload, bytes) or len(request_payload) > 4096:
        raise ValueError('request must be bounded packet bytes')
    requests = ping_packet(request_payload)
    if len(requests) != 1:
        raise ValueError('exactly one ping request is supported')
    return encode_ping_back_item(requests[0], telemetry)


def encode_ping_back_item(request, telemetry):
    """Encode an item already validated by read_ping, including batched items."""
    if not isinstance(telemetry, PingBackTelemetry):
        raise TypeError('actual PingBackTelemetry is required')
    if not isinstance(request, dict):
        raise TypeError('a parsed ping request is required')
    if request['kind'] != 5 or request['flags'] != 0:
        raise ValueError('only unextended kind-5 requests are supported')
    timestamp = _unsigned(request['timestamp'], 64, 'timestamp')
    first_word = _unsigned(request['first_word'], 32, 'first_word')
    if type(request['unreliable']) is not bool:
        raise ValueError('unreliable must be a boolean')
    response = telemetry.response_word
    if timestamp & 0x20:
        response ^= 0xffffffff
    return (b'\x83\x06\x01'
            + struct.pack('<Q9I', timestamp, first_word,
                          *telemetry.reply_words, telemetry.status_word, response)
            + bytes((request['unreliable'], 0)))
