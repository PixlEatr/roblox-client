import unittest

from login_packets import ticket_hash, ticket_checksum, encode_signed, build_identifier, ticket_packet, verification_packet


class LoginCodecTests(unittest.TestCase):
    def test_xxh32_known_vector(self):
        self.assertEqual(ticket_hash(b"", 0), 0x02CC5D05)

    def test_native_checksum_vectors(self):
        # Independently obtained from bounded ARM64 emulation, synthetic data.
        for value, expected in ((0, 2126276933), (1, 2705179358), (0x12345678, 142237176),
                                (0x7FFFFFFF, 2942384042), (0x80000000, 1710407009),
                                (0xFFFFFFFF, 1680253933)):
            self.assertEqual(ticket_checksum(value), expected)

    def test_zigzag_boundaries(self):
        self.assertEqual(encode_signed(0), b"\0")
        self.assertEqual(encode_signed(-1), b"\x01")
        self.assertEqual(encode_signed(1), b"\x02")
        self.assertEqual(encode_signed(-(1 << 63)), b"\xff" * 9 + b"\x01")
        with self.assertRaises(ValueError):
            encode_signed(1 << 63)

    def test_build_identifier_and_issued_fields(self):
        self.assertEqual(len(build_identifier()), 74)
        self.assertEqual(build_identifier()[-1], ".")
        raw = ticket_packet({"UserId": 123, "ClientTicket": "synthetic-issued-ticket", "SessionId": "synthetic-session"})
        self.assertEqual(raw[:3], b"\x8a\xf6\x01")
        self.assertIn(b"synthetic-issued-ticket", raw)
        self.assertIn(b"synthetic-session", raw)
        self.assertEqual(raw[-4:], bytes.fromhex("feca01c0"))
        self.assertEqual(verification_packet(0)[0], 146)


if __name__ == "__main__":
    unittest.main()
