"""Reliable channel prefix recovered at 102154ed8 / 102155004.

The prefix is length 6, OpenReliableChannel kind 1, application uint8,
channel ID uint32 big-endian. Game transport creation uses application 4.
"""
import struct


def reliable_header(application, channel):
    if not 0 <= application <= 8 or not 0 <= channel <= 0xffffffff:
        raise ValueError("Invalid channel header field")
    return struct.pack("!BBBI", 6, 1, application, channel)


def parse_reliable_header(data):
    if len(data) < 7:
        raise ValueError("Incomplete reliable channel header")
    size, kind, application, channel = struct.unpack_from("!BBBI", data)
    if size != 6 or kind != 1 or application > 8:
        raise ValueError("Unrecognized reliable channel header")
    return application, channel
