"""Empty script data must be registered by the peer before cache reuse."""
import hashlib
import struct
import unittest

from wire_reader import Reader, WireError
from world_replication import World


EMPTY = hashlib.blake2b(b'', digest_size=16).digest()


def world(property_type=47):
    return World({'classes': [{'name': 'Script', 'properties': [0]}],
                  'properties': [{'id': 0, 'class': 0, 'name': 'Source', 'type': property_type}]})


def instance(identifier, *, definition=False, lists=2):
    data = b'\x01' + struct.pack('<IH', identifier, 0)  # scope, ID, class
    data += b'\x00' + EMPTY + b'\xff' * lists
    if definition:
        data += EMPTY + b'\x00\x00'  # definition marker and zero-length payload
    return data + b'\x00'  # null parent


class SharedStringRegistrationTests(unittest.TestCase):
    def test_first_empty_source_consumes_definition_before_following_item(self):
        for property_type in (46, 47):
            with self.subTest(property_type=property_type):
                w = world(property_type)
                w.packet(b'\x83\x02' + instance(1, definition=True)
                         + b'\x10' + struct.pack('<i', 123) + b'\x00')
                self.assertEqual(w.tags, [123])
                self.assertEqual(w.shared[EMPTY], b'')
                self.assertIsNone(w.instances[(1, 1)]['parent'])
                self.assertTrue(w.creation[(1, 1)]['complete'])

    def test_later_empty_source_uses_registered_entry_without_extra_bytes(self):
        w = world()
        w.packet(b'\x83\x02' + instance(1, definition=True)
                 + b'\x02' + instance(2) + b'\x00')
        self.assertEqual(len(w.instances), 2)
        self.assertEqual(w.instances[(1, 2)]['properties']['Source'], {'shared': EMPTY.hex()})
        self.assertTrue(w.creation[(1, 2)]['complete'])

    def test_known_hash_is_not_an_implicit_cache_registration(self):
        with self.assertRaises(WireError):
            world().shared_entry(Reader(EMPTY + b'\x01'))
        w = world()
        w.shared_entry(Reader(EMPTY + b'\x00\x00'))
        self.assertEqual(w.shared_entry(Reader(EMPTY + b'\x01')), EMPTY)

    def test_registration_is_not_shared_between_worlds(self):
        first = world()
        first.packet(b'\x83\x02' + instance(1, definition=True) + b'\x00')
        second = world()
        second.packet(b'\x83\x02' + instance(2, definition=True) + b'\x00')
        self.assertTrue(second.creation[(1, 2)]['complete'])

    def test_cached_instance_requires_received_shared_definition(self):
        with self.assertRaises(WireError):
            world().new_instance(Reader(instance(1, lists=1)), lists=1, inline_shared=False)
        w = world()
        w.shared_entry(Reader(EMPTY + b'\x00\x00'))
        r = Reader(instance(2, lists=1))
        w.new_instance(r, lists=1, inline_shared=False)
        r.finish()
        self.assertTrue(w.creation[(1, 2)]['complete'])

    def test_truncated_definition_and_wrong_digest_still_fail(self):
        definition = EMPTY + b'\x00\x00'
        for end in range(len(definition)):
            with self.subTest(end=end), self.assertRaises(WireError):
                world().shared_entry(Reader(definition[:end]))
        with self.assertRaisesRegex(WireError, 'digest mismatch'):
            world().shared_entry(Reader(EMPTY + b'\x00\x01x'))


if __name__ == '__main__':
    unittest.main()
