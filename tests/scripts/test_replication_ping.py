import struct
import unittest

from replication_ping import ping_packet
from wire_reader import WireError


class PingTests(unittest.TestCase):
    def test_short_ping_and_reply_lengths(self):
        ping = b'\x83\x05\x00' + struct.pack('<QIII', 123, 456, 789, 0) + b'\x01\x00'
        self.assertEqual(len(ping), 25)
        decoded = ping_packet(ping)[0]
        self.assertEqual(decoded['timestamp'], 123)
        self.assertEqual(decoded['reply_words'], [])
        self.assertTrue(decoded['unreliable'])
        reply = b'\x83\x06\x01' + struct.pack('<Q9I', 123, *range(9)) + b'\x01\x00'
        decoded = ping_packet(reply)[0]
        self.assertEqual(decoded['reply_words'], list(range(1, 7)))
        self.assertEqual(decoded['trailer_words'], [7, 8])

    def test_all_truncations_and_invalid_extensions_rejected(self):
        valid = b'\x83\x05\x00' + struct.pack('<QIII', 1, 2, 3, 4) + b'\x01\x00'
        for n in range(len(valid)):
            with self.assertRaises(WireError): ping_packet(valid[:n])
        for value in (valid+b'\0', valid[:-2]+b'\x02\x00', valid[:2]+b'\x08'+valid[3:],
                      b'\x83\x07'+valid[2:]):
            with self.assertRaises(WireError): ping_packet(value)

    def test_explicit_optional_word_consumed(self):
        packet = b'\x83\x05\x04' + struct.pack('<QIII', 1, 2, 3, 4) + b'\x00' + struct.pack('<I', 5) + b'\x00'
        self.assertEqual(ping_packet(packet)[0]['extra_word'], 5)


if __name__ == '__main__': unittest.main()
