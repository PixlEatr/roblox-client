import unittest

from physics_header import (
    HEADER_SIZE,
    LAYOUT_SEQUENCE_THEN_TAG,
    LAYOUT_TAG_THEN_SEQUENCE,
    decode_header,
    encode_header,
)


class PhysicsHeaderTests(unittest.TestCase):
    def test_literal_sequence_then_tag_vector(self):
        raw = bytes.fromhex("1b 00 01 02 03 04 05 06 07 08 07 06 05 04 03 02 01 85")
        parsed = decode_header(raw, layout=LAYOUT_SEQUENCE_THEN_TAG)
        self.assertEqual(parsed, {
            "timestamp_ms": 0x0706050403020100,
            "sequence": 0x0102030405060708,
            "layout": LAYOUT_SEQUENCE_THEN_TAG,
        })
        self.assertEqual(encode_header(parsed["timestamp_ms"], parsed["sequence"],
                                       layout=LAYOUT_SEQUENCE_THEN_TAG), raw)

    def test_literal_tag_then_sequence_vector(self):
        raw = bytes.fromhex("1b de ad be ef 00 11 22 33 85 08 07 06 05 04 03 02 01")
        parsed = decode_header(raw, layout=LAYOUT_TAG_THEN_SEQUENCE)
        self.assertEqual(parsed["timestamp_ms"], 0x33221100efbeadde)
        self.assertEqual(parsed["sequence"], 0x0102030405060708)
        self.assertEqual(encode_header(parsed["timestamp_ms"], parsed["sequence"],
                                       layout=LAYOUT_TAG_THEN_SEQUENCE), raw)

    def test_requires_layout(self):
        with self.assertRaises(TypeError):
            encode_header(0, 0)
        with self.assertRaises(TypeError):
            decode_header(bytes(HEADER_SIZE))
        with self.assertRaises(ValueError):
            encode_header(0, 0, layout="guess")

    def test_rejects_truncation_and_trailing_bytes(self):
        valid = encode_header(0, 7, layout=LAYOUT_SEQUENCE_THEN_TAG)
        for length in range(HEADER_SIZE):
            with self.subTest(length=length), self.assertRaises(ValueError):
                decode_header(valid[:length], layout=LAYOUT_SEQUENCE_THEN_TAG)
        with self.assertRaises(ValueError):
            decode_header(valid + b"x", layout=LAYOUT_SEQUENCE_THEN_TAG)

    def test_rejects_wrong_tags_and_wrong_layout_marker(self):
        valid = bytearray(encode_header(0, 7, layout=LAYOUT_SEQUENCE_THEN_TAG))
        valid[0] = 0
        with self.assertRaises(ValueError):
            decode_header(bytes(valid), layout=LAYOUT_SEQUENCE_THEN_TAG)
        valid = bytearray(encode_header(0, 7, layout=LAYOUT_SEQUENCE_THEN_TAG))
        valid[-1] = 0
        with self.assertRaises(ValueError):
            decode_header(bytes(valid), layout=LAYOUT_SEQUENCE_THEN_TAG)

    def test_rejects_invalid_timestamp_and_sequence(self):
        for timestamp in (-1, 1 << 64, True, 1.0):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                encode_header(timestamp, 0, layout=LAYOUT_SEQUENCE_THEN_TAG)
        for sequence in (-1, 1 << 64, True, 1.0):
            with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                encode_header(0, sequence, layout=LAYOUT_SEQUENCE_THEN_TAG)


if __name__ == "__main__":
    unittest.main()
