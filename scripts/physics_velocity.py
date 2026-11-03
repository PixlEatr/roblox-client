"""Semantic codecs for the selected build's five-byte and three-byte velocities.

Both formats use a one-byte zero sentinel. Nonzero values retain their exact
raw bytes and decoded exponent/quantized-component metadata.
"""
from dataclasses import dataclass
import math
import struct
from typing import Union


@dataclass(frozen=True)
class DecodedVelocity:
    vector: tuple[float, float, float]
    exponent: int | None
    quantized: tuple[int, int, int]
    packed: bytes
    zero_sentinel: bool

    @property
    def packed_hex(self) -> str:
        return self.packed.hex()


def _signed12(value: int) -> int:
    return value - 0x1000 if value & 0x800 else value


def encode_native_short_velocity(vector) -> bytes:
    """Encode the native three-byte short-vector branch (0x104CBF4AC).

    This is the three-byte native variant. :func:`physics_wire.velocity`
    encodes the distinct five-byte variant; D76C selects which transform mode
    to use at runtime.
    """
    if not isinstance(vector, (tuple, list)) or len(vector) != 3:
        raise ValueError('native short velocity requires three components')
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) >= 1e9
           for x in vector):
        raise ValueError('native short velocity components must be bounded finite numbers')
    values = tuple(_f32(float(x)) for x in vector)
    maximum = max(abs(x) for x in values)
    if maximum < _f32(0.001):
        return b'\0'

    exponent = math.frexp(maximum)[1]
    exponent = max(-16, min(15, exponent))
    scale = _f32(math.ldexp(1.0, exponent))
    quantized = []
    for value in values:
        normalized = _f32(value / scale)
        normalized = max(-1.0, min(1.0, normalized))
        scaled = _f32(normalized * _f32(31.0))
        biased = _f32(scaled + (_f32(0.5) if value >= 0.0 else _f32(-0.5)))
        quantized.append(math.trunc(biased) & 0x3f)

    x, y, z = quantized
    return bytes((
        ((x >> 4) | (exponent << 2) | 0x80) & 0xff,
        ((x << 4) | (y >> 2)) & 0xff,
        ((z & 0x3f) | (y << 6)) & 0xff,
    ))


def _f32(value: float) -> float:
    return struct.unpack('<f', struct.pack('<f', value))[0]


def _signed6(value: int) -> int:
    value &= 0x3f
    return value - 0x40 if value & 0x20 else value


def decode_native_short_velocity(data: Union[bytes, bytearray, memoryview]) -> DecodedVelocity:
    """Decode the native three-byte short-vector branch (0x104CBF634).

    A zero vector is the one-byte zero sentinel. Nonzero vectors contain an
    exponent/x prefix byte followed by two packed component bytes.
    """
    raw = bytes(data)
    if raw == b'\0':
        return DecodedVelocity((0.0, 0.0, 0.0), None, (0, 0, 0), raw, True)
    if len(raw) != 3:
        raise ValueError('native short velocity must be the zero sentinel or exactly three bytes')
    header, middle, tail = raw
    if header == 0:
        raise ValueError('native short velocity zero header must use the one-byte sentinel')
    exponent_bits = (header >> 2) & 0x1f
    exponent = exponent_bits - 0x20 if exponent_bits & 0x10 else exponent_bits
    x = _signed6(((header & 0x03) << 4) | (middle >> 4))
    y = _signed6(((middle & 0x0f) << 2) | (tail >> 6))
    z = _signed6(tail & 0x3f)
    scale = _f32(math.ldexp(1.0, exponent))
    quantized = (x, y, z)
    vector = tuple(_f32(_f32(float(component) / _f32(31.0)) * scale)
                   for component in quantized)
    return DecodedVelocity(vector, exponent, quantized, raw, False)


def decode(data: Union[bytes, bytearray, memoryview]) -> DecodedVelocity:
    """Decode exactly one compact velocity; malformed sizes/fields raise ValueError."""
    raw = bytes(data)
    if raw == b'\x00':
        return DecodedVelocity((0.0, 0.0, 0.0), None, (0, 0, 0), raw, True)
    if len(raw) != 5:
        raise ValueError('compact velocity must be the one-byte zero sentinel or exactly five bytes')

    header = raw[0]
    # Header high nibble is exponent+1 (the all-zero header is the sentinel);
    # low nibble carries the low four bits of signed z.
    exponent = (header >> 4) - 1
    if not 0 <= exponent <= 14:
        raise ValueError('compact velocity exponent is outside the supported range')
    payload = struct.unpack('<I', raw[1:])[0]
    x = _signed12(payload >> 20)
    y = _signed12((payload >> 8) & 0xFFF)
    z = _signed12(((payload & 0xFF) << 4) | (header & 0xF))
    q = (x, y, z)
    # The producer clamps to [-1,1] and uses scale 2047, so -2048 is not
    # representable as a canonical quantized component.
    if any(component == -2048 for component in q):
        raise ValueError('compact velocity contains a non-canonical -2048 component')
    scale = math.ldexp(1.0, exponent) / 2047.0
    return DecodedVelocity(tuple(component * scale for component in q), exponent, q, raw, False)
