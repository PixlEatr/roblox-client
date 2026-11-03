"""Offline codec for the statically recovered 18-byte movement header.

The timestamp is an unsigned 64-bit millisecond-scale value from the native
physics clock conversion. Its epoch/origin is not a Unix time and is not fully
identified. Callers must select sequence/tag ordering from external evidence;
this module deliberately has no default layout.
"""
import struct

TAG = 0x1B
SEQUENCE_TAG = 0x85
TIMESTAMP_SIZE = 8
HEADER_SIZE = 18
LAYOUT_SEQUENCE_THEN_TAG = "sequence_then_tag"
LAYOUT_TAG_THEN_SEQUENCE = "tag_then_sequence"
_LAYOUTS = (LAYOUT_SEQUENCE_THEN_TAG, LAYOUT_TAG_THEN_SEQUENCE)
_U64_MAX = (1 << 64) - 1


def _check_layout(layout):
    if layout not in _LAYOUTS:
        raise ValueError("An explicitly supported physics header layout is required")


def encode_header(timestamp_ms, sequence, *, layout):
    """Encode the native header with timestamp and sequence as uint64 LE."""
    _check_layout(layout)
    if type(timestamp_ms) is not int or not 0 <= timestamp_ms <= _U64_MAX:
        raise ValueError("Timestamp must fit an unsigned 64-bit millisecond value")
    if type(sequence) is not int or not 0 <= sequence <= _U64_MAX:
        raise ValueError("Sequence must fit an unsigned 64-bit integer")
    timestamp_bytes = struct.pack("<Q", timestamp_ms)
    sequence_bytes = struct.pack("<Q", sequence)
    if layout == LAYOUT_SEQUENCE_THEN_TAG:
        tail = sequence_bytes + bytes((SEQUENCE_TAG,))
    else:
        tail = bytes((SEQUENCE_TAG,)) + sequence_bytes
    return bytes((TAG,)) + timestamp_bytes + tail


def decode_header(data, *, layout):
    """Decode exactly one header and expose its unsigned millisecond value."""
    _check_layout(layout)
    if not isinstance(data, bytes) or len(data) != HEADER_SIZE:
        raise ValueError("Physics header must be exactly 18 bytes")
    if data[0] != TAG:
        raise ValueError("Invalid physics header tag")
    timestamp_ms = struct.unpack("<Q", data[1:9])[0]
    tail = data[9:]
    if layout == LAYOUT_SEQUENCE_THEN_TAG:
        if tail[8] != SEQUENCE_TAG:
            raise ValueError("Invalid sequence/tag marker for selected layout")
        sequence = struct.unpack("<Q", tail[:8])[0]
    else:
        if tail[0] != SEQUENCE_TAG:
            raise ValueError("Invalid sequence/tag marker for selected layout")
        sequence = struct.unpack("<Q", tail[1:])[0]
    return {"timestamp_ms": timestamp_ms, "sequence": sequence, "layout": layout}
