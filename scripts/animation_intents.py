"""Bounded ordered outbox for genuine Luau AnimationTrack operations.

No wire messages are inferred here. The session validates the current received
Animator and chooses its verified serializer when consuming these operations.
"""
from collections import deque
import math

from animation_assets import animation_asset_id


_PARAMETERS = {
    'Play': {'fade_time', 'weight', 'speed'},
    'Stop': {'fade_time'}, 'AdjustSpeed': {'speed'},
    'AdjustWeight': {'weight', 'fade_time'}, 'TimePosition': {'position'},
    'Looped': {'looped'}, 'Priority': {'priority'},
}
_STATE_FIELDS = {'playing', 'priority', 'looped', 'time_position', 'speed',
                 'weight_current', 'weight_target'}


def _validate_numbers(parameters):
    for key, value in parameters.items():
        if key in ('looped', 'playing'):
            if type(value) is not bool:
                raise ValueError('Animation state flags must be boolean')
        elif key == 'priority':
            if type(value) is not int or value not in (0, 1, 2, 3, 4, 5, 1000):
                raise ValueError('Unknown animation priority')
        elif (type(value) not in (int, float) or not math.isfinite(value)
              or abs(value) > 1e6 or key != 'speed' and value < 0):
            raise ValueError('Animation parameter must be finite and in range')


class AnimationIntentOutbox:
    MAX_PENDING = 1024

    def __init__(self):
        self._pending = deque()
        self.last_sequence = 0
        self.accepted = 0
        self.duplicates = 0

    def submit(self, entries):
        if not isinstance(entries, list) or len(entries) > self.MAX_PENDING:
            raise ValueError('Animation intent batch exceeds its limit')
        prepared = []
        watermark = self.last_sequence
        duplicates = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Animation intent must be an object')
            sequence = entry.get('sequence')
            if type(sequence) is not int or not 0 < sequence < 2**64:
                raise ValueError('Animation intent sequence must be a positive uint64')
            if sequence <= self.last_sequence:
                duplicates += 1
                continue
            if sequence <= watermark:
                raise ValueError('Animation intent batch is out of order')
            kind = entry.get('kind')
            if kind not in _PARAMETERS:
                raise ValueError('Unknown animation intent kind')
            for key in ('animator_id', 'track_id'):
                value = entry.get(key)
                if not isinstance(value, str) or not 0 < len(value) <= 256:
                    raise ValueError('Animation intent target is invalid')
            reference = 'rbxassetid://' + animation_asset_id(entry.get('animation_reference'))
            parameters = entry.get('parameters')
            if (not isinstance(parameters, dict)
                    or set(parameters) != _PARAMETERS[kind] | {'effective_state'}):
                raise ValueError('Animation intent parameters do not match its operation')
            state = parameters['effective_state']
            if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
                raise ValueError('Animation intent has invalid effective state')
            _validate_numbers(state)
            _validate_numbers({k: v for k, v in parameters.items() if k != 'effective_state'})
            prepared.append({'sequence': sequence, 'kind': kind,
                             'animator_id': entry['animator_id'], 'track_id': entry['track_id'],
                             'animation_reference': reference,
                             'parameters': {**parameters, 'effective_state': dict(state)}})
            watermark = sequence
        if len(self._pending) + len(prepared) > self.MAX_PENDING:
            raise ValueError('Animation outbox is full')
        self._pending.extend(prepared)
        self.last_sequence = watermark
        self.accepted += len(prepared)
        self.duplicates += duplicates

    def drain(self):
        entries = list(self._pending)
        self._pending.clear()
        return entries

    def report(self):
        return {'pending': len(self._pending), 'accepted': self.accepted,
                'duplicate_sequences': self.duplicates}
