import unittest

from blake3 import blake3
from bytecode_signature import (COEFFICIENT_COUNT, SignatureError, _evaluate_public_map,
                                _public_coefficients, verify_signed_bytecode)


class SignatureTests(unittest.TestCase):
    def test_official_blake3_empty_vector(self):
        self.assertEqual(blake3(b'').hexdigest(),
                         'af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262')

    def test_public_map_msb_bit_order_and_first_last_pair(self):
        coefficients = [0] * COEFFICIENT_COUNT
        coefficients[0], coefficients[1], coefficients[128] = 1, 2, 4
        coefficients[255] = 8  # pair (0,127): first triangular row's last entry
        signature = b'\x80' + bytes(14) + b'\x01'
        self.assertEqual(_evaluate_public_map(signature, coefficients), (15).to_bytes(12, 'little'))

    def test_public_map_last_pair_index(self):
        coefficients = [0] * COEFFICIENT_COUNT
        coefficients[-1] = 16  # final pair (126,127)
        self.assertEqual(_evaluate_public_map(bytes(15) + b'\x03', coefficients),
                         (16).to_bytes(12, 'little'))

    def test_all_zero_synthetic_signature_retains_only_constant_coefficient(self):
        coefficients = [0] * COEFFICIENT_COUNT
        coefficients[0] = 17
        self.assertEqual(_evaluate_public_map(bytes(16), coefficients), (17).to_bytes(12, 'little'))

    def test_pinned_public_asset_geometry(self):
        coefficients = _public_coefficients()
        self.assertEqual(len(coefficients), COEFFICIENT_COUNT)
        self.assertTrue(all(0 <= value < 1 << 96 for value in coefficients))

    def test_invalid_inputs_fail_closed(self):
        for value in (None, 'not-bytecode', b'', bytes(279), bytes(280), b'\1' + bytes(279)):
            with self.assertRaises(SignatureError):
                verify_signed_bytecode(value)

    def test_invalid_map_geometry_fails_closed(self):
        with self.assertRaises(SignatureError):
            _evaluate_public_map(bytes(15), [0] * COEFFICIENT_COUNT)
        with self.assertRaises(SignatureError):
            _evaluate_public_map(bytes(16), [0])


if __name__ == '__main__':
    unittest.main()
