"""Resolve a Move direction using the camera captured by the Luau worker.

Only ground walking is modeled: transform into world space and project onto
XZ while retaining horizontal input strength. Camera translation has no effect.
No current or received camera is substituted for missing call-time metadata.
"""
import math
from movement_collision import rotation_matrix
from movement_controller import _vector3


def _camera_direction(intent):
    if intent.get('kind') != 'Move':
        raise ValueError('camera_transform_unavailable')
    try:
        vector = _vector3(intent.get('vector'), 'direction')
        if any(abs(v) > 1e6 for v in vector):
            raise ValueError()
    except ValueError:
        raise ValueError('invalid_movement_direction') from None
    # Zero is invariant under rotation. Stopping needs no invented orientation
    # even when the camera's received rotation is unavailable.
    if not any(vector):
        return vector
    camera = intent.get('camera')
    if not isinstance(camera, dict) or not camera:
        raise ValueError('camera_transform_unavailable')
    if camera.get('error') == 'camera_transform_invalid':
        raise ValueError('camera_transform_invalid')
    if (not isinstance(camera.get('instance_id'), str) or not camera['instance_id']
            or camera.get('origin') not in ('local', 'class_default', 'received', 'derived_received')):
        raise ValueError('camera_transform_unavailable')
    try:
        rotation = rotation_matrix(camera.get('rotation'))
    except ValueError:
        raise ValueError('camera_transform_invalid') from None
    world = tuple(sum(rotation[3*i+j]*vector[j] for j in range(3)) for i in range(3))
    # A nearly vertical direction has no usable horizontal walking direction.
    # Do not normalize numerical residue from a 90-degree CFrame to full speed.
    horizontal = math.hypot(world[0], world[2])
    if horizontal <= 1e-6*math.hypot(*world):
        return (0., 0., 0.)
    # Pitch changes the ground direction, not the thumbstick's strength.
    strength = min(1., math.hypot(vector[0], vector[2]))
    return (world[0]/horizontal*strength, 0., world[2]/horizontal*strength)


def camera_movement_error(intent):
    """Return an explicit rejection reason, or None for a usable request."""
    if not intent.get('relative_to_camera', False):
        return None
    try:
        _camera_direction(intent)
    except ValueError as error:
        return str(error)
    return None


def movement_direction(intent):
    """Return world-space walking input without mutating the intention."""
    return _camera_direction(intent) if intent.get('relative_to_camera', False) else intent.get('vector')
