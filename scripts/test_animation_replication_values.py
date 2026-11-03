import struct
import unittest

from test_world_snapshot import fixture
from wire_reader import Reader, WireError


class AnimationReplicationValueTests(unittest.TestCase):
    def test_schema_values_read_native_field_order_without_consuming_next_value(self):
        world, _ = fixture()
        examples = [
            (65, struct.pack('<Bfd', 1, 1.5, 123.25),
             {'anim_track_play_state': {'playing': True, 'playback_speed': 1.5,
                                       'ref_step_id_for_time_position': 123.25}}),
            (66, struct.pack('<Bh', 1, 1000),
             {'anim_track_metadata': {'looped': True, 'priority': 1000}}),
            (67, struct.pack('<ffd', .75, .125, 100.5),
             {'anim_track_weight': {'weight_target': .75, 'weighted_fade_time': .125,
                                   'ref_step_id_for_weight': 100.5}}),
        ]
        for kind, body, expected in examples:
            with self.subTest(kind=kind):
                reader = Reader(body + b'\x7f')
                self.assertEqual(world.value(reader, {'type': kind}, set()), expected)
                self.assertEqual(reader.u8(), 127)
                reader.finish()
                for size in range(len(body)):
                    with self.assertRaises(WireError):
                        world.value(Reader(body[:size]), {'type': kind}, set())


if __name__ == '__main__':
    unittest.main()
