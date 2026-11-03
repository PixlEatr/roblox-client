"""Find ARM64 direct calls and tail branches in a pinned Mach-O.

This is a static triage tool. A call-site hit only identifies functions to
inspect; it does not classify them as readers or prove wire-field semantics.
Rotation/position helpers are the default targets; --target selects other
addresses without changing the decoder or excluding constructor tail thunks.
"""
from __future__ import annotations

import hashlib
import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXPECTED_SHA256 = 'b6b2184109237a8fea5de33d3265ac026cda3db05dbe3d66f749c91b4297b870'
HELPERS = {
    0x102E8F40C: 'packed_orientation_expander',
    0x104CBE7B4: 'compact_position_helper',
    0x104CBEC4C: 'quaternion_encoder',
    0x104CBEE10: 'quaternion_decoder',
}


class MachOError(ValueError):
    pass


@dataclass(frozen=True)
class MachOText:
    data: bytes
    text_address: int
    text_offset: int
    text_size: int
    image_base: int
    function_starts: tuple[int, ...]
    sha256: str


@dataclass(frozen=True)
class CallSite:
    caller: int
    address: int
    target: int
    kind: str


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise MachOError('truncated Mach-O field')
    return struct.unpack_from('<I', data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise MachOError('truncated Mach-O field')
    return struct.unpack_from('<Q', data, offset)[0]


def _uleb(data: bytes, offset: int, end: int) -> tuple[int, int]:
    value = shift = 0
    while offset < end and shift < 64:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise MachOError('invalid LC_FUNCTION_STARTS ULEB128')


def parse_macho(data: bytes, *, expected_sha256: str | None = None) -> MachOText:
    """Extract __TEXT,__text and LC_FUNCTION_STARTS from thin ARM64 Mach-O."""
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise MachOError('Mach-O SHA-256 does not match expected binary')
    if len(data) < 32 or data[:4] != b'\xcf\xfa\xed\xfe':
        raise MachOError('expected little-endian 64-bit Mach-O')
    if struct.unpack_from('<i', data, 4)[0] != 0x0100000C:
        raise MachOError('expected ARM64 Mach-O')
    ncmds = _u32(data, 16)
    offset = 32
    image_base = None
    text = None
    function_blob = None
    for _ in range(ncmds):
        command, size = _u32(data, offset), _u32(data, offset + 4)
        if size < 8 or offset + size > len(data):
            raise MachOError('invalid load command size')
        if command == 0x19:  # LC_SEGMENT_64
            segname = data[offset + 8:offset + 24].split(b'\0', 1)[0]
            vmaddr = _u64(data, offset + 24)
            if segname == b'__TEXT':
                image_base = vmaddr
                nsects = _u32(data, offset + 64)
                section_offset = offset + 72
                for _section in range(nsects):
                    section_name = data[section_offset:section_offset + 16].split(b'\0', 1)[0]
                    segment_name = data[section_offset + 16:section_offset + 32].split(b'\0', 1)[0]
                    if section_name == b'__text' and segment_name == b'__TEXT':
                        text = (_u64(data, section_offset + 32), _u64(data, section_offset + 40),
                                _u32(data, section_offset + 48))
                    section_offset += 80
        elif command == 0x26:  # LC_FUNCTION_STARTS
            blob_offset, blob_size = _u32(data, offset + 8), _u32(data, offset + 12)
            function_blob = (blob_offset, blob_size)
        offset += size
    if image_base is None or text is None or function_blob is None:
        raise MachOError('required __TEXT,__text or LC_FUNCTION_STARTS is missing')
    text_address, text_size, text_offset = text
    if text_offset + text_size > len(data):
        raise MachOError('__text extends beyond file')
    if text_address & 3 or text_offset & 3 or text_size & 3:
        raise MachOError('__text is not aligned as ARM64 instructions')
    blob_offset, blob_size = function_blob
    if blob_offset + blob_size > len(data):
        raise MachOError('LC_FUNCTION_STARTS extends beyond file')
    starts = []
    cursor = blob_offset
    end = blob_offset + blob_size
    address = image_base
    while cursor < end:
        delta, cursor = _uleb(data, cursor, end)
        if delta == 0:
            break
        address += delta
        starts.append(address)
    if not starts or any(a >= b for a, b in zip(starts, starts[1:])):
        raise MachOError('function starts table is empty or unordered')
    return MachOText(data, text_address, text_offset, text_size, image_base,
                     tuple(starts), digest)


def find_direct_calls(image: MachOText, targets: Iterable[int] = HELPERS) -> tuple[CallSite, ...]:
    """Return direct ARM64 BL calls and tail B branches into selected helpers."""
    target_set = set(targets)
    calls = []
    raw_calls = []
    # `bytes.find` scans in optimized native code. Candidate B/BL words have
    # high bytes 0x14..0x17 / 0x94..0x97 in little-endian storage; decode only
    # only those candidates instead of iterating over every text instruction.
    text = image.data[image.text_offset:image.text_offset + image.text_size]
    for opcode_byte in (*range(0x14, 0x18), *range(0x94, 0x98)):
        needle = bytes((opcode_byte,))
        byte_offset = 3
        while True:
            byte_offset = text.find(needle, byte_offset)
            if byte_offset < 0:
                break
            if byte_offset % 4 != 3:
                byte_offset += 1
                continue
            word = int.from_bytes(text[byte_offset - 3:byte_offset + 1], 'little')
            relative = byte_offset - 3
            if word & 0x7C000000 != 0x14000000:
                byte_offset += 1
                continue
            displacement = word & 0x03FFFFFF
            if displacement & 0x02000000:
                displacement -= 0x04000000
            site = image.text_address + relative
            destination = site + displacement * 4
            if destination in target_set:
                raw_calls.append((site, destination, 'bl' if word & 0x80000000 else 'b'))
            byte_offset += 1
    start_index = 0
    for site, destination, kind in sorted(raw_calls):
        while start_index + 1 < len(image.function_starts) and image.function_starts[start_index + 1] <= site:
            start_index += 1
        caller = image.function_starts[start_index] if image.function_starts[0] <= site else 0
        calls.append(CallSite(caller, site, destination, kind))
    return tuple(calls)


def scan_file(path: str | Path, *, require_pinned_hash: bool = True,
              targets: Iterable[int] = HELPERS) -> tuple[MachOText, tuple[CallSite, ...]]:
    data = Path(path).read_bytes()
    image = parse_macho(data, expected_sha256=EXPECTED_SHA256 if require_pinned_hash else None)
    return image, find_direct_calls(image, targets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('macho', help='path to the copied 0.745 ARM64 Mach-O')
    parser.add_argument('--allow-unpinned', action='store_true',
                        help='scan without enforcing the recorded SHA-256')
    parser.add_argument('--target', type=lambda value: int(value, 0), action='append',
                        help='target address (hex or decimal); repeat to scan multiple targets')
    args = parser.parse_args()
    image, calls = scan_file(args.macho, require_pinned_hash=not args.allow_unpinned,
                             targets=args.target if args.target is not None else HELPERS)
    print(f'sha256={image.sha256} function_starts={len(image.function_starts)}')
    for call in calls:
        print(f'kind={call.kind} caller=0x{call.caller:x} site=0x{call.address:x} '
              f'target=0x{call.target:x} helper={HELPERS.get(call.target, "custom")}')


if __name__ == '__main__':
    main()
