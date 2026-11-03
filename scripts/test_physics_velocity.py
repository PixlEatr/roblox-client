import math
import random
import unittest

from physics_velocity import (decode, decode_native_short_velocity,
                              encode_native_short_velocity)
from physics_wire import velocity


class CompactVelocityTests(unittest.TestCase):
    def test_zero_sentinel_and_bytes(self):
        result = decode(velocity((0.0, 0.0, 0.0)))
        self.assertTrue(result.zero_sentinel)
        self.assertEqual(result.vector, (0.0, 0.0, 0.0))
        self.assertEqual(result.packed, b'\x00')

    def test_round_trip_is_within_half_quantum_and_preserves_bytes(self):
        rng = random.Random(96831)
        vectors = [(0.001, -0.25, 10.0), (-0.7, 0.4, 2.0), (1e6, -1e6, 0.0)]
        vectors += [tuple(rng.uniform(-1, 1) * 2**e for _ in range(3))
                    for e in range(15) for _ in range(30)]
        for vector in vectors:
            packed = velocity(vector)
            decoded = decode(packed)
            self.assertEqual(decoded.packed, packed)
            self.assertEqual(decoded.packed_hex, packed.hex())
            self.assertFalse(decoded.zero_sentinel)
            self.assertTrue(all(math.isfinite(x) for x in decoded.vector))
            # Encoder quantizes normalized input in increments of 1/2047.
            maximum = max(abs(x) for x in vector)
            exponent = max(0, min(14, math.frexp(maximum)[1]))
            quantum = 2**exponent / 2047
            for got, source in zip(decoded.vector, vector):
                bounded = max(-2**exponent, min(2**exponent, source))
                self.assertLessEqual(abs(got-bounded), quantum * 0.51 + 1e-8)

    def test_negative_values_decode_signed(self):
        decoded = decode(velocity((-0.5, -1.25, -2.5)))
        self.assertTrue(all(v < 0 for v in decoded.vector))
        self.assertTrue(all(q < 0 for q in decoded.quantized))

    def test_rejects_truncated_and_extra_payloads(self):
        for value in (b'', b'\x00\x00', b'\x10', b'\x10\x00\x00\x00\x00\x00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode(value)

    def test_rejects_noncanonical_negative_2048(self):
        # x=-2048, y=z=0. Header exponent zero is stored as high nibble 1.
        payload = ((0x800) << 20).to_bytes(4, 'little')
        with self.assertRaises(ValueError):
            decode(b'\x10' + payload)

    def test_rejects_invalid_zero_header_with_payload(self):
        with self.assertRaises(ValueError):
            decode(b'\x00\x01\x00\x00\x00')

    def test_native_three_byte_short_velocity_golden_vectors(self):
        vectors = (
            ((0.0, 0.0, 0.0), bytes.fromhex('00')),
            ((0.0009, -0.0007, 0.0008), bytes.fromhex('00')),
            ((0.001, 0.0, 0.0), bytes.fromhex('dd 00 00')),
            ((1.0, -2.0, 3.0), bytes.fromhex('88 8c 17')),
        )
        for vector, expected in vectors:
            with self.subTest(vector=vector):
                self.assertEqual(encode_native_short_velocity(vector), expected)

    def test_native_short_velocity_validates_inputs(self):
        for vector in ((0.0, 1.0), (0.0, 0.0, float('inf')),
                       (0.0, float('nan'), 0.0), (1e9, 0.0, 0.0)):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                encode_native_short_velocity(vector)

    def test_native_short_velocity_decoder_matches_encoder_metadata(self):
        vectors = ((0.0, 0.0, 0.0), (0.001, 0.0, 0.0),
                   (1.0, -2.0, 3.0), (-100.0, 25.5, 0.2))
        for vector in vectors:
            with self.subTest(vector=vector):
                packed = encode_native_short_velocity(vector)
                result = decode_native_short_velocity(packed)
                self.assertEqual(result.packed, packed)
                self.assertEqual(result.zero_sentinel, packed == b'\0')
                if packed != b'\0':
                    self.assertEqual(len(packed), 3)
                    self.assertIsNotNone(result.exponent)
                    self.assertTrue(all(-31 <= value <= 31 for value in result.quantized))

    def test_native_short_velocity_decoder_accepts_nonzero_headers_without_marker(self):
        vectors = (
            (bytes.fromhex('01 00 00'), (0.5161290168762207, 0.0, 0.0),
             0, (16, 0, 0)),
            (bytes.fromhex('7f 00 00'), (-0.25806450843811035, 0.0, 0.0),
             -1, (-16, 0, 0)),
        )
        for raw, vector, exponent, quantized in vectors:
            with self.subTest(raw=raw):
                result = decode_native_short_velocity(raw)
                self.assertEqual(result.vector, vector)
                self.assertEqual(result.exponent, exponent)
                self.assertEqual(result.quantized, quantized)
                self.assertEqual(result.packed, raw)
                self.assertFalse(result.zero_sentinel)

    def test_native_short_velocity_decoder_rejects_bad_lengths_and_zero_header(self):
        for raw in (b'', b'\x80', b'\x80\x00', b'\x00\x00',
                    b'\x00\x00\x00', b'\x80\x00\x00\x00'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_native_short_velocity(raw)


if __name__ == '__main__':
    unittest.main()
