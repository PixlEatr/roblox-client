"""Offline serializer for one schema-verified Animator.OnCombinedUpdate event.

This does not decide when an animation should be replicated or interpret the
native ``valuesUpdated`` mask. The mask is required from the caller and passed
through unchanged. This module does not send traffic.
"""
import math
import struct

from login_packets import encode_signed
from replication_out import guid
from game_wire import encode_unsigned


SUPPORTED_CAPABILITIES = 0x100000
EVENT_NAME = 'OnCombinedUpdate'
EVENT_ARGUMENT_TYPES = (33, 9, 11, 11, 11, 11, 7, 9, 10)
ANIMATION_PRIORITIES = frozenset((0, 1, 2, 3, 4, 5, 1000))
MAX_CONTENT_ID_BYTES = 16 * 1024 * 1024


def _class_name(world, instance):
    if not isinstance(instance, dict):
        return None
    class_id = instance.get('class')
    classes = world.schema.get('classes', [])
    if type(class_id) is not int or not 0 <= class_id < len(classes):
        return None
    return classes[class_id].get('name')


def _event_id(world, animator):
    class_id = animator['class']
    cls = world.schema['classes'][class_id]
    candidates = [i for i in cls.get('events', [])
                  if world.schema['events'][i]['name'] == EVENT_NAME]
    if len(candidates) != 1:
        raise ValueError('Animator.OnCombinedUpdate is missing or ambiguous')
    event_id = candidates[0]
    event = world.schema['events'][event_id]
    signature = tuple(arg['type'] for arg in event['arguments'])
    if (event.get('id') != event_id or event.get('class') != class_id
            or signature != EVENT_ARGUMENT_TYPES
            or event['arguments'][6].get('enum') != 37
            or len(world.schema.get('enums', [])) <= 37
            or world.schema['enums'][37].get('name') != 'AnimationPriority'):
        raise ValueError('Unsupported Animator.OnCombinedUpdate schema')
    if not world.schema.get('strings') or world.schema['strings'][0] != '':
        raise ValueError('Unsupported ContentId prefix table')
    return event_id


def _own_server_animator(world, user_id, animator_key):
    """Require a received Animator directly under the issued player's Humanoid."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError('A positive issued user ID is required')
    animator = world.instances.get(animator_key)
    if animator is None or _class_name(world, animator) != 'Animator':
        raise ValueError('Target is not a replicated Animator')
    creation = world.creation.get(animator_key, {})
    if creation.get('fresh') is not True or creation.get('complete') is not True:
        raise ValueError('Animator must have a complete server-received creation record')

    players = [(key, item) for key, item in world.instances.items()
               if _class_name(world, item) == 'Player'
               and item['properties'].get('UserId') == user_id]
    if len(players) != 1:
        raise ValueError('Issued player is missing or ambiguous')
    player_key, player = players[0]
    if _class_name(world, world.instances.get(player.get('parent'))) != 'Players':
        raise ValueError('Issued player is not parented to Players')
    character_key = player['properties'].get('Character')
    character = world.instances.get(character_key)
    if character is None or _class_name(world, character) != 'Model':
        raise ValueError('Issued player character is not replicated')
    humanoids = [(key, item) for key, item in world.instances.items()
                 if item.get('parent') == character_key
                 and _class_name(world, item) == 'Humanoid']
    if len(humanoids) != 1:
        raise ValueError('Player character Humanoid is missing or ambiguous')
    humanoid_key, _ = humanoids[0]
    if animator.get('parent') != humanoid_key:
        raise ValueError('Animator is not under the issued player Humanoid')
    return animator, player_key


def _finite_f32(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    try:
        return struct.pack('<f', float(value))
    except (OverflowError, struct.error) as error:
        raise ValueError(f'{name} is outside the float32 wire range') from error


def _content_id(value):
    if type(value) is not str or not value:
        raise ValueError('animation must be a nonempty ContentId string')
    try:
        data = value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError('animation ContentId is not valid UTF-8') from error
    if len(data) > MAX_CONTENT_ID_BYTES:
        raise ValueError('animation ContentId exceeds the native reader limit')
    # Compressed ContentId type 33, using schema prefix index zero (the empty
    # prefix), followed by the ordinary length-prefixed UTF-8 suffix.
    return b'\x00' + encode_unsigned(len(data)) + data


def encode_on_combined_update(world, user_id, animator_key, *, capability_bits,
                              animation, play_state, fade_time, weight, speed,
                              time_position, priority, looping, values_updated):
    """Build one item-7 replication payload; no transport or auto-send behavior.

    ``values_updated`` is a required opaque signed int32. No native bit meaning
    or event-production eligibility is inferred by this serializer.
    """
    if type(capability_bits) is not int or capability_bits != SUPPORTED_CAPABILITIES:
        raise ValueError('Unsupported replication capability set')
    if type(play_state) is not bool or type(looping) is not bool:
        raise ValueError('play_state and looping must be booleans')
    if type(priority) is not int or priority not in ANIMATION_PRIORITIES:
        raise ValueError('priority must be a known AnimationPriority enum value')
    if type(values_updated) is not int or not -(1 << 31) <= values_updated < (1 << 31):
        raise ValueError('values_updated must be an explicit signed int32')

    animator, _ = _own_server_animator(world, user_id, animator_key)
    event_id = _event_id(world, animator)
    arguments = (
        _content_id(animation),
        bytes((int(play_state),)),
        _finite_f32(fade_time, 'fade_time'),
        _finite_f32(weight, 'weight'),
        _finite_f32(speed, 'speed'),
        _finite_f32(time_position, 'time_position'),
        encode_unsigned(priority),
        bytes((int(looping),)),
        encode_signed(values_updated),
    )
    # Replication item 7: target GUID, LE16 schema event id, typed arguments.
    return b'\x83\x07' + guid(animator_key) + struct.pack('<H', event_id) + b''.join(arguments) + b'\x00'


__all__ = ['SUPPORTED_CAPABILITIES', 'encode_on_combined_update']
