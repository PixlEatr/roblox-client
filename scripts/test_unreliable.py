import unittest

from unreliable import control_open, parse_control_open, encode_datagram, parse_datagram
from wire_reader import WireError


class UnreliableTests(unittest.TestCase):
    def test_known_header_and_varint_transition(self):
        self.assertEqual(encode_datagram(2, 64, b'abc'), b'\x02\x40\x40\0abc')
        result = parse_datagram(b'\x03\x01\0\x83\0')
        self.assertEqual((result['wire_id'], result['sequence'], result['payload']), (3, 1, b'\x83\0'))
        self.assertTrue(result['complete'])

    def test_control_fields_are_network_endian(self):
        known = b'\x02\x04\0\0\0\x06\0\0\0\x07'
        self.assertEqual(parse_control_open(known), (4, 6, 7))
        self.assertEqual(control_open(4, 6, 7), known)

    def test_fragment_bounds_and_truncation(self):
        fragment = parse_datagram(b'\x03\x01\x05\x02abc')
        self.assertFalse(fragment['complete'])
        self.assertEqual((fragment['total'], fragment['offset']), (5, 2))
        for bad in (b'', b'\xc0\0', b'\3\1', b'\3\1\2\1abc', b'\0\1\0abc'):
            with self.assertRaises(WireError): parse_datagram(bad)


if __name__ == '__main__': unittest.main()
