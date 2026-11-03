"""Small deterministic local walking integrator.

This models an authored movement intent for offline/runtime development. It
does not implement Roblox character physics or send movement to a server.
"""
import math


MAX_WALK_SPEED = 128.0
MAX_STEP_SECONDS = 0.25


def _vector3(value, label):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f'{label} must contain three finite numbers')
    result = []
    for component in value:
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            raise ValueError(f'{label} must contain three finite numbers')
        component = float(component)
        if not math.isfinite(component):
            raise ValueError(f'{label} must contain three finite numbers')
        result.append(component)
    return tuple(result)


class Controller:
    """Integrates bounded horizontal input at a configurable maximum speed."""

    def __init__(self, position=(0.0, 0.0, 0.0), walk_speed=16.0):
        self._position = _vector3(position, 'position')
        self._walk_speed = self._speed(walk_speed)
        self._direction = (0.0, 0.0, 0.0)

    @staticmethod
    def _speed(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('walk_speed must be finite and between 0 and 128')
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= MAX_WALK_SPEED:
            raise ValueError('walk_speed must be finite and between 0 and 128')
        return value

    @property
    def position(self):
        return self._position

    def resolve_position(self, position):
        """Accept a collision-resolved local pose without altering movement intent."""
        self._position = _vector3(position, 'position')

    @property
    def walk_speed(self):
        return self._walk_speed

    @walk_speed.setter
    def walk_speed(self, value):
        self._walk_speed = self._speed(value)

    def set_direction(self, direction, *, normalize=False):
        """Preserve partial input, cap diagonals, or aim at full speed for MoveTo."""
        if type(normalize) is not bool:
            raise ValueError('normalize must be a boolean')
        x, _, z = _vector3(direction, 'direction')
        scale = max(abs(x), abs(z))
        if scale == 0.0:
            self._direction = (0.0, 0.0, 0.0)
        elif not normalize and scale <= 1.0 and math.hypot(x, z) <= 1.0:
            self._direction = (x, 0.0, z)
        else:
            # Scaling first avoids overflow for very large finite directions.
            x, z = x / scale, z / scale
            length = math.hypot(x, z)
            self._direction = (x / length, 0.0, z / length)

    def stop(self):
        self._direction = (0.0, 0.0, 0.0)

    def step(self, dt):
        """Advance by dt seconds; reject oversized steps to avoid hidden leaps."""
        if isinstance(dt, bool) or not isinstance(dt, (int, float)):
            raise ValueError('dt must be finite and between 0 and 0.25 seconds')
        dt = float(dt)
        if not math.isfinite(dt) or not 0.0 <= dt <= MAX_STEP_SECONDS:
            raise ValueError('dt must be finite and between 0 and 0.25 seconds')
        dx, _, dz = self._direction
        speed = self._walk_speed
        velocity = (dx * speed, 0.0, dz * speed)
        x, y, z = self._position
        self._position = (x + velocity[0] * dt, y, z + velocity[2] * dt)
        return {
            'position': self._position,
            'velocity': velocity,
            'moving': speed > 0.0 and (dx != 0.0 or dz != 0.0),
        }
