"""Encode the capability mask. No account data needed."""
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
