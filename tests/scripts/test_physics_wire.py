"""Independent wire-level regressions for the recovered compact motion fields."""
import math
import unittest

from physics_wire import position, velocity
from wire_reader import Reader, WireError
from world_replication import World


class PhysicsWireTests(unittest.TestCase):
    def test_pinned_native_position_vectors_and_wire_decoder(self):
        # These byte vectors were cross-checked against the selected 0.745
        # native codec; keeping them literal catches coordinated encoder drift.
        cases = (
            ((0.0, 0.0, 0.0), '0000000000'),
            ((1.0, -1.0, 0.5), '0a00010820'),
            ((16.0, -8.0, 2.0), '2a008000c00010'),
            ((1024.0, -256.0, 128.0), '5a0007008000000200'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                encoded = position(source)
                self.assertEqual(encoded.hex(), expected)
                reader = Reader(encoded)
                decoded = World.compact_position(reader)
                reader.finish()
                self.assertEqual(len(decoded), 3)
                self.assertTrue(all(math.isfinite(value) for value in decoded))
                self.assertGreaterEqual(encoded[0], 0)

    def test_position_quantization_across_width_transitions_and_signs(self):
        # The header's documented compact widths change at exponent 4 and 10.
        for source, width in (
            ((15.999, -3.25, 0.5), 5),
            ((16.0, -8.0, 2.0), 7),
            ((1023.999, -511.0, 127.0), 7),
            ((1024.0, -256.0, 128.0), 9),
            ((-16384.0, 8192.0, -4096.0), 9),
        ):
            with self.subTest(source=source):
                encoded = position(source)
                self.assertEqual(len(encoded), width)
                reader = Reader(encoded)
                decoded = World.compact_position(reader)
                reader.finish()
                self.assertEqual([math.copysign(1, x) for x in decoded],
                                 [math.copysign(1, x) for x in source])
                self.assertLess(max(abs(a-b) for a, b in zip(decoded, source)),
                                max(1.0, max(abs(x) for x in source) / 10000))

    def test_position_rejects_bad_vectors_and_reader_rejects_truncation(self):
        for source in ((0, 0), (0, 0, 0, 0), (float('nan'), 0, 0),
                       (0, float('inf'), 0), (1e9, 0, 0)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                position(source)
        encoded = position((1024.0, -256.0, 128.0))
        for size in range(len(encoded)):
            with self.subTest(size=size), self.assertRaises(WireError):
                World.compact_position(Reader(encoded[:size]))
        reader = Reader(encoded + b'\0')
        World.compact_position(reader)
        with self.assertRaises(WireError):
            reader.finish()

    def test_pinned_velocity_vectors_and_native_small_value_cutoff(self):
        cases = (
            ((0.0, 0.0, 0.0), '00'),
            ((0.0009, 0.0, 0.0), '00'),
            ((0.001, 0.0, 0.0), '1000002000'),
            ((1.0, -1.0, 0.5), '2020000c40'),
            ((-1.0, -2.0, -3.0), '31a0000ce0'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                encoded = velocity(source)
                self.assertEqual(encoded.hex(), expected)
                self.assertIn(len(encoded), (1, 5))

    def test_velocity_is_decoded_inside_the_world_physics_value(self):
        # Type 57 is the actual PhysicsJoint/ReplicationPV value layout. Decode
        # the encoded velocity fields through World.value and the wire Reader.
        sample = velocity((1.0, -1.0, 0.5))
        encoded = b'\x9f' + bytes(28) + sample + sample
        reader = Reader(encoded)
        world = World({'classes': [], 'strings': []})
        value = world.value(reader, {'name': 'ReplicationPV', 'type': 57}, set())
        reader.finish()
        self.assertEqual(value['position'], [0.0, 0.0, 0.0])
        self.assertFalse(value['position_present'])
        self.assertEqual(value['packed_velocities'], [[sample[0], int.from_bytes(sample[1:], 'little')]] * 2)

    def test_velocity_wire_value_rejects_truncated_physics_records(self):
        sample = velocity((1.0, -1.0, 0.5))
        encoded = b'\x9f' + bytes(28) + sample + sample
        world = World({'classes': [], 'strings': []})
        for size in range(len(encoded)):
            with self.subTest(size=size), self.assertRaises(WireError):
                world.value(Reader(encoded[:size]), {'name': 'ReplicationPV', 'type': 57}, set())

    def test_recovered_bounds_and_extreme_finite_components(self):
        for value in (-999_999_999.0, -16_384.0, -0.001, 0.001,
                      16_384.0, 999_999_999.0):
            with self.subTest(value=value):
                pos = position((value, -value, value / 3))
                vel = velocity((value, -value, value / 3))
                self.assertIn(len(pos), (5, 7, 9))
                self.assertIn(len(vel), (1, 5))
                reader = Reader(pos)
                result = World.compact_position(reader)
                reader.finish()
                self.assertTrue(all(math.isfinite(x) for x in result))


if __name__ == '__main__':
    unittest.main()
