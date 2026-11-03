"""Exact bodies for the Roblox 0.745 Animator track state value types.

These functions encode/decode only one custom property value body. They do
not include a property ID, instance reference, packet header, or transport.
The native sender writes the fields in the order below using explicit 1, 2,
4, and 8 byte raw copies; its reader uses the same fixed-size reads.

IDA evidence from outputs/ida/104dae900.asm/.c dispatches types 65/66/67 to
0x104F225A0, 0x104F224E8, and 0x104F2267C. Their readers are 0x104F2260C,
0x104F2253C, and 0x104F226E8. The scalar reads call 0x1015F8E60 with the
explicit byte counts; the scalar writes call 0x1015F9230 with the same counts.
The reference-step double fields are kept opaque: their relationship to the
simulation step has not been established by this codec. The native reader
normalizes every nonzero boolean byte to true; this decoder mirrors that
behavior. It additionally rejects nonfinite floats to protect JSON snapshots.
"""
import math
import struct


_PLAY_STATE = struct.Struct('<Bfd')
_METADATA = struct.Struct('<Bh')
_WEIGHT = struct.Struct('<ffd')


def _finite_number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def _decode(data, offset, layout, label):
    if not isinstance(data, bytes):
        raise TypeError(f'{label} data must be bytes')
    if type(offset) is not int or offset < 0:
        raise ValueError('offset must be a nonnegative integer')
    end = offset + layout.size
    if end > len(data):
        raise ValueError(f'truncated {label} value')
    values = layout.unpack_from(data, offset)
    if any(type(value) is float and not math.isfinite(value) for value in values):
        raise ValueError(f'nonfinite scalar in {label} value')
    return values, end


def encode_anim_track_play_state(*, playing, playback_speed,
                                 ref_step_id_for_time_position):
    """Encode type 65: bool byte, playbackSpeed f32, reference-step f64."""
    if type(playing) is not bool:
        raise ValueError('playing must be boolean')
    speed = _finite_number(playback_speed, 'playback_speed')
    step = _finite_number(ref_step_id_for_time_position,
                          'ref_step_id_for_time_position')
    try:
        return _PLAY_STATE.pack(playing, speed, step)
    except (OverflowError, struct.error) as error:
        raise ValueError('type 65 scalar is outside the wire range') from error


def decode_anim_track_play_state(data, offset=0):
    """Decode type 65 and return (field mapping, next byte offset)."""
    (playing, speed, reference_step), end = _decode(
        data, offset, _PLAY_STATE, 'animation play state')
    return {'playing': bool(playing), 'playback_speed': speed,
            'ref_step_id_for_time_position': reference_step}, end


def encode_anim_track_metadata(*, looped, priority):
    """Encode type 66: bool byte followed by the signed 16-bit priority."""
    if type(looped) is not bool:
        raise ValueError('looped must be boolean')
    if type(priority) is not int or not -32768 <= priority <= 32767:
        raise ValueError('priority must fit a signed 16-bit field')
    return _METADATA.pack(looped, priority)


def decode_anim_track_metadata(data, offset=0):
    """Decode type 66 and return (field mapping, next byte offset)."""
    (looped, priority), end = _decode(data, offset, _METADATA,
                                      'animation track metadata')
    return {'looped': bool(looped), 'priority': priority}, end


def encode_anim_track_weight(*, weight_target, weighted_fade_time,
                             ref_step_id_for_weight):
    """Encode type 67: weightTarget f32, fade time f32, reference-step f64."""
    weight = _finite_number(weight_target, 'weight_target')
    fade = _finite_number(weighted_fade_time, 'weighted_fade_time')
    step = _finite_number(ref_step_id_for_weight, 'ref_step_id_for_weight')
    try:
        return _WEIGHT.pack(weight, fade, step)
    except (OverflowError, struct.error) as error:
        raise ValueError('type 67 scalar is outside the wire range') from error


def decode_anim_track_weight(data, offset=0):
    """Decode type 67 and return (field mapping, next byte offset)."""
    (weight, fade, reference_step), end = _decode(
        data, offset, _WEIGHT, 'animation track weight')
    return {'weight_target': weight, 'weighted_fade_time': fade,
            'ref_step_id_for_weight': reference_step}, end


__all__ = [
    'encode_anim_track_play_state', 'decode_anim_track_play_state',
    'encode_anim_track_metadata', 'decode_anim_track_metadata',
    'encode_anim_track_weight', 'decode_anim_track_weight',
]
