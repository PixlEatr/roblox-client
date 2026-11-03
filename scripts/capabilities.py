"""Offline reconstruction of the observed RbxTransport capabilities text codec.

Source build: Roblox 0.745.0.7451137 ARM64, SHA256 b6b21841...297b870.
Encode: 0x10222690c -> 0x102c60c2c; decode: 0x1022269ac -> 0x102c609dc.
Request path construction: 0x1021324f8. No meaning is assigned to individual bits.
This module makes no network requests and does not read account credentials.
"""
import re


def encode_capabilities(value):
    if type(value) is not int or not 0 <= value < 1 << 64:
        raise ValueError("Capabilities must be an unsigned 64-bit integer")
    return value.to_bytes(8, "big").hex()


def decode_capabilities(text):
    if not isinstance(text, str) or re.fullmatch(r"[0-9a-fA-F]{16}", text) is None:
        raise ValueError("Capabilities must contain exactly 16 hexadecimal characters")
    return int.from_bytes(bytes.fromhex(text), "big")


def capabilities_path(value):
    return "/wt?X-Rbx-Capabilities=" + encode_capabilities(value)
