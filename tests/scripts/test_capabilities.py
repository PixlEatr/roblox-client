import json
from pathlib import Path
import struct
import unittest

from capabilities import capabilities_path, decode_capabilities, encode_capabilities


class CapabilitiesTests(unittest.TestCase):
    def test_observed_text_contract(self):
        self.assertEqual(encode_capabilities(0), "0000000000000000")
        self.assertEqual(encode_capabilities(0x0123456789abcdef), "0123456789abcdef")
        self.assertEqual(encode_capabilities(0xffffffffffffffff), "ffffffffffffffff")
        self.assertEqual(decode_capabilities("FEDCBA9876543210"), 0xfedcba9876543210)
        self.assertEqual(capabilities_path(0x81), "/wt?X-Rbx-Capabilities=0000000000000081")

    def test_rejects_values_outside_native_unsigned_range(self):
        for value in [-1, 1 << 64, 1.0, True, "1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_capabilities(value)

    def test_exact_decoder_length_and_alphabet(self):
        for value in ["", "0" * 15, "0" * 17, "0x0123456789abcd", "g" * 16,
                      "000000000000000 ", "000000000000000\n", "０" * 16, None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                decode_capabilities(value)

    def test_encoder_byte_order_against_binary_neon_constants(self):
        # Independent byte extraction modeled from the four loaded NEON vectors,
        # rather than testing encode/decode only against one another.
        root = Path(__file__).resolve().parents[2]
        if not (root / 'inputs/RobloxPlayer-0.745-arm64').is_file() or not (root / 'outputs/macho-layout.json').is_file():
            self.skipTest('local Roblox binary and layout are not bundled')
        layout = json.loads((root / "outputs/macho-layout.json").read_text())
        data = (root / "inputs/RobloxPlayer-0.745-arm64").read_bytes()
        shifts = []
        for address in [0x105b30150, 0x105b30140, 0x105b52b80, 0x105b52b70]:
            section = next(s for s in layout["sections"] if s["address"] <= address < s["address"] + s["size"])
            offset = section["offset"] + address - section["address"]
            shifts.extend(struct.unpack_from("<2q", data, offset))
        self.assertEqual(shifts, [-56, -48, -40, -32, -24, -16, -8, 0])
        for value in [0, 1, 0x0123456789abcdef, 0xffffffffffffffff] + [1 << i for i in range(64)]:
            raw = bytes((value >> -shift) & 255 for shift in shifts)
            self.assertEqual(encode_capabilities(value), raw.hex())


if __name__ == "__main__":
    unittest.main()
