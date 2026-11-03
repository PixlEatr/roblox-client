import dataclasses
import struct
import unittest

from ping_back import PingBackTelemetry, encode_ping_back
from replication_ping import ping_packet


def request(timestamp=0x0102030405060708, *, flags=0, kind=5):
    packet = bytes((0x83, kind, flags))
    packet += struct.pack('<QIII', timestamp, 0x12345678, 0x11111111, 0x22222222)
    packet += b'\x01'
    if flags & 4:
        packet += struct.pack('<I', 0x33333333)
    return packet + b'\0'


class PingBackTests(unittest.TestCase):
    def setUp(self):
        # Synthetic observations exercise serialization, never native attestation.
        self.telemetry = PingBackTelemetry((11, 22, 33, 44, 55, 66), 77, 88)

    def test_native_field_order_has_independent_byte_fixture(self):
        expected = bytes.fromhex(
            '830601080706050403020178563412'
            '0b00000016000000210000002c0000003700000042000000'
            '4d000000580000000100')
        result = encode_ping_back(request(), self.telemetry)
        self.assertEqual(result, expected)
        self.assertEqual(len(result), 49)
        parsed = ping_packet(result)[0]
        self.assertEqual(parsed['reply_words'], [11, 22, 33, 44, 55, 66])
        self.assertEqual(parsed['trailer_words'], [77, 88])
        self.assertEqual(parsed['timestamp'], 0x0102030405060708)
        self.assertEqual(parsed['first_word'], 0x12345678)

    def test_conditional_complement_does_not_mutate_or_toggle_on_repeat(self):
        result = encode_ping_back(request(0x20), self.telemetry)
        self.assertEqual(ping_packet(result)[0]['trailer_words'][1], 88 ^ 0xffffffff)
        self.assertEqual(encode_ping_back(request(0x20), self.telemetry), result)
        self.assertEqual(self.telemetry.response_word, 88)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.telemetry.status_word = 0

    def test_no_implicit_telemetry_and_strict_integer_bounds(self):
        with self.assertRaises(TypeError): PingBackTelemetry()
        with self.assertRaises(TypeError): encode_ping_back(request(), None)
        for bad in (True, -1, 1 << 32, 1.5, '1'):
            for args in (((bad, 1, 2, 3, 4, 5), 0, 0),
                         ((0, 1, 2, 3, 4, 5), bad, 0),
                         ((0, 1, 2, 3, 4, 5), 0, bad)):
                with self.assertRaises(ValueError): PingBackTelemetry(*args)
        with self.assertRaises(ValueError): PingBackTelemetry((1, 2), 0, 0)

    def test_rejects_replies_extensions_batches_and_truncations(self):
        reply = encode_ping_back(request(), self.telemetry)
        for packet in (reply, request(flags=4), b'\x83\0',
                       request()[:-1] + request()[1:], request() + b'\0'):
            with self.assertRaises(ValueError): encode_ping_back(packet, self.telemetry)
        for length in range(len(request())):
            with self.assertRaises(ValueError):
                encode_ping_back(request()[:length], self.telemetry)


if __name__ == '__main__':
    unittest.main()
