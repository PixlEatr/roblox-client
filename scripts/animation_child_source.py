"""Data-only builder for experimental dependent animated child body records.

This composes already-computed own-avatar joint poses with received
AnimationConstraint attachments. It does not choose network ownership, mutate
the replicated World, encode/send a packet, or establish that Roblox accepts a
kinematic child hierarchy. The caller must explicitly choose frame, origin
suffix, velocity codec, and supply a per-actor pose history.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from animation_rig_pose import (_attachment_part, _enabled, _frame, _mul,
                                _ref, _transpose)
from physics_history import CurrentRecord
from physics_packet import TransformHeader
from physics_root_record import ClientChildBody, encode_instance_reference
from physics_rotation import RotationField, native_matrix3x3_to_quaternion


class AnimationChildSourceError(ValueError):
    """Invalid or incomplete own-character dependent-body source data."""


@dataclass
class ChildPoseHistory:
    """Caller-owned finite-difference cache; first sample has zero velocities.

    A change of World object, own user, or root identity clears old samples, so
    respawns and reconnect replays cannot produce velocities across actors.
    """

    _actor: tuple[Any, int, Any] | None = None
    _frames: dict[Any, tuple[float, tuple[float, ...], tuple[float, ...]]] = field(default_factory=dict)

    def _bind_actor(self, world, user_id, root_key):
        actor = (world, user_id, root_key)
        if (self._actor is None or self._actor[0] is not world
                or self._actor[1:] != (user_id, root_key)):
            self._actor = actor
            self._frames.clear()

    def reset(self):
        self._actor = None
        self._frames.clear()


def _class_name(world, node):
    try:
        return world.schema['classes'][node['class']]['name']
    except (KeyError, IndexError, TypeError):
        return None


def _body_frame(position, rotation, *, offset=(0., 0., 0.)):
    rotated = tuple(sum(rotation[row * 3 + col] * offset[col] for col in range(3))
                    for row in range(3))
    return (tuple(position[i] + rotated[i] for i in range(3)), tuple(rotation))


def _pose(value, label):
    """Validate one FK result shaped as ``(position3, rotation9)``."""
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise AnimationChildSourceError(f'{label}: pose must contain position and rotation')
    position, rotation = value
    if (not isinstance(position, (tuple, list)) or len(position) != 3
            or not isinstance(rotation, (tuple, list)) or len(rotation) != 9
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in tuple(position) + tuple(rotation))):
        raise AnimationChildSourceError(f'{label}: pose values must be finite')
    rotation = tuple(float(v) for v in rotation)
    for row in range(3):
        for other in range(3):
            dot = sum(rotation[row * 3 + i] * rotation[other * 3 + i] for i in range(3))
            if abs(dot - (1. if row == other else 0.)) > 2e-3:
                raise AnimationChildSourceError(f'{label}: rotation must be rigid')
    det = (rotation[0] * (rotation[4] * rotation[8] - rotation[5] * rotation[7])
           - rotation[1] * (rotation[3] * rotation[8] - rotation[5] * rotation[6])
           + rotation[2] * (rotation[3] * rotation[7] - rotation[4] * rotation[6]))
    if abs(det - 1.) > 2e-3:
        raise AnimationChildSourceError(f'{label}: rotation must preserve handedness')
    return tuple(float(v) for v in position), rotation


def _angular_velocity(previous, current, dt):
    # World-space delta: R_now * transpose(R_previous).
    delta = _mul(current, _transpose(previous))
    q = native_matrix3x3_to_quaternion(delta)
    norm = math.sqrt(sum(v * v for v in q))
    if norm <= 1e-12 or not math.isfinite(norm):
        raise AnimationChildSourceError('rotation delta is invalid')
    x, y, z, w = (v / norm for v in q)
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    w = max(-1., min(1., w))
    half_angle = math.acos(w)
    sine = math.sin(half_angle)
    if abs(sine) < 1e-9:
        return (2. * x / dt, 2. * y / dt, 2. * z / dt)
    scale = 2. * half_angle / (sine * dt)
    return (x * scale, y * scale, z * scale)


def build_kinematic_child_records(
    world,
    user_id: int,
    root_key,
    posed_parts,
    *,
    context_tag: int,
    origin_index: int,
    velocity_mode: str,
    body_frame: str,
    now: float,
    history: ChildPoseHistory,
):
    """Build child record tree from complete received kinematic joints.

    ``posed_parts`` must be the FK result's ``poses`` mapping, keyed by actual
    decoded World instance keys. ``history`` is caller-owned and required: the
    first valid sample emits zero velocities, later samples use finite
    differences of the selected world body frames. No guessed fallback is
    used for context tag, origin suffix, precision, body-frame convention, or
    clock.

    ``body_frame='part'`` uses each FK part CFrame. ``'joint_pivot'`` composes
    the actual received ``JointOffset`` translation, but only when the child
    has ``HasJointOffset is True`` and it exactly equals incoming
    Attachment1.CFrame.position. That equality is observed input validation,
    not evidence that this is the native outbound transform convention.

    Returns ``(ordered ClientChildBody roots, structural_report)``. The report
    labels the hierarchy experimental and explicitly makes no server-acceptance
    or ownership claim.
    """
    if type(user_id) is not int or user_id <= 0:
        raise AnimationChildSourceError('own user identity is invalid')
    if type(context_tag) is not int or not 0 <= context_tag <= 0xFFFFFFFF:
        raise AnimationChildSourceError('context_tag must be an explicit u32')
    if type(origin_index) is not int or not 0 <= origin_index <= 255:
        raise AnimationChildSourceError('origin_index must be an explicit byte')
    if velocity_mode not in ('compact', 'short'):
        raise AnimationChildSourceError('velocity_mode must be explicit compact or short')
    if body_frame not in ('part', 'joint_pivot'):
        raise AnimationChildSourceError('body_frame must be part or joint_pivot')
    if type(now) not in (int, float) or not math.isfinite(now):
        raise AnimationChildSourceError('now must be a finite caller-supplied clock')
    if not isinstance(history, ChildPoseHistory):
        raise AnimationChildSourceError('caller-owned ChildPoseHistory is required')
    if not isinstance(posed_parts, dict):
        raise AnimationChildSourceError('posed_parts must be the FK instance-key mapping')

    try:
        avatar = world.avatar(user_id)
    except Exception as exc:
        raise AnimationChildSourceError('could not resolve own character') from exc
    if not avatar.get('evidence', {}).get('player_spawned'):
        raise AnimationChildSourceError('own character is not currently spawned')
    character_key = avatar.get('character_key')
    if root_key != avatar.get('root_key') or root_key not in world.instances:
        raise AnimationChildSourceError('root_key must be the current own-character root')
    if _class_name(world, world.instances[root_key]) not in ('Part', 'MeshPart'):
        raise AnimationChildSourceError('own root is not a received body part')

    # Compile the exact current character subtree. This builder deliberately
    # performs a bounded pass each call so it never relies on stale topology.
    subtree = set()
    pending = [character_key]
    while pending:
        key = pending.pop()
        if key in subtree:
            raise AnimationChildSourceError('own character subtree contains a cycle')
        node = world.instances.get(key)
        if node is None:
            raise AnimationChildSourceError('own character subtree is incomplete')
        subtree.add(key)
        pending.extend(child for child, child_node in world.instances.items()
                       if child_node.get('parent') == key)
    body_parts = {key for key in subtree if _class_name(world, world.instances[key]) in ('Part', 'MeshPart')}
    if root_key not in body_parts:
        raise AnimationChildSourceError('own root is outside the current character body')

    constraints = [(key, node) for key, node in world.instances.items()
                   if key in subtree and _class_name(world, node) == 'AnimationConstraint']
    if len(constraints) != 15:
        raise AnimationChildSourceError('complete 15-edge AnimationConstraint graph is required')

    edges = []
    edge_pairs = set()
    for key, node in constraints:
        props = node.get('properties', {})
        if not _enabled(world, key, node):
            raise AnimationChildSourceError('every selected AnimationConstraint must be enabled')
        if props.get('IsKinematic') is not True:
            raise AnimationChildSourceError('every selected AnimationConstraint must be explicitly kinematic')
        a0, a1 = _ref(props.get('Attachment0')), _ref(props.get('Attachment1'))
        if a0 not in subtree or a1 not in subtree:
            raise AnimationChildSourceError('joint attachments must resolve inside own character')
        node0, node1 = world.instances.get(a0), world.instances.get(a1)
        if node0 is None or node1 is None or _class_name(world, node0) != 'Attachment' or _class_name(world, node1) != 'Attachment':
            raise AnimationChildSourceError('joint endpoints must be received Attachments')
        try:
            parent_part = _attachment_part(world, a0, character_key)
            child_part = _attachment_part(world, a1, character_key)
        except Exception as exc:
            raise AnimationChildSourceError('joint endpoint ancestry is invalid') from exc
        if parent_part not in body_parts or child_part not in body_parts or parent_part == child_part:
            raise AnimationChildSourceError('joint must connect two distinct own body parts')
        at0, at1 = node0.get('properties', {}).get('CFrame'), node1.get('properties', {}).get('CFrame')
        if at0 is None or at1 is None:
            raise AnimationChildSourceError('both received attachment CFrames are required')
        try:
            frame0 = _frame(at0, 'attachment0')
            frame1 = _frame(at1, 'attachment1')
        except Exception as exc:
            raise AnimationChildSourceError('joint attachment CFrame is invalid') from exc
        pair = (parent_part, child_part)
        if pair in edge_pairs:
            raise AnimationChildSourceError('joint graph has an ambiguous duplicate edge')
        edge_pairs.add(pair)
        edges.append((parent_part, child_part, frame0, frame1, node1, child_part))

    # A strict arborescence: exactly one incoming edge for each non-root body,
    # none for root, and every edge reachable from this root.
    incoming, adjacency = {}, {}
    for edge in edges:
        parent_part, child_part = edge[:2]
        if child_part in incoming:
            raise AnimationChildSourceError('joint graph has an ambiguous parent')
        incoming[child_part] = edge
        adjacency.setdefault(parent_part, []).append(edge)
    graph_parts = {root_key, *(part for edge in edges for part in edge[:2])}
    if set(incoming) != graph_parts - {root_key}:
        raise AnimationChildSourceError('joint graph does not cover the complete own body set')
    ordered_edges, visited = [], {root_key}
    queue = [root_key]
    while queue:
        parent_part = queue.pop(0)
        for edge in adjacency.get(parent_part, ()):
            child_part = edge[1]
            if child_part in visited:
                raise AnimationChildSourceError('joint graph contains a cycle')
            visited.add(child_part)
            ordered_edges.append(edge)
            queue.append(child_part)
    if visited != graph_parts or len(ordered_edges) != 15:
        raise AnimationChildSourceError('joint graph is not fully rooted at own HumanoidRootPart')

    if any(key not in subtree for key in posed_parts):
        raise AnimationChildSourceError('posed_parts contains a foreign instance')
    if set(posed_parts) != graph_parts:
        raise AnimationChildSourceError('posed_parts must cover exactly the own rooted body graph')
    parsed = {key: _pose(value, 'body pose') for key, value in posed_parts.items()}
    body_frames = {root_key: parsed[root_key]}
    for _, child_part, _, attachment1, attachment_node, _ in ordered_edges:
        position, rotation = parsed[child_part]
        if body_frame == 'part':
            body_frames[child_part] = (position, rotation)
            continue
        props = world.instances[child_part].get('properties', {})
        if props.get('HasJointOffset') is not True:
            raise AnimationChildSourceError('joint_pivot requires received HasJointOffset true')
        offset = props.get('JointOffset')
        incoming_position = attachment1[0]
        if (not isinstance(offset, (tuple, list)) or len(offset) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in offset)
                or tuple(offset) != tuple(incoming_position)):
            raise AnimationChildSourceError('JointOffset must exactly match incoming Attachment1 position')
        body_frames[child_part] = _body_frame(position, rotation, offset=offset)

    history._bind_actor(world, user_id, root_key)
    first_sample = not history._frames
    if history._frames and now <= max(frame[0] for frame in history._frames.values()):
        raise AnimationChildSourceError('now must advance beyond the previous body sample')
    velocities, rotations = {}, {}
    for key, (position, rotation) in body_frames.items():
        previous = history._frames.get(key)
        if previous is None:
            velocities[key] = ((0., 0., 0.), (0., 0., 0.))
        else:
            old_time, old_position, old_rotation = previous
            dt = now - old_time
            velocities[key] = (
                tuple((position[i] - old_position[i]) / dt for i in range(3)),
                _angular_velocity(old_rotation, rotation, dt),
            )
        rotations[key] = RotationField(0, 'native-quaternion',
                                      native_matrix3x3_to_quaternion(rotation))

    object_refs = {}
    for key in graph_parts - {root_key}:
        try:
            object_refs[key] = encode_instance_reference(context_tag, key)
        except (TypeError, ValueError, OverflowError) as exc:
            raise AnimationChildSourceError('could not encode an actual own-body instance reference') from exc

    records = {}
    for edge in ordered_edges:
        _, child_part, *_ = edge
        position, rotation = body_frames[child_part]
        zero_position = all(component == 0. for component in position)
        raw = 0x80 if zero_position else 0
        header = TransformHeader(0, zero_position, False, False, raw)
        linear, angular = velocities[child_part]
        current = CurrentRecord(
            header, position, rotations[child_part], linear, angular,
            velocity_mode, state=origin_index,
        )
        records[child_part] = ClientChildBody(object_refs[child_part], current)
    mutable_children = {key: [] for key in graph_parts}
    for parent_part, child_part, *_ in ordered_edges:
        mutable_children[parent_part].append(child_part)
    for parent_part in reversed([root_key] + [edge[1] for edge in ordered_edges]):
        for child_part in mutable_children[parent_part]:
            records[child_part] = ClientChildBody(
                object_refs[child_part], records[child_part].current_record,
                motor_records=(), children=tuple(records[k] for k in mutable_children[child_part]),
            )
    top = tuple(records[key] for key in mutable_children[root_key])

    # Commit only after all validation and record construction succeeds.
    history._frames = {key: (float(now), *body_frames[key]) for key in graph_parts}
    report = {
        'status': 'ok',
        'body_count': len(graph_parts),
        'child_body_count': len(graph_parts) - 1,
        'constraint_edge_count': len(ordered_edges),
        'selected_constraints_enabled': True,
        'selected_constraints_kinematic': True,
        'origin_index': origin_index,
        'velocity_mode': velocity_mode,
        'body_frame': body_frame,
        'body_frame_source': 'caller_supplied_fk_world_cframe',
        'physics_pv_basis_verified': False,
        'first_sample_zero_velocity': first_sample,
        'local_dependent_pose_model': True,
        'native_hierarchy_compatibility_verified': False,
        'server_animation_confirmed': False,
        'network_authority_changed': False,
        'world_mutated': False,
        'body_frames': dict(body_frames),
        'linear_velocities': {key: value[0] for key, value in velocities.items()},
        'angular_velocities': {key: value[1] for key, value in velocities.items()},
    }
    return top, report


__all__ = ['AnimationChildSourceError', 'ChildPoseHistory',
           'build_kinematic_child_records']
