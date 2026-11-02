"""Rna unreliable datagrams (native header reader 0x1021cd338).

All four header fields use QUIC varints, not LEB128. Small outgoing movement
packets are unfragmented. Fragmented incoming data is explicitly identified.
"""
import struct

from game_wire import encode_varint
from wire_reader import Reader, WireError


def control_open(application, channel, wire_id):
    if not 0 <= application <= 8 or not 0 <= channel < 32 or not 0 < wire_id < 2**31:
        raise ValueError('Invalid unreliable channel')
    return b'\x02' + struct.pack('!BII', application, channel, wire_id)


def parse_control_open(payload):
    if len(payload) != 10 or payload[0] != 2:
        raise WireError('Invalid unreliable channel announcement')
    app, channel, wire_id = struct.unpack('!BII', payload[1:])
    control_open(app, channel, wire_id)
    return app, channel, wire_id


def encode_datagram(wire_id, sequence, payload):
    if not 0 < wire_id < 2**31 or not 0 <= sequence < 2**62 or len(payload) > 1100:
        raise ValueError('Unreliable packet exceeds supported bounds')
    return encode_varint(wire_id) + encode_varint(sequence) + b'\0' + payload


def parse_datagram(data):
    r = Reader(data)
    def field():
        head = r.u8()
        width = 1 << (head >> 6)
        return int.from_bytes(bytes((head & 63,)) + r.take(width-1), 'big')
    wire_id, sequence, total = field(), field(), field()
    offset = field() if total else 0
    if not 0 < wire_id < 2**31 or total > 65536 or offset + r.remaining > total and total:
        raise WireError('Invalid unreliable datagram bounds')
    return {'wire_id': wire_id, 'sequence': sequence, 'total': total,
            'offset': offset, 'complete': total == 0 or total == r.remaining,
            'payload': r.take(r.remaining)}
