"""Narrow client-originated replication messages for the logged-in player."""
import struct
from collections import deque

from game_wire import encode_unsigned


def marker_reply(payload):
    """Acknowledge a server-issued replication marker, without changing it.

    Native receive 104dda14c creates MarkerItem; 104d8bf34 serializes item 4
    with the original LE32 marker. This is a queue-order acknowledgment.
    """
    if len(payload) != 5 or payload[0] != 132:
        raise ValueError('Invalid replication marker request')
    return b'\x83\x04' + payload[1:] + b'\0'


class MarkerQueue:
    """Queue marker replies until the initial world has been consumed.

    Native 104dda14c queues MarkerItem rather than sending it in the receive
    handler; 104d8bf34 checks terrain readiness before writing it. Tag 13 is
    a conservative barrier for this data-only prototype, not an implementation
    of every native terrain-readiness condition.
    """
    def __init__(self):
        self.pending = deque()

    def add(self, payload):
        reply = marker_reply(payload)
        if len(self.pending) >= 64:
            raise ValueError('Too many pending replication markers')
        self.pending.append(reply)

    def drain(self, world):
        if world is None or world.client_scope is None or 13 not in world.tags:
            return []
        replies = list(self.pending)
        self.pending.clear()
        return replies


def guid(key):
    if key is None: return b'\0'
    scope, index = key
    if not 1 <= scope <= 0xffffffff or not 0 <= index <= 0xffffffff:
        raise ValueError('Invalid instance reference')
    return encode_unsigned(scope) + struct.pack('<I', index)


def streaming_ready(world, user_id):
    avatar = world.avatar(user_id)
    if not avatar['evidence']['player_spawned'] or not avatar['evidence']['spawn_region_received']:
        raise ValueError('Character and surrounding region must be received before resuming')
    player = avatar['player']
    cls = world.schema['classes'][player['class']]
    prop = next(world.schema['properties'][i] for i in cls['properties']
                if world.schema['properties'][i]['name'] == 'GameplayPaused')
    if prop['type'] != 9: raise ValueError('Unexpected pause property type')
    # Native GCJob 104e5e398 calls the GameplayPaused setter 100abadc0.
    # Capability set 0x100000 uses the unversioned changed-property layout.
    return b'\x83\x03' + guid(avatar['player_key']) + struct.pack('<H', prop['id']) + b'\0\0\0'
