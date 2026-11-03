"""Contact transitions for submitted owned box poses against anchored listeners.

This is a bounded local overlap model, not a full physics solver. It never
accepts script-requested touch pairs. Native 104D53354 sends message 134 on
reliable channel 7: two scoped GUIDs, transition byte, then a null GUID.
1040D28E0 maps state 0 to Touched and state 3 to TouchEnded. Composite states
and the extra high-bit flag are not needed for one transition per step.
"""
import collections

from movement_collision import rotation_matrix
from owned_part_poses import _constructor_velocities, _vector
from physics_assemblies import _class_name
from physics_root_record import encode_instance_reference
from physics_rotation import native_property_selector_to_matrix


def encode_contacts(records):
    if not 1 <= len(records) <= 64:
        raise ValueError('Contact batch must contain 1..64 transitions')
    payload = bytearray(b'\x86')
    for first, second, began in records:
        if type(began) is not bool or first == second:
            raise ValueError('Invalid contact transition')
        for key in (first, second):
            if (not isinstance(key, tuple) or len(key) != 2
                    or any(type(v) is not int for v in key)
                    or not 1 <= key[0] < 0xffffffff or not 0 <= key[1] <= 0xffffffff):
                raise ValueError('Contacts require received scoped GUIDs')
            payload.extend(encode_instance_reference(key[0], key))
        payload.append(0 if began else 3)
    payload.append(0)
    return bytes(payload)


def boxes_overlap(first, second):
    """Exact separating-axis test for two rigid boxes, including edge axes."""
    pa, ra, sa = first
    pb, rb, sb = second
    for p, r, s in (first, second):
        if not _vector(p, 3) or not _vector(s, 3) or any(v <= 0 for v in s):
            raise ValueError('Invalid box')
        rotation_matrix(r)
    axes_a = [tuple(ra[i*3+j] for i in range(3)) for j in range(3)]
    axes_b = [tuple(rb[i*3+j] for i in range(3)) for j in range(3)]
    axes = axes_a + axes_b
    axes += [(a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
             for a in axes_a for b in axes_b]
    dot = lambda a, b: sum(x*y for x, y in zip(a, b))
    delta = [pb[i]-pa[i] for i in range(3)]
    for axis in axes:
        if dot(axis, axis) < 1e-20:
            continue
        radius = sum(sa[j]*.5*abs(dot(axis, axes_a[j]))
                     + sb[j]*.5*abs(dot(axis, axes_b[j])) for j in range(3))
        if abs(dot(delta, axis)) > radius:
            return False
    return True


def _field(world, key, name, aliases=()):
    node = world.instances[key]
    for candidate in (name, *aliases):
        if candidate in node['properties']:
            return node['properties'][candidate]
    creation = world.creation.get(key, {})
    # Both cached and ordinary complete new-instance records construct the
    # class and apply their supplied non-default properties. Updates/reused
    # instances do not provide that constructor provenance.
    if creation.get('fresh') and creation.get('complete'):
        value = _constructor_velocities().get(_class_name(world, node), {}).get(
            'properties', {}).get(name, {}).get('value')
        if value is not None:
            return value.get('$vector3', value) if isinstance(value, dict) else value
        # Documented engine defaults absent from the scriptable registry.
        if name == 'Shape' and _class_name(world, node) == 'Part':
            return 1  # Enum.PartType.Block
        if name == 'TouchesUseCollisionGroups' and _class_name(world, node) == 'Workspace':
            return False
    return None


def _workspace(world, key):
    seen = set()
    while key in world.instances and key not in seen:
        seen.add(key)
        node = world.instances[key]
        if _class_name(world, node) == 'Workspace':
            return key
        key = node.get('parent')
    return None


def _box(world, key, pose=None):
    node = world.instances.get(key)
    if node is None or _class_name(world, node) != 'Part':
        raise ValueError('unsupported_geometry')
    shape = _field(world, key, 'Shape', ('shape',))
    if type(shape) is not int or shape != 1:
        raise ValueError('unsupported_shape')
    if _field(world, key, 'CanTouch') is not True:
        raise ValueError('touch_disabled_or_unknown')
    size = _field(world, key, 'Size', ('size',))
    if not _vector(size, 3) or any(v <= 0 for v in size):
        raise ValueError('size_unknown')
    if pose is None:
        props = node['properties']
        names = [name for name in ('CFrame', 'ReplicationPV') if name in props]
        if not names:
            raise ValueError('pose_unknown')
        versions = world.property_versions.get(key, {})
        frame = props[max(names, key=lambda name: versions.get(name, 0))]
        position = frame.get('position')
        rotation = frame.get('rotation')
        if type(rotation) is int:
            rotation = native_property_selector_to_matrix(rotation)
        elif rotation is None:
            rotation = native_property_selector_to_matrix(frame.get('rotation_id'))
    else:
        position, rotation = pose[:2]
    if not _vector(position, 3):
        raise ValueError('pose_unknown')
    return position, rotation_matrix(rotation), size


class OwnedPartContacts:
    def __init__(self):
        _constructor_velocities()
        self.world = None
        self.active = set()
        self.packets = self.begins = self.ends = 0
        self.skipped = collections.Counter()
        self.error = None

    def observe_submitted(self, world, poses, send):
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('Contacts cannot cross world sessions')
        if self.error or not poses:
            return
        listeners = {node.get('parent') for node in world.instances.values()
                     if _class_name(world, node) == 'TouchTransmitter'}
        if len(listeners) > 4096:
            self.skipped['listener_budget'] += 1
            return
        transitions, updated = [], set(self.active)
        for key, pose in poses.items():
            old = {pair for pair in self.active if pair[0] == key}
            updated.difference_update(old)
            node = world.instances.get(key)
            workspace = _workspace(world, key)
            if (node is None or workspace is None
                    or type(world.client_scope) is not int or world.client_scope <= 0
                    or node['properties'].get('NetworkOwnerV3') != world.client_scope
                    or _field(world, key, 'Anchored') is not False
                    or _field(world, workspace, 'TouchesUseCollisionGroups') is not False):
                self.skipped['authority_or_workspace_unavailable'] += 1
                continue
            try:
                box = _box(world, key, pose)
            except (ValueError, TypeError, KeyError):
                self.skipped['source_geometry_unavailable'] += 1
                continue
            for other in sorted(listeners - {None, key}):
                if other not in world.instances or _workspace(world, other) != workspace:
                    continue
                if _field(world, other, 'Anchored') is not True:
                    continue  # Moving-vs-moving contacts need a shared simulation step.
                try:
                    target = _box(world, other)
                    overlap = boxes_overlap(box, target)
                except (ValueError, TypeError, KeyError):
                    self.skipped['listener_geometry_unavailable'] += 1
                    continue
                pair = (key, other)
                if overlap:
                    updated.add(pair)
                if overlap != (pair in old):
                    transitions.append((key, other, overlap))
        if len(transitions) > 64:
            self.skipped['transition_budget'] += 1
            return
        if transitions:
            payload = encode_contacts(transitions)
            try:
                result = send(7, payload, application=4)
            except Exception:
                self.error = 'transport_submission_ambiguous_no_retry'
                return
            if result is False:
                self.error = 'transport_submission_ambiguous_no_retry'
                return
            self.packets += 1
            self.begins += sum(began for _, _, began in transitions)
            self.ends += sum(not began for _, _, began in transitions)
        self.active = updated

    def report(self):
        return {'packets_submitted': self.packets, 'begins_submitted': self.begins,
                'ends_submitted': self.ends, 'active_pairs': len(self.active),
                'skipped': dict(self.skipped), 'error': self.error,
                'channel': 7, 'application': 4, 'server_acceptance_confirmed': False,
                'scope': 'submitted_owned_boxes_against_anchored_received_listeners'}
