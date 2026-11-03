"""Compose explicit native Animator slot values without choosing slot/time policy.

These are the four schema-typed value bodies and the existing unversioned
changed-property framing. Caller-supplied reference steps remain opaque; this
module does not infer native allocation or simulation-clock conversion.
"""
import struct

from animation_replication import SUPPORTED_CAPABILITIES, _own_server_animator
from animation_wire_codec import (encode_anim_track_play_state,
    encode_anim_track_metadata, encode_anim_track_weight)
from login_packets import encode_signed
from replication_out import guid


def encode_animator_slot(world, user_id, animator_key, *, capability_bits,
                         slot, animation_id, playing, playback_speed, looped,
                         priority, weight_target, weighted_fade_time,
                         ref_step_id_for_time_position, ref_step_id_for_weight):
    if type(capability_bits) is not int or capability_bits != SUPPORTED_CAPABILITIES:
        raise ValueError('Unsupported animation property capability set')
    if type(slot) is not int or not 0 <= slot < 16:
        raise ValueError('Animator slot must be an explicit integer from 0 to 15')
    if type(animation_id) is not int or not 0 <= animation_id < 2**63:
        raise ValueError('Animation asset identity must be a nonnegative int64')
    if type(priority) is not int or priority not in (0, 1, 2, 3, 4, 5, 1000):
        raise ValueError('Unknown animation priority')
    animator, _ = _own_server_animator(world, user_id, animator_key)
    class_id = animator['class']
    body_values = [
        (f'AnimationId{slot}', 44, encode_signed(animation_id)),
        (f'AnimTrackMetadata{slot}', 66,
         encode_anim_track_metadata(looped=looped, priority=priority)),
        (f'AnimTrackWeight{slot}', 67,
         encode_anim_track_weight(weight_target=weight_target,
             weighted_fade_time=weighted_fade_time, ref_step_id_for_weight=ref_step_id_for_weight)),
        (f'AnimTrackPlayState{slot}', 65,
         encode_anim_track_play_state(playing=playing, playback_speed=playback_speed,
             ref_step_id_for_time_position=ref_step_id_for_time_position)),
    ]
    pieces = [b'\x83']
    for name, kind, body in body_values:
        candidates = [i for i in world.schema['classes'][class_id]['properties']
                      if world.schema['properties'][i]['name'] == name]
        if len(candidates) != 1:
            raise ValueError('Animator slot property is missing or ambiguous')
        index = candidates[0]
        prop = world.schema['properties'][index]
        if (type(index) is not int or not 0 <= index <= 65535
                or prop.get('id') != index or prop.get('class') != class_id
                or prop.get('type') != kind):
            raise ValueError('Animator slot property schema does not match native layout')
        pieces.append(b'\x03' + guid(animator_key) + struct.pack('<H', index) + b'\x00' + body)
    return b''.join(pieces) + b'\x00'


__all__ = ['encode_animator_slot']
