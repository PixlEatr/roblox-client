"""Check the original V1 signature. This only verifies; it cannot sign anything."""
import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
from secrets import compare_digest
import stat

from blake3 import blake3

ROOT = Path(__file__).resolve().parent.parent
SOURCE_BINARY_SHA256 = 'b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870'
PUBLIC_TABLE_SHA256 = '0b6cdf37e5817b29638fe705319618df725ffd2aafc0491d768ab6f162f42c3c'
COEFFICIENT_COUNT = 8257
COEFFICIENT_BYTES = 12
MAX_BYTECODE = 16 * 1024 * 1024
TRAILER_BYTES = 24
MIN_SIGNED_BYTES = 0x118


class SignatureError(ValueError):
    """Sanitized verification failure; never contains input or signature bytes."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@lru_cache(maxsize=1)
def _public_coefficients():
    path = ROOT / 'inputs/bytecode-signature-v1-public.bin'
    try:
        raw = path.read_bytes()
        provenance = json.loads(path.with_suffix('.json').read_text())
    except (OSError, ValueError):
        raise SignatureError('public-trust-asset-unavailable') from None
    if (len(raw) != COEFFICIENT_COUNT * COEFFICIENT_BYTES
            or hashlib.sha256(raw).hexdigest() != PUBLIC_TABLE_SHA256
            or provenance.get('table_sha256') != PUBLIC_TABLE_SHA256
            or provenance.get('source_binary_sha256') != SOURCE_BINARY_SHA256):
        raise SignatureError('public-trust-provenance-mismatch')
    # Native 1013fadb4 pads each of these 96-bit coefficients to 128 bits.
    # The high 32 bits stay zero and are outside the 12-byte comparison.
    return tuple(int.from_bytes(raw[pos:pos + 12], 'little')
                 for pos in range(0, len(raw), 12))


def _evaluate_public_map(signature, coefficients):
    """Forward public verification map; no inverse/signing operation exists."""
    if len(signature) != 16 or len(coefficients) != COEFFICIENT_COUNT:
        raise SignatureError('invalid-public-map-input')
    selected = [byte_index * 8 + bit for byte_index, byte in enumerate(signature)
                for bit in range(8) if byte & (0x80 >> bit)]
    result = coefficients[0]
    for bit in selected:
        result ^= coefficients[1 + bit]
    for position, left in enumerate(selected):
        # Native 1013fac7c rows start at 129,256,382,... and enumerate
        # unordered pairs of the 128 MSB-first signature bits.
        row = 129 + 127 * left - left * (left - 1) // 2
        for right in selected[position + 1:]:
            result ^= coefficients[row + right - left - 1]
    return result.to_bytes(12, 'little')


def verify_signed_bytecode(raw_original_bytes):
    """Return sanitized success metadata or raise SignatureError, failing closed.

    This always performs V1 cryptographic verification; native runtime feature
    flags that can skip verification are deliberately not treated as success.
    Legacy/unknown signature versions are unsupported and rejected. The native
    configurable minimum-body policy ranges from 256 to 1024 bytes; this checks
    its unconditional 280-byte outer minimum and the genuine signature itself.
    """
    if not isinstance(raw_original_bytes, (bytes, bytearray, memoryview)):
        raise SignatureError('invalid-bytecode-input-type')
    if not MIN_SIGNED_BYTES <= len(raw_original_bytes) <= MAX_BYTECODE:
        raise SignatureError('signed-bytecode-size-outside-bounds')
    raw = bytes(raw_original_bytes)
    if raw[0] == 0:
        raise SignatureError('invalid-bytecode-header')
    body, trailer = raw[:-TRAILER_BYTES], raw[-TRAILER_BYTES:]
    # Direct native version selector 1013fa64c: XOR the first and last
    # trailer words; the first three bytes identify this signature family.
    version_word = bytes(left ^ right for left, right in zip(trailer[:4], trailer[20:24]))
    if version_word[:3] != b'\x32\xc4\x6a':
        raise SignatureError('unrecognized-signature-envelope')
    version = version_word[3]
    if version != 1:
        raise SignatureError('unsupported-signature-version')
    # Native 1013f942c additionally checks this V1 trailer consistency tag.
    tag = int.from_bytes(trailer[:4], 'little') ^ int.from_bytes(trailer[16:20], 'little')
    if tag != 0x74171870:
        raise SignatureError('signature-envelope-tag-mismatch')
    digest = blake3(body).digest(length=32)
    observed = _evaluate_public_map(trailer[:16], _public_coefficients())
    if not compare_digest(observed, digest[4:16]):
        raise SignatureError('signature-verification-failed')
    return {'verification': True, 'signature_version': version,
            'body_bytes': len(body), 'trailer_bytes': TRAILER_BYTES,
            'input_bytes': len(raw), 'algorithm': 'native-v1-blake3-public-quadratic'}


def _read_private_module(path):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to((ROOT / 'secrets').resolve()):
        raise SignatureError('cli-input-must-be-private-capture')
    flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptor = os.open(str(resolved), flags)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                or metadata.st_uid != os.getuid() or metadata.st_size > MAX_BYTECODE):
            raise SignatureError('private-capture-permissions-or-size')
        with os.fdopen(descriptor, 'rb') as source:
            descriptor = None
            return source.read(MAX_BYTECODE + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('private_module', type=Path)
    args = parser.parse_args()
    try:
        metadata = verify_signed_bytecode(_read_private_module(args.private_module))
    except (SignatureError, OSError) as error:
        reason = error.reason if isinstance(error, SignatureError) else 'private-capture-unavailable'
        print(json.dumps({'verified_count': 0, 'failed_count': 1, 'reason': reason}))
        return 2
    print(json.dumps({'verified_count': 1, 'failed_count': 0, **metadata}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
