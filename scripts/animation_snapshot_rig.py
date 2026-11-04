"""Evaluate detached local rig geometry, including canonical model scaling.

Snapshots already contain scaled C0/C1 and attachment frames. Only sampled
animation translations receive the canonical factor during playback.
"""
import math
from collections import defaultdict

from animation_rig_pose import ReceivedAvatarRig, RigPoseError, _frame, _inverse, _name


_PARTS = frozenset(('Part', 'MeshPart', 'UnionOperation', 'WedgePart',
    'CornerWedgePart', 'TrussPart', 'Seat', 'VehicleSeat', 'SpawnLocation'))


def _ref(value):
    return value.get('$ref') if isinstance(value, dict) else None


def scaled_animation_sample(sample, scale):
    """Return fresh poses; never scale rotations, weights, or cached assets."""
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise RigPoseError('invalid_animation_model_scale')
    poses = {}
    for path, pose in sample.get('poses', {}).items():
        position, rotation = _frame(pose, 'pose')
        position = [v * scale for v in position]
        if not all(math.isfinite(v) for v in position):
            raise RigPoseError('scaled_animation_translation_nonfinite')
        poses[path] = {'position':position,'rotation':list(rotation),'weight':pose.get('weight',1.)}
    return {**sample, 'poses': poses}


class SnapshotAnimationRig(ReceivedAvatarRig):
    """Compile Motor6D/attachment bindings without mutating received state.

    Reuses the existing root-pinned FK evaluator. This is an immutable per-view
    rig; callers replace it when the detached snapshot changes.
    """
    def __init__(self, snapshot, character_id, root_id, animator_id, *, enabled_defaults=()):
        self.world = None
        self.user_id = None
        self.character_key = character_id
        self.root_key = root_id
        self.valid = False
        self.error = 'rig_snapshot_invalid'
        self.binds = {}
        self.part_by_name = defaultdict(list)
        self.scale = 1.
        nodes = {n['id']: n for n in snapshot['nodes']}
        try:
            def ancestry(identifier):
                seen = set()
                while identifier is not None:
                    if identifier in seen:
                        raise RigPoseError('world_ancestry_cycle')
                    seen.add(identifier)
                    node = nodes.get(identifier)
                    if node is None:
                        break
                    yield node
                    identifier = node.get('parent')

            actor = nodes.get(animator_id)
            if actor is None or actor.get('class_name') != 'Animator':
                raise RigPoseError('animator_unavailable')
            ancestors = list(ancestry(animator_id))
            if character_id not in {n['id'] for n in ancestors}:
                raise RigPoseError('animator_outside_character')
            model = next((n for n in ancestors if n.get('class_name') == 'Model'), None)
            if model is None:
                raise RigPoseError('animation_model_unavailable')
            self.scale = model.get('properties', {}).get('Scale', 1.)
            scaled_animation_sample({'poses': {}}, self.scale)
            children = defaultdict(list)
            for identifier, node in nodes.items():
                children[node.get('parent')].append(identifier)
            self.subtree, pending = set(), [character_id]
            while pending:
                identifier = pending.pop()
                if identifier in self.subtree:
                    raise RigPoseError('world_ancestry_cycle')
                if identifier not in nodes:
                    raise RigPoseError('character_node_missing')
                self.subtree.add(identifier)
                pending.extend(children.get(identifier, ()))
            if root_id not in self.subtree or nodes[root_id].get('class_name') not in _PARTS:
                raise RigPoseError('root_outside_character')
            for identifier in self.subtree:
                node = nodes[identifier]
                if node.get('class_name') in _PARTS:
                    self.part_by_name[_name(node.get('name', ''))].append(identifier)

            def attachment(identifier):
                node = nodes.get(identifier)
                if identifier not in self.subtree or node.get('class_name') != 'Attachment':
                    raise RigPoseError('attachment_endpoint_unresolved')
                parent = node.get('parent')
                if parent not in self.subtree or nodes[parent].get('class_name') not in _PARTS:
                    raise RigPoseError('attachment_parent_unresolved')
                return parent, _frame(node.get('properties', {}).get('CFrame'), 'attachment')

            components = {}
            def find(part):
                components.setdefault(part, part)
                while components[part] != part:
                    components[part] = components[components[part]]
                    part = components[part]
                return part

            for identifier in self.subtree:
                node = nodes[identifier]
                kind, props = node.get('class_name'), node.get('properties', {})
                if kind not in ('Motor6D', 'AnimationConstraint'):
                    continue
                enabled = props.get('Enabled', identifier in enabled_defaults)
                if enabled is not True:
                    continue
                if kind == 'Motor6D':
                    p0, p1 = _ref(props.get('Part0')), _ref(props.get('Part1'))
                    if p0 not in self.subtree or p1 not in self.subtree:
                        continue
                    if any(nodes[p].get('class_name') not in _PARTS for p in (p0, p1)):
                        raise RigPoseError('joint_endpoint_not_part')
                    frame0, frame1 = _frame(props.get('C0'), 'C0'), _frame(props.get('C1'), 'C1')
                else:
                    p0, frame0 = attachment(_ref(props.get('Attachment0')))
                    p1, frame1 = attachment(_ref(props.get('Attachment1')))
                pair = frozenset((p0, p1))
                if pair in self.binds:
                    raise RigPoseError('ambiguous_animation_joint')
                r0, r1 = find(p0), find(p1)
                if r0 == r1:
                    raise RigPoseError('animation_joint_topology_cycle')
                components[r1] = r0
                self.binds[pair] = (p0, p1, frame0, _inverse(frame1))
            self.valid, self.error = True, 'ok'
        except RigPoseError as error:
            self.error = str(error)

    def valid_for(self, world, user_id):
        return self.valid

    def evaluate(self, root_cframe, pose_sample):
        try:
            sample = scaled_animation_sample(pose_sample, self.scale)
        except RigPoseError as error:
            return {'status': str(error), 'poses': {}, 'resolved_bodyparts': 0}
        result = super().evaluate(root_cframe, sample)
        result['animation_model_scale'] = self.scale
        if result['status'] == 'ok':
            result['joint_transforms'] = sample['poses']
        return result
