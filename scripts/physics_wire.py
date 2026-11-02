"""Compact position/velocity formats recovered from the selected ARM64 build."""
import math
import struct


def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


def vector(v):
    if len(v) != 3 or not all(math.isfinite(x) and abs(x) < 1e9 for x in v):
        raise ValueError('A bounded finite three-component vector is required')
    return [f32(x) for x in v]


def quantize(v, exponent, scale):
    return [int(f32(f32(max(-1., min(1., f32(x / 2**exponent))) * scale)
                      + (0.5 if x >= 0 else -0.5))) for x in v]


def position(v):
    v = vector(v)
    exponent = max(0, min(31, math.frexp(max(abs(x) for x in v))[1]))
    bits = 10 if exponent <= 4 else 16 if exponent <= 10 else 21
    x, y, z = quantize(v, exponent, (1 << bits) - 1)
    header = exponent << 3 | (4 if x < 0 else 0) | (2 if y < 0 else 0) | (1 if z < 0 else 0)
    mask = (1 << bits) - 1
    x, y, z = x & mask, y & mask, z & mask
    if bits == 10:
        data = struct.pack('<I', x << 20 | y << 10 | z)
    elif bits == 16:
        data = struct.pack('<HHH', x, y, z)
    else:
        data = struct.pack('<II', (x << 11 | y >> 10) & 0xffffffff, z | (y & 1023) << 21)
    return bytes([header]) + data


def velocity(v):
    v = vector(v)
    maximum = max(abs(x) for x in v)
    if maximum < f32(0.001): return b'\0'
    exponent = max(0, min(14, math.frexp(maximum)[1]))
    x, y, z = quantize(v, exponent, 2047)
    header = (exponent << 4 | z & 15) + 16
    body = ((x << 20) | (y & 4095) << 8 | (z >> 4 & 255)) & 0xffffffff
    return bytes([header]) + struct.pack('<I', body)
