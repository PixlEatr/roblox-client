"""Map authored track commands to the pinned ordinary-track update semantics.

Native evidence: 3-argument Play 10308C414, Stop 10308CCE4,
AdjustWeight 10308CECC, AdjustSpeed 10308D1B8, TimePosition 10308D408,
Priority 10308D5E8, Looped 10308D774, emitter 1030A1ED4.
This module prepares messages only; no transport or successful-playback claim.
"""
from copy import deepcopy
import re

from animation_intents import AnimationIntentOutbox
from animation_replication import _own_server_animator, encode_on_combined_update


class NativeAnimationUpdates:
    def __init__(self):
        self._world = None
        self._character = None
        self._tracks = {}
        self._sequence = 0

    def prepare(self, entries, *, world, user_id, capability_bits):
        # Validate the whole batch before advancing any command state.
        validated = AnimationIntentOutbox()
        validated.submit(entries)
        entries = validated.drain()
        if not entries:
            return []
        character = world.avatar(user_id).get('character_key')
        tracks = (deepcopy(self._tracks) if self._world is world
                  and self._character == character else {})
        messages = []
        checked_animators = set()
        sequence = self._sequence
        for entry in entries:
            if entry['sequence'] <= sequence:
                continue
            match = re.fullmatch(r'([1-9][0-9]*):([0-9]+)', entry['animator_id'])
            if match is None:
                raise ValueError('Track Animator is not a received network identity')
            animator_key = tuple(map(int, match.groups()))
            if animator_key not in checked_animators:
                _own_server_animator(world, user_id, animator_key)
                checked_animators.add(animator_key)
            identity = (entry['animator_id'], entry['track_id'])
            track = tracks.setdefault(identity, {'explicit_priority': False,
                'explicit_looped': False, 'played': False})
            kind = entry['kind']
            params = entry['parameters']
            state = params['effective_state']
            if kind == 'Priority':
                track['explicit_priority'] = True
            elif kind == 'Looped':
                track['explicit_looped'] = True
            sequence = entry['sequence']
            if kind == 'Stop' and not track['played']:
                continue
            if kind == 'AdjustWeight' and not state['playing']:
                continue
            fields = {'animation': entry['animation_reference'],
                'play_state': kind != 'Stop', 'fade_time': 0.,
                'weight': state['weight_target'], 'speed': state['speed'],
                'time_position': 0., 'priority': state['priority'],
                'looping': state['looped']}
            if kind == 'Play':
                fields.update(play_state=True, fade_time=params['fade_time'],
                    weight=params['weight'], speed=params['speed'],
                    priority=state['priority'] if track['explicit_priority'] else 1000,
                    values_updated=1 | (16 if track['explicit_priority'] else 0)
                                     | (64 if track['explicit_looped'] else 0))
                track['played'] = True
            elif kind == 'Stop':
                fields.update(play_state=False, fade_time=params['fade_time'],
                              priority=1000, values_updated=1)
            elif kind == 'AdjustWeight':
                fields.update(weight=params['weight'], fade_time=params['fade_time'],
                              values_updated=2)
            elif kind == 'AdjustSpeed':
                fields.update(speed=params['speed'], values_updated=4)
            elif kind == 'TimePosition':
                fields.update(time_position=params['position'], values_updated=8)
            elif kind == 'Priority':
                fields.update(play_state=True, values_updated=16)
            elif kind == 'Looped':
                fields.update(play_state=True, values_updated=64)
            payload = encode_on_combined_update(world, user_id, animator_key,
                capability_bits=capability_bits, **fields)
            messages.append({'operation': kind, 'mask': fields['values_updated'],
                             'payload': payload})
        if len(tracks) > 1024:
            raise ValueError('Animation producer track limit exceeded')
        self._world, self._character = world, character
        self._tracks, self._sequence = tracks, sequence
        return messages
