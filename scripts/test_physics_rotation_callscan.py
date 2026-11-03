import struct
import unittest

from physics_rotation_callscan import MachOError, MachOText, find_direct_calls, parse_macho


def uleb(value):
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def tiny_macho():
    base = 0x100000000
    text_addr, text_off = base + 0x200, 0x200
    target = base + 0x280
    imm = (target - text_addr) // 4
    words = struct.pack('<II', 0x94000000 | imm, 0x14000000 | 31)
    cmds_size = 152 + 16
    header = struct.pack('<IiiIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 2,
                         cmds_size, 0, 0)
    segment = bytearray(152)
    struct.pack_into('<II', segment, 0, 0x19, 152)
    segment[8:14] = b'__TEXT'
    struct.pack_into('<QQQQiiII', segment, 24, base, 0x1000, 0, 0x400,
                     7, 5, 1, 0)
    section = 72
    segment[section:section + 6] = b'__text'
    segment[section + 16:section + 22] = b'__TEXT'
    struct.pack_into('<QQIIIIIIII', segment, section + 32, text_addr, len(words),
                     text_off, 2, 0, 0, 0, 0, 0, 0)
    function_blob = uleb(0x200) + uleb(4) + b'\0'
    function_command = struct.pack('<IIII', 0x26, 16, 0x300, len(function_blob))
    data = bytearray(0x400)
    data[:len(header)] = header
    data[32:32 + 152] = segment
    data[32 + 152:32 + cmds_size] = function_command
    data[text_off:text_off + len(words)] = words
    data[0x300:0x300 + len(function_blob)] = function_blob
    return bytes(data), target


class RotationCallscanTests(unittest.TestCase):
    def test_decodes_bl_and_tail_b_and_maps_callers(self):
        data, target = tiny_macho()
        image = parse_macho(data)
        calls = find_direct_calls(image, {target})
        self.assertEqual(len(image.function_starts), 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].address, 0x100000200)
        self.assertEqual(calls[0].caller, 0x100000200)
        self.assertEqual(calls[0].target, target)
        self.assertEqual(calls[0].kind, 'bl')
        self.assertEqual(calls[1].address, 0x100000204)
        self.assertEqual(calls[1].caller, 0x100000204)
        self.assertEqual(calls[1].target, target)
        self.assertEqual(calls[1].kind, 'b')

    def test_rejects_unpinned_binary_when_expected_hash_is_supplied(self):
        data, _ = tiny_macho()
        with self.assertRaises(MachOError):
            parse_macho(data, expected_sha256='0' * 64)

    def test_constructor_tail_thunk_sign_extends_backward_displacement(self):
        # Actual independently disassembled instruction at 104C9E348. A BL-only
        # search missed this path and incorrectly concluded there was no caller.
        site, target = 0x104C9E348, 0x104C9D9CC
        image = MachOText(struct.pack('<I', 0x17FFFDA1), site, 0, 4,
                          0x100000000, (site,), 'synthetic')
        calls = find_direct_calls(image, {target})
        self.assertEqual([(c.address, c.target, c.kind) for c in calls],
                         [(site, target, 'b')])

    def test_rejects_non_macho_input(self):
        with self.assertRaises(MachOError):
            parse_macho(b'not a Mach-O')


if __name__ == '__main__':
    unittest.main()
