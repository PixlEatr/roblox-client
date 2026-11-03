"""Four-byte packed quaternion leaf used by the selected 0x104CBEF68 path.

This is a generic quaternion codec only. It does not encode positions, choose
joint order, or frame a replication packet. Component/table/bit layout follows
the literal 0x104CBEF68 ARM64 output and table 0x105EA7CA0.
"""
from __future__ import annotations

import math
import struct


_COMPONENTS = ((1, 2, 3), (0, 2, 3), (0, 1, 3), (0, 1, 2))
_RANGE = 0.7071067690849304  # binary32 0x3f3504f3 at 0x1059B7000
_SCALE = 511.0              # binary32 0x43ff8000


class Quaternion32Error(ValueError):
    """Invalid input to the bounded four-byte quaternion codec."""


def _f32(value: float) -> float:
    try:
        return struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error):
        raise Quaternion32Error('quaternion component is not float32-representable') from None


def _finite(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Quaternion32Error('quaternion requires four finite numbers')
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise Quaternion32Error('quaternion requires four finite numbers') from None
    if not math.isfinite(result):
        raise Quaternion32Error('quaternion requires four finite numbers')
    return result


def _normalize_float32(q):
    # Preserve the ARM64 NEON sum/reduction order documented in the helper:
    # (x*x + y*y) + (z*z + w*w), rounded to binary32 at each step.
    q = tuple(_f32(x) for x in q)
    squares = tuple(_f32(x*x) for x in q)
    first = _f32(squares[0] + squares[1])
    second = _f32(squares[2] + squares[3])
    norm2 = _f32(first + second)
    if not math.isfinite(norm2) or norm2 <= 0.0:
        raise Quaternion32Error('quaternion must have nonzero finite length')
    norm = _f32(math.sqrt(norm2))
    inv = _f32(1.0 / norm)
    return tuple(_f32(x*inv) for x in q)


def encode_quaternion32(quaternion) -> bytes:
    """Encode an (x, y, z, w) quaternion into the native four-byte form.

    The largest absolute component is omitted (first wins ties), the sign is
    canonicalized so that omitted component is nonnegative, and the other
    values are signed 10-bit integers using range 0.7071067690849304 and scale
    511. Quantization rounds halves away from zero, as shown by FCVTZS.
    """
    if not isinstance(quaternion, (tuple, list)) or len(quaternion) != 4:
        raise Quaternion32Error('quaternion requires four finite numbers')
    values = _normalize_float32(tuple(_finite(x) for x in quaternion))
    # Native helper first chooses the omitted component, then flips q if needed.
    omitted = max(range(4), key=lambda i: abs(values[i]))
    if values[omitted] < 0.0:
        values = tuple(_f32(-x) for x in values)
    a,b,c = (_quantize(values[i]) for i in _COMPONENTS[omitted])
    word = (omitted << 30) | ((a & 0x3ff) << 20) | ((b & 0x3ff) << 10) | (c & 0x3ff)
    return struct.pack('<I', word)


def _quantize(value: float) -> int:
    # Mirrors FDIV, FMUL, sign-selected +/-0.5, and FCVTZS.
    ratio = _f32(value / _RANGE)
    scaled = _f32(ratio * _SCALE)
    adjusted = _f32(scaled + (0.5 if value >= 0.0 else -0.5))
    return math.trunc(adjusted)


def decode_quaternion32(data: bytes, offset: int = 0) -> tuple[float, float, float, float]:
    """Decode four bytes to normalized (x, y, z, w) quaternion components."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise Quaternion32Error('packed quaternion must be bytes')
    if type(offset) is not int or offset < 0 or offset + 4 > len(data):
        raise Quaternion32Error('truncated packed quaternion')
    word = struct.unpack_from('<I', data, offset)[0]
    omitted = word >> 30
    packed = ((word >> 20) & 0x3ff, (word >> 10) & 0x3ff, word & 0x3ff)
    q = [0.0]*4
    for index, code in zip(_COMPONENTS[omitted], packed):
        signed = code - 0x400 if code & 0x200 else code
        q[index] = _f32(_f32(signed / _SCALE) * _RANGE)
    q[omitted] = _f32(math.sqrt(max(1.0 - sum(x*x for x in q), 0.0)))
    norm = math.sqrt(sum(x*x for x in q))
    if not math.isfinite(norm) or norm <= 0.0:
        raise Quaternion32Error('invalid packed quaternion')
    return tuple(_f32(x/norm) for x in q)
