"""Small routing-header codec recovered from the copied 0.745 ARM64 client.

Evidence: 102239138 (serialize), 102239444/102239558 (parse),
10223ced4 (IPv4 endpoint), 10223d3b8 (issued-token TLV).
This module never creates routing credentials; callers supply issued tokens.
"""
import ipaddress
import struct


def client_header(token, backend_ip, backend_port, token_kind=1):
    if len(token) != 16 or token_kind not in (1, 2):
        raise ValueError("Expected a 16-byte issued routing token")
    if not 1 <= backend_port <= 65535:
        raise ValueError("Invalid backend port")
    address = ipaddress.IPv4Address(backend_ip).packed
    fields = bytes((1, 17, token_kind)) + token
    fields += bytes((2, 6)) + address + struct.pack("!H", backend_port)
    # The observed initial constructor uses kind=1, flags=0. Its DSR call
    # passes a zero mask, which does not change flags (102238ca0).
    return struct.pack("!BBH", 1, 0, 4 + len(fields)) + fields


def split_datagram(data):
    """Return (header metadata, payload); accept unwrapped QUIC as well.

    Raw TLV values are kept in memory for issued token rotation, never logged.
    Unknown TLVs are preserved so this diagnostic parser makes no unsupported
    assertion about the native parser's handling of newer field types.
    """
    if not data or data[0] not in (1, 3, 4):
        return None, data
    if len(data) < 4:
        raise ValueError("Truncated RUPP header")
    kind, flags, size = struct.unpack_from("!BBH", data)
    if size < 4 or size > len(data):
        raise ValueError("Invalid RUPP header length")
    fields = []
    offset = 4
    while offset < size:
        if offset + 2 > size:
            raise ValueError("Truncated RUPP TLV")
        field_type, length = data[offset:offset + 2]
        offset += 2
        if offset + length > size:
            raise ValueError("Truncated RUPP value")
        fields.append((field_type, data[offset:offset + length]))
        offset += length
    return {"kind": kind, "flags": flags, "size": size, "fields": fields}, data[size:]
