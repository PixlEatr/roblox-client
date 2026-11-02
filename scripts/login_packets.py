"""Normal ticket packet layout recovered from client build 0.745.0.7451137.

The account ticket and SessionId are issued by Roblox and passed through. The
other fields here are public build identifiers and serialization checksums;
this module does not generate credentials or solve server challenges.
"""
import codecs
import os
import struct

from game_wire import encode_unsigned, encode_string

MASK32 = 0xFFFFFFFF
BUILD_SEED = 1966058251


def rol32(value, count):
    value &= MASK32
    return ((value << count) | (value >> (32 - count))) & MASK32


def ror32(value, count):
    return rol32(value, 32 - count)


def ticket_hash(data, seed=1):
    """XXH32, identified and cross-checked against native 102ed4f00."""
    p1, p2, p3, p4, p5 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D, 0x27D4EB2F, 0x165667B1
    offset = 0
    if len(data) >= 16:
        lanes = [(seed + p1 + p2) & MASK32, (seed + p2) & MASK32, seed & MASK32, (seed - p1) & MASK32]
        while offset <= len(data) - 16:
            for i, word in enumerate(struct.unpack_from("<4I", data, offset)):
                lanes[i] = (rol32(lanes[i] + word * p2, 13) * p1) & MASK32
            offset += 16
        value = sum(rol32(v, n) for v, n in zip(lanes, (1, 7, 12, 18))) & MASK32
    else:
        value = (seed + p5) & MASK32
    value = (value + len(data)) & MASK32
    while offset <= len(data) - 4:
        word = struct.unpack_from("<I", data, offset)[0]
        value = (rol32(value + word * p3, 17) * p4) & MASK32
        offset += 4
    for byte in data[offset:]:
        value = (rol32(value + byte * p5, 11) * p1) & MASK32
    value = ((value ^ (value >> 15)) * p2) & MASK32
    value = ((value ^ (value >> 13)) * p3) & MASK32
    return value ^ (value >> 16)


def ticket_checksum(value):
    """Public client checksum permutation, native 104c901b8 / 104f0b358."""
    seed, constant = BUILD_SEED, 0x557BB5D7
    flags = (seed * 0x0729BBAA) & 0xFFFF
    value = ror32(value + constant, 25 if flags & 2 else 7)
    value = (value - (seed if flags & 4 else constant)) & MASK32
    factor = seed if flags & 8 else constant
    value = ror32(value * factor, 19 if flags & 0x10 else 13)
    left = seed if flags & 0x20 else constant
    right = seed if flags & 0x40 else constant
    value = right ^ ((left - value) & MASK32)
    value = ror32(value, 15 if flags & 0x80 else 17)
    value = (value + (seed if flags & 0x100 else -seed) + (constant if flags & 0x200 else -constant)) & MASK32
    value = ror32(value, 9 if flags & 0x400 else 23)
    if flags & 0x800:
        value = -value
    value = (seed + value) & MASK32
    if flags & 0x1000:
        value = -value
    value = ror32(value + constant, 3 if flags & 0x2000 else 29)
    value ^= seed if flags & 0x4000 else constant
    return (value if flags & 0x8000 else -value) & MASK32


def encode_signed(value):
    if not -(1 << 63) <= value < 1 << 63:
        raise ValueError("Signed network integer is outside int64")
    return encode_unsigned((value << 1) ^ (value >> 63))


def build_identifier():
    # InitFunc_26617 (104f0c674) + ROT13 102c62d14, then 104f0b7dc.
    prefix = codecs.decode("2p2q778rnqo4263sp76os47q2s3s6717ro2nsr41", "rot_13")
    words = (0x85CB1F5E, 0xBF907B98, 0x8AB1A79E, 0x6133538C)
    suffix = "".join(format((word >> shift) & (0xDF if i == 0 else 0xFF), "02x")
                     for shift in range(0, 32, 8) for i, word in enumerate(words))
    return prefix + "!" + suffix + "."


def ticket_packet(join):
    """Packet 138, native 104c8fc5c, for RbxTransport (no RakNet encryption)."""
    ticket = join["ClientTicket"]
    checksum = ticket_checksum(ticket_hash(ticket.encode("utf-8"), 1))
    return (b"\x8a" + encode_signed(join["UserId"]) + encode_string(ticket)
            + struct.pack("<I", 36) + encode_string(build_identifier())
            + encode_string("OSX") + encode_string("?")
            + encode_unsigned(checksum) + encode_unsigned((checksum - 195948557) & MASK32)
            # InitFunc_26617 initializes the optional platform string empty.
            # 104f0bcdc transforms it in place, leaving an empty string empty.
            + encode_string("") + encode_string(join["SessionId"])
            + struct.pack("<I", 0xC001CAFE))


def verification_packet(nonce=None):
    """Packet 146, native 104c92a80. Its exact semantic name is unconfirmed."""
    nonce = int.from_bytes(os.urandom(4), "little") if nonce is None else nonce
    if not 0 <= nonce <= MASK32:
        raise ValueError("Verification nonce is outside uint32")
    constant = (0x1F5E0000 | (0x8AB1A79E & 0xFFFF)) ^ 0x11496016
    value = (nonce << 32) | (nonce ^ constant)
    if value >= 1 << 63:
        value -= 1 << 64
    return b"\x92" + encode_signed(value)


def server_id_packet():
    # 104c926e8 has a normal non-Studio path that emits an empty server ID.
    return b"\x8f" + encode_string("")
