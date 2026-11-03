"""Conservative structural assembly inspection for an already-decoded World.

This module only reads the local replicated model. Joint connectivity is not
evidence of simulation authority, and the result deliberately leaves ownership
unknown.
"""
import json
import math
from pathlib import Path


_PARENTS_PATH = Path(__file__).resolve().parents[1] / 'runtime/class-parents-745.json'
_RIGID_CLASSES = {'Weld', 'Snap', 'ManualWeld', 'Motor', 'Motor6D', 'WeldConstraint'}


def _class_name(world, instance):
    if instance is None:
        return None
    ci = instance.get('class')
    classes = world.schema.get('classes', [])
    return classes[ci].get('name') if isinstance(ci, int) and 0 <= ci < len(classes) else None


def _parents(world):
    supplied = getattr(world, 'class_parents', None)
    if isinstance(supplied, dict):
        return supplied
    try:
        return json.loads(_PARENTS_PATH.read_text())['parents']
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _is_a(name, target, parents):
    seen = set()
    while isinstance(name, str) and name not in seen:
        if name == target:
            return True
        seen.add(name)
        name = parents.get(name)
    return False


def _name(instance, fallback=''):
    value = (instance or {}).get('properties', {}).get('Name', fallback)
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value if isinstance(value, str) else fallback


def _descendant_keys(world, root, allowed=None):
    out = set()
    if root is None:
        return out
    children = {}
    for key, item in world.instances.items():
        parent = item.get('parent')
        if _reference(parent):
            children.setdefault(parent, []).append(key)
    pending = [root]
    seen = {root}
    while pending:
        parent = pending.pop()
        for key in children.get(parent, ()):
            if key in seen:
                continue
            seen.add(key)
            out.add(key)
            pending.append(key)
    return out


def _reference(value):
    return (isinstance(value, tuple) and len(value) == 2
            and all(type(component) is int for component in value))


def _cframe_components(value):
    if not isinstance(value, dict):
        raise ValueError('CFrame must be a decoded dictionary')
    position, rotation = value.get('position'), value.get('rotation')
    if (not isinstance(position, (list, tuple)) or len(position) != 3
            or not isinstance(rotation, (list, tuple)) or len(rotation) != 9):
        raise ValueError('CFrame position and row-major rotation are unavailable')
    values = tuple(position) + tuple(rotation)
    if any(type(component) not in (int, float) or not math.isfinite(component)
           for component in values):
        raise ValueError('CFrame components must be finite numbers')
    return tuple(float(component) for component in position), tuple(
        float(component) for component in rotation)


def relative_cframe(reference, target):
    """Return ``target`` in ``reference`` object space.

    This is the native/reference CFrame composition ``reference:Inverse() *
    target``: transpose the reference rotation, rotate the position delta, and
    multiply the rotation matrices. Opaque rotation selectors are not guessed.
    """
    reference_position, reference_rotation = _cframe_components(reference)
    target_position, target_rotation = _cframe_components(target)
    inverse_rotation = tuple(reference_rotation[3 * column + row]
                             for row in range(3) for column in range(3))
    delta = tuple(target_position[index] - reference_position[index]
                  for index in range(3))
    position = [sum(inverse_rotation[3 * row + column] * delta[column]
                    for column in range(3)) for row in range(3)]
    rotation = [sum(inverse_rotation[3 * row + index] * target_rotation[3 * index + column]
                    for index in range(3))
                for row in range(3) for column in range(3)]
    return {'position': position, 'rotation': rotation}


def resolve_own_assembly(world, user_id):
    """Return structural evidence and private keys for the selected player's assembly.

    A root comes from the received Player.Character/Model.PrimaryPart reference;
    only when that reference is unavailable does a unique direct child named
    HumanoidRootPart qualify as a fallback. Edges require received, resolved
    Part0/Part1 references and explicit Enabled state on rigid joint classes.
    Disabled and explicitly inactive joints do not connect parts. This is
    structural evidence, not simulation activation or ownership evidence.
    """
    parents = _parents(world)
    avatar = world.avatar(user_id)
    character_key = avatar.get('character_key')
    character = world.instances.get(character_key) if _reference(character_key) else None
    character_ok = _class_name(world, character) == 'Model'
    descendants = _descendant_keys(world, character_key) if character_ok else set()

    root_key = avatar.get('root_key')
    root = world.instances.get(root_key) if _reference(root_key) else None
    root_source = 'primary_part' if root is not None else None
    if (root is None or root_key not in descendants
            or not _is_a(_class_name(world, root), 'BasePart', parents)):
        candidates = [key for key in descendants
                      if world.instances[key].get('parent') == character_key
                      if _name(world.instances[key]) == 'HumanoidRootPart'
                      and _is_a(_class_name(world, world.instances[key]), 'BasePart', parents)]
        if len(candidates) == 1:
            root_key, root = candidates[0], world.instances[candidates[0]]
            root_source = 'unique_named_child'
        else:
            root_key, root, root_source = None, None, None

    part_keys = {key for key, item in world.instances.items()
                 if _is_a(_class_name(world, item), 'BasePart', parents)}
    adjacency = {key: set() for key in part_keys}
    records = []
    for joint_key, joint in world.instances.items():
        joint_class = _class_name(world, joint)
        if not any(_is_a(joint_class, name, parents) for name in _RIGID_CLASSES):
            continue
        properties = joint.get('properties', {})
        p0_present, p1_present = 'Part0' in properties, 'Part1' in properties
        p0, p1 = properties.get('Part0'), properties.get('Part1')
        reasons = []
        inactive = (properties.get('Enabled') is False or properties.get('Active') is False
                    or (p0_present and p0 is None) or (p1_present and p1 is None))
        if not inactive:
            if 'Enabled' not in properties:
                reasons.append('Enabled_not_received')
            elif type(properties['Enabled']) is not bool:
                reasons.append('Enabled_invalid')
            if 'Active' in properties and type(properties['Active']) is not bool:
                reasons.append('Active_invalid')
        for label, is_present, value in (('Part0', p0_present, p0), ('Part1', p1_present, p1)):
            if not is_present:
                reasons.append(label + '_not_received')
            elif value is None:
                reasons.append(label + '_null')
            elif not _reference(value):
                reasons.append(label + '_invalid_reference')
            elif value not in world.instances:
                reasons.append(label + '_unresolved_or_streamed_out')
            elif value not in part_keys:
                reasons.append(label + '_not_basepart')
        records.append((joint_key, joint_class, p0, p1, inactive, reasons))
        if not inactive and not reasons and p0 != p1:
            adjacency[p0].add(p1)
            adjacency[p1].add(p0)

    connected = set()
    if root_key in adjacency:
        pending = [root_key]
        while pending:
            current = pending.pop()
            if current in connected:
                continue
            connected.add(current)
            pending.extend(adjacency[current] - connected)

    # Resolve the whole component first: a character can be welded to an
    # external chain whose later joints do not directly reference the character.
    relevant_parts = descendants | connected
    missing = []
    examined = inactive_count = 0
    for joint_key, joint_class, p0, p1, inactive, reasons in records:
        relevant = (joint_key in descendants
                    or (_reference(p0) and p0 in relevant_parts)
                    or (_reference(p1) and p1 in relevant_parts))
        if not relevant:
            continue
        examined += 1
        if inactive:
            inactive_count += 1
        elif reasons:
            missing.append({'joint_class': joint_class, 'reasons': reasons})

    uncertain = bool(missing) or root_key is None or not character_ok
    return {
        'evidence': {
            'own_player_resolved': avatar.get('player_key') is not None,
            'character_model_resolved': character_ok,
            'root_resolved': root_key is not None,
            'root_source': root_source,
            'connected_part_count': len(connected),
            'recognized_joint_count': examined,
            'inactive_joint_count': inactive_count,
            'rigid_connectivity_only': True,
            'simulation_activity': 'unknown',
            'incomplete_or_unresolved_joints': len(missing),
            'streaming_or_missing_data_possible': bool(missing),
            'network_ownership': 'unknown',
            'assembly_complete': not uncertain,
            'structural_connectivity_complete': not uncertain,
        },
        'root_key': root_key,
        'character_key': character_key,
        'connected_part_keys': sorted(connected),
        'unresolved_joints': missing,
    }
