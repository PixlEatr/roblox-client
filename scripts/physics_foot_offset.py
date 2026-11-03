"""Derive root-to-ground clearance from received and opted-in authored sizing.

Formula source: Roblox/creator-docs Humanoid.HipHeight, properties section:
https://raw.githubusercontent.com/Roblox/creator-docs/main/content/en-us/reference/engine/classes/Humanoid.yaml
"""
from __future__ import annotations

import math


DEFAULT_FOOT_OFFSET = 3.0
_SIZE_ORIGINS = {'received', 'derived_received'}


def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _vector3_y(value):
    if isinstance(value, dict):
        value = value.get('$vector3')
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(_finite_number(component) and component>0 for component in value):
        return None
    return float(value[1])


def _property(node, names, allowed_origins):
    properties = node.get('properties', {}) if isinstance(node, dict) else {}
    origins = node.get('origins', {}) if isinstance(node, dict) else {}
    if not isinstance(properties, dict) or not isinstance(origins, dict):
        return None
    for name in names:
        if name in properties and origins.get(name) in allowed_origins:
            return properties[name]
    return None


def _locally_sized(node):
    origins=node.get('origins',{}) if isinstance(node,dict) else {}
    return isinstance(origins,dict) and any(origins.get(key)=='local' for key in ('Size','size'))


def _root_parts(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('nodes'), list):
        return None, None, None
    nodes = {node.get('id'): node for node in snapshot['nodes']
             if isinstance(node, dict) and node.get('id') is not None}
    player = nodes.get(snapshot.get('local_player'))
    player_properties = player.get('properties', {}) if isinstance(player, dict) else {}
    character_ref = player_properties.get('Character') if isinstance(player_properties, dict) else None
    character_id = character_ref.get('$ref') if isinstance(character_ref, dict) else None
    character = nodes.get(character_id)
    if not isinstance(character, dict) or character.get('class_name') != 'Model':
        return None, None, None
    children = [node for node in nodes.values() if node.get('parent') == character_id]
    humanoid = next((node for node in children if node.get('class_name') == 'Humanoid'), None)
    root = next((node for node in children if node.get('name') == 'HumanoidRootPart'), None)
    return humanoid, root, nodes


def derive_foot_offset(snapshot, fallback=DEFAULT_FOOT_OFFSET):
    """Return ``(offset, origin)`` from proven rig identity and current sizing.

    Roblox's R15 root center height is half the root's Y size plus HipHeight.
    R6 adds the current LeftLeg Y size. Authored sizing requires the prediction
    overlay's explicit opt-in; raw received views never trust local provenance.
    If any required field or provenance
    is absent, malformed, non-finite, or produces a negative offset,
    retain the configured fallback and label it as such.
    """
    if not _finite_number(fallback) or fallback < 0:
        raise ValueError('fallback foot offset must be finite and non-negative')
    humanoid, root, nodes = _root_parts(snapshot)
    authored=isinstance(snapshot,dict) and snapshot.get('allow_authored_motion') is True
    values_origins={'received','local'} if authored else {'received'}
    size_origins=_SIZE_ORIGINS|({'local'} if authored else set())
    reason = 'missing_received_rig_size'
    if humanoid is not None and root is not None:
        hprops = humanoid.get('properties', {})
        horigins = humanoid.get('origins', {})
        if not isinstance(hprops, dict):
            hprops = {}
        if not isinstance(horigins, dict):
            horigins = {}
        rig_type = hprops.get('RigType') if horigins.get('RigType') == 'received' else None
        if isinstance(rig_type,dict) and isinstance(rig_type.get('$enum'),dict):
            item=rig_type['$enum'];rig_type=item.get('value') if item.get('type')=='HumanoidRigType' else None
        hip_height = hprops.get('HipHeight') if horigins.get('HipHeight') in values_origins else None
        root_size_value = _property(root, ('Size', 'size'), size_origins)
        root_size_y = _vector3_y(root_size_value)
        local_size=authored and (horigins.get('HipHeight')=='local' or _locally_sized(root))
        if (type(rig_type) is not int or rig_type not in (0, 1)
                or not _finite_number(hip_height) or root_size_y is None or root_size_y <= 0):
            reason = 'missing_received_rig_size'
        elif rig_type == 1:
            value = root_size_y / 2.0 + float(hip_height)
            reason = 'r15_'+('authored' if local_size else 'received')+'_root_size_and_hip_height'
            if _valid_offset(value):
                return value, reason
            reason = 'invalid_received_rig_size'
        else:
            left_ref = _property(humanoid, ('LeftLeg',), values_origins)
            left_id = left_ref.get('$ref') if isinstance(left_ref, dict) else None
            left_leg = nodes.get(left_id) if left_id is not None else None
            left_size_value = _property(left_leg, ('Size', 'size'), size_origins)
            left_size_y = _vector3_y(left_size_value)
            if (left_size_y is None or left_size_y <= 0
                    or left_leg.get('destroyed') is True
                    or left_leg.get('parent') != humanoid.get('parent')):
                reason = 'missing_received_r6_left_leg_size'
            else:
                value = left_size_y + root_size_y / 2.0 + float(hip_height)
                local_size=local_size or authored and (horigins.get('LeftLeg')=='local' or _locally_sized(left_leg))
                reason = 'r6_'+('authored' if local_size else 'received')+'_root_left_leg_size_and_hip_height'
                if _valid_offset(value):
                    return value, reason
                reason = 'invalid_received_rig_size'
    return float(fallback), f'fallback_default_{fallback:g}_{reason}'


def _valid_offset(value):
    return math.isfinite(value) and value >= 0.0
