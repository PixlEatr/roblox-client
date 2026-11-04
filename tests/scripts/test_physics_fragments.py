import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from physics_fragments import FragmentError, FragmentReassembler
from unreliable import parse_datagram


def qvarint(value):
    for width, prefix in ((1, 0), (2, 1), (4, 2), (8, 3)):
        if value < 1 << (width * 8 - 2):
            return bytes([prefix << 6 | (value >> (8 * (width - 1)))]) + (
                value.to_bytes(width, "big")[-(width - 1):] if width > 1 else b"")
    raise ValueError("too large")


def parsed_fragment(wire_id, sequence, total, offset, payload):
    # Generic synthetic header matching unreliable.parse_datagram's envelope.
    raw = (qvarint(wire_id) + qvarint(sequence) + qvarint(total) +
           qvarint(offset) + payload)
    return parse_datagram(raw)


class FragmentReassemblerTests(unittest.TestCase):
    def test_out_of_order_completion(self):
        r = FragmentReassembler()
        self.assertIsNone(r.push(parsed_fragment(3, 8, 6, 3, b"def"), now=0))
        self.assertEqual(r.push(parsed_fragment(3, 8, 6, 0, b"abc"), now=1), b"abcdef")
        self.assertEqual(r.pending_count, 0)

    def test_identical_duplicate_is_idempotent(self):
        r = FragmentReassembler()
        f = parsed_fragment(2, 1, 4, 0, b"ab")
        self.assertIsNone(r.push(f, now=0))
        self.assertIsNone(r.push(f, now=1))
        self.assertEqual(r.push(parsed_fragment(2, 1, 4, 2, b"cd"), now=2), b"abcd")

    def test_identical_overlap_and_conflicting_overlap(self):
        r = FragmentReassembler()
        self.assertIsNone(r.push(parsed_fragment(1, 4, 5, 0, b"abc"), now=0))
        self.assertEqual(r.push(parsed_fragment(1, 4, 5, 2, b"cde"), now=1), b"abcde")
        self.assertIsNone(r.push(parsed_fragment(1, 5, 4, 0, b"ab"), now=3))
        with self.assertRaises(FragmentError):
            r.push(parsed_fragment(1, 5, 4, 1, b"ZZ"), now=4)
        self.assertEqual(r.pending_count, 0)

    def test_inconsistent_total_drops_pending(self):
        r = FragmentReassembler()
        self.assertIsNone(r.push(parsed_fragment(1, 1, 5, 0, b"ab"), now=0))
        with self.assertRaises(FragmentError):
            r.push(parsed_fragment(1, 1, 6, 2, b"cd"), now=1)
        self.assertEqual(r.pending_bytes, 0)

    def test_expiry(self):
        r = FragmentReassembler(expiry_seconds=2)
        self.assertIsNone(r.push(parsed_fragment(1, 1, 3, 0, b"a"), now=0))
        self.assertEqual(r.expire(now=1.99), 0)
        self.assertEqual(r.expire(now=2), 1)
        self.assertEqual(r.pending_count, 0)

    def test_count_and_byte_caps_evict_oldest(self):
        r = FragmentReassembler(max_pending=1, max_pending_bytes=10)
        self.assertIsNone(r.push(parsed_fragment(1, 1, 3, 0, b"a"), now=0))
        self.assertIsNone(r.push(parsed_fragment(1, 2, 3, 0, b"b"), now=1))
        self.assertEqual(r.pending_count, 1)
        self.assertEqual(r.push(parsed_fragment(1, 2, 3, 1, b"cd"), now=2), b"bcd")
        small = FragmentReassembler(max_pending=5, max_pending_bytes=6)
        self.assertIsNone(small.push(parsed_fragment(1, 1, 2, 0, b"x"), now=0))
        self.assertIsNone(small.push(parsed_fragment(1, 2, 2, 0, b"y"), now=1))
        self.assertEqual(small.pending_count, 1)

    def test_rejects_malformed_ranges_and_limits(self):
        r = FragmentReassembler(max_message_bytes=4)
        for total, offset, payload in ((0, 0, b"x"), (5, 0, b"x"), (4, 4, b"x"), (4, 0, b"")):
            with self.subTest(total=total, offset=offset, payload=payload):
                with self.assertRaises(FragmentError):
                    r.push({"wire_id": 1, "sequence": 1, "total": total,
                            "offset": offset, "payload": payload}, now=0)


if __name__ == "__main__":
    unittest.main()
