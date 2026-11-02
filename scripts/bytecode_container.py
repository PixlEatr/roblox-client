"""Unpack RSB1 and fix opcode headers. This alone does not authenticate code."""
from functools import lru_cache
from pathlib import Path
import struct
import sys

import zstandard

from login_packets import ticket_hash


MAX_BYTES = 128 * 1024


def unpack_container(blob):
    if not isinstance(blob, bytes) or not 8 < len(blob) <= MAX_BYTES:
        raise ValueError('Invalid bytecode-container size')
    key = bytes(((blob[i] ^ b'RSB1'[i]) - i * 41) & 255 for i in range(4))
    raw = bytes(value ^ ((key[i % 4] + i * 41) & 255)
                for i, value in enumerate(blob))
    if raw[:4] != b'RSB1' or ticket_hash(raw, 42) != int.from_bytes(key, 'little'):
        raise ValueError('Bytecode-container checksum mismatch')
    size = struct.unpack_from('<I', raw, 4)[0]
    # Zero means the bytes are already uncompressed.
    # The next layer still needs to check the Luau format.
    if size == 0:
        return raw[8:]
    if size > MAX_BYTES:
        raise ValueError('Bytecode-container expansion exceeds limit')
    try:
        declared = zstandard.frame_content_size(raw[8:])
        if declared not in (zstandard.CONTENTSIZE_UNKNOWN, size):
            raise ValueError('Bytecode frame size differs from container size')
        result = zstandard.ZstdDecompressor().decompress(
            raw[8:], max_output_size=size, allow_extra_data=False)
    except zstandard.ZstdError as error:
        raise ValueError('Invalid compressed bytecode frame') from error
    if len(result) != size:
        raise ValueError('Bytecode-container expansion size mismatch')
    return result


@lru_cache(maxsize=1)
def _inspector():
    path = Path(__file__).resolve().parent
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    import inspect_bytecode
    return inspect_bytecode


def standardize_opcodes(data, encoding='auto'):
    """Convert instruction-header opcode bytes, preserving all other bytes.

    Returns (converted_bytes, structural_metadata). The existing inspector is
    not a full execution-safety or signature verifier; conversion does not
    authorize executing an input or replying to an authentication check.
    """
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise ValueError('Invalid bytecode size')
    inspector = _inspector()
    module, summary = inspector.inspect(data, encoding)
    output = bytearray(data)
    for proto in module['prototypes']:
        for instruction in proto['instructions']:
            position = proto['code_offset'] + instruction['pc'] * 4
            output[position] = instruction['opcode']
    metadata = {key: summary[key] for key in
                ('bytecode_version', 'type_version', 'prototype_count',
                 'instruction_count', 'opcode_decode_multiplier', 'trailer_bytes',
                 'prototype_extension_bytes')}
    metadata['authentication_validated'] = False
    metadata['execution_safety_verified'] = False
    return bytes(output), metadata
