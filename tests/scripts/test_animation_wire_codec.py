import struct
import unittest

from animation_wire_codec import (
    decode_anim_track_metadata, decode_anim_track_play_state,
    decode_anim_track_weight, encode_anim_track_metadata,
    encode_anim_track_play_state, encode_anim_track_weight,
)


class AnimationWireCodecTests(unittest.TestCase):
    def test_type_65_field_order_sizes_and_stream_offset(self):
        body = encode_anim_track_play_state(
            playing=True, playback_speed=1.5,
            ref_step_id_for_time_position=-2.25)
        self.assertEqual(body, b'\x01' + struct.pack('<fd', 1.5, -2.25))
        self.assertEqual(len(body), 13)
        value, end = decode_anim_track_play_state(b'abc' + body + b'z', 3)
        self.assertEqual(value, {'playing': True, 'playback_speed': 1.5,
                                 'ref_step_id_for_time_position': -2.25})
        self.assertEqual(end, 16)

    def test_type_65_native_reader_normalizes_any_nonzero_bool(self):
        value, end = decode_anim_track_play_state(b'\x02' + struct.pack('<fd', 0., 0.))
        self.assertIs(value['playing'], True)
        self.assertEqual(end, 13)

    def test_type_66_wire_order_is_looped_then_signed_priority(self):
        body = encode_anim_track_metadata(looped=False, priority=1000)
        self.assertEqual(body, b'\x00\xe8\x03')
        self.assertEqual(len(body), 3)
        self.assertEqual(decode_anim_track_metadata(body),
                         ({'looped': False, 'priority': 1000}, 3))
        self.assertEqual(decode_anim_track_metadata(b'\x01\xff\xff')[0],
                         {'looped': True, 'priority': -1})

    def test_type_67_wire_order_is_two_float32_then_reference_float64(self):
        body = encode_anim_track_weight(weight_target=1.25,
                                        weighted_fade_time=2.5,
                                        ref_step_id_for_weight=.5)
        self.assertEqual(body, struct.pack('<ffd', 1.25, 2.5, .5))
        self.assertEqual(len(body), 16)
        self.assertEqual(decode_anim_track_weight(body),
                         ({'weight_target': 1.25, 'weighted_fade_time': 2.5,
                           'ref_step_id_for_weight': .5}, 16))

    def test_truncation_and_encoder_ranges_fail_cleanly(self):
        for decoder, data in (
            (decode_anim_track_play_state, b'\0' * 12),
            (decode_anim_track_metadata, b'\0' * 2),
            (decode_anim_track_weight, b'\0' * 15),
        ):
            with self.subTest(data_length=len(data)), self.assertRaises(ValueError):
                decoder(data)
        with self.assertRaises(ValueError):
            encode_anim_track_metadata(looped=1, priority=2)
        with self.assertRaises(ValueError):
            encode_anim_track_metadata(looped=False, priority=32768)
        with self.assertRaises(ValueError):
            encode_anim_track_play_state(playing=False, playback_speed=float('nan'),
                                         ref_step_id_for_time_position=0.)
        with self.assertRaises(ValueError):
            decode_anim_track_weight(b'\0' * 16, True)

    def test_nonfinite_values_are_rejected_on_decode(self):
        cases = (
            (decode_anim_track_play_state, b'\x00' + struct.pack('<f', float('inf')) + b'\0' * 8),
            (decode_anim_track_play_state, b'\x00' + struct.pack('<f', 0.) + struct.pack('<d', float('nan'))),
            (decode_anim_track_weight, struct.pack('<f', float('-inf')) + b'\0' * 12),
            (decode_anim_track_weight, struct.pack('<ff', 0., 0.) + struct.pack('<d', float('inf'))),
        )
        for decoder, data in cases:
            with self.subTest(decoder=decoder.__name__), self.assertRaises(ValueError):
                decoder(data)


if __name__ == '__main__':
    unittest.main()
