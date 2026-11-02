import struct
import unittest

from bytecode_check import MAX_PROGRAM_BYTES, parse_program_check
from wire_reader import WireError


class ProgramCheckTests(unittest.TestCase):
    def test_fixed_width_native_fields_and_callback_argument_order(self):
        # Authored opaque fixture, never executed and unrelated to a live check.
        packet = bytes.fromhex('9b040302010807060503000000') + b'abc'
        check = parse_program_check(packet)
        self.assertEqual(check.arguments, (0x05060708, 0x01020304))
        self.assertEqual(check.program, b'abc')
        self.assertEqual(check.summary(), {'program_bytes': 3,
                         'execution_implemented': False, 'response_implemented': False})

    def test_every_truncation_trailing_data_and_wrong_type_rejected(self):
        packet = b'\x9b' + struct.pack('<III', 123, 456, 5) + b'hello'
        for cut in range(len(packet)):
            with self.subTest(cut=cut), self.assertRaises(WireError):
                parse_program_check(packet[:cut])
        for malformed in (packet + b'\0', b'\x9c' + packet[1:], bytearray(packet)):
            with self.assertRaises(WireError):
                parse_program_check(malformed)

    def test_sizes_are_bounded_and_no_argument_values_leak_in_summary(self):
        for length in (0, MAX_PROGRAM_BYTES + 1, 0xffffffff):
            with self.assertRaises(WireError):
                parse_program_check(b'\x9b' + struct.pack('<III', 123, 456, length))
        summary = parse_program_check(b'\x9b' + struct.pack('<III', 123, 456, 1) + b'x').summary()
        self.assertNotIn('first', summary)
        self.assertNotIn('second', summary)
        self.assertNotIn('program', summary)


if __name__ == '__main__':
    unittest.main()
