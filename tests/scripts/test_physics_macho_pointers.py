#!/usr/bin/env python3
"""Regression checks for pointer fixup inventory used by static vtable scans."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from physics_macho_pointers import Image, Segment


def uleb(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


class BindWraparoundTests(unittest.TestCase):
    def setUp(self):
        self.data = bytearray(0x100)
        self.segment = Segment('__DATA', 0x100000000, 0x100, 0, 0x100)

    def test_bind_add_addr_uleb_wraps_at_u64(self):
        # Offset 16: bind advances by 8, then dyld's 64-bit unsigned ULEB
        # delta -16 wraps it back to offset 8 for the second bind.
        stream = bytes((0x70,)) + uleb(16) + bytes((0x40,)) + b'_target\0' + bytes((0x51, 0x90, 0x80)) + uleb(0xfffffffffffffff0) + bytes((0x90, 0))
        image = Image(bytes(self.data), [self.segment], None, (0, len(stream)), None, None, None)
        image.data = stream
        found = image.bind_fixups(image.bind)
        self.assertEqual([f.fileoff for f in found], [16, 8])
        self.assertEqual([f.vmaddr for f in found], [0x100000010, 0x100000008])

    def test_references_report_file_offsets_for_preferred_addresses(self):
        data = bytearray(0x40)
        data[8:16] = (0x100000020).to_bytes(8, 'little')
        image = Image(bytes(data), [Segment('__DATA', 0x100000000, 0x40, 0, 0x40)], None, None, None, None, None)
        self.assertEqual(image.raw_references([0x100000020]), {0x100000020: [8]})

    def test_location_outside_file_backed_segment_is_rejected(self):
        image = Image(bytes(self.data), [self.segment], None, None, None, None, None)
        with self.assertRaises(ValueError):
            image.vm_to_off(0, 0x100)


if __name__ == '__main__':
    unittest.main()
