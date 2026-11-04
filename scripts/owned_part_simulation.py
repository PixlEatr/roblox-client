"""Session-local gravity state for a bounded set of owned isolated block Parts.

This optional model is separate from received replication. It models anchored
block contacts with an inelastic, frictionless response, not native solver
parity. A bounded BodyVelocity proportional-force model is supported; other
force types, dynamic pairs, and assemblies are not simulated. Unknown geometry
is reported. It never supplies integrity reports or changes game test flags.
"""
from collections import Counter
from dataclasses import dataclass, replace
import math
import struct

from collision_groups import context_from_world, part_group
from owned_part_contacts import _field, _workspace
from owned_part_dynamics import step_owned_part
from owned_part_poses import _constructor_velocities, _rotation, _vector, _velocities
from physics_assemblies import _class_name, _is_a, _parents
from physics_ground import GroundGeometry, OBB
from physics_forces import velocity_servo_values
from physics_rotation import native_property_selector_to_matrix, native_compact_selector_to_matrix
from remote_datatypes import remote_cframe
from remote_client_rpc import parse_key


POSE_FIELDS = ('CFrame', 'ReplicationPV', 'Parent', 'NetworkOwnerV3', 'Anchored',
               'PhysicsRepRootPart', 'PhysicsRepRootRef')
LOCAL_FIELDS = {'Anchored', 'CanCollide', 'Size', 'Shape', 'CollisionGroup', 'Parent',
                'AssemblyLinearVelocity', 'AssemblyAngularVelocity', 'Material',
                'MaterialVariant', 'CustomPhysicalProperties', 'P', 'MaxForce', 'Velocity'}


@dataclass
class Body:
    position: tuple
    rotation: tuple
    linear: tuple
    angular: tuple
    revision: int
    last_at: float
    velocity_source: str
    simulated_seconds: float = 0.


@dataclass(frozen=True)
class UnmodeledBounds:
    center: tuple
    radius: float

    def intersects(self, lower, upper):
        # Center-distance form retains a useful far-away bound even when a
        # float32-sized center is too large for center +/- small size in f64.
        return all(abs(self.center[i]-(lower[i]+upper[i])*.5)
                   <= self.radius+(upper[i]-lower[i])*.5 for i in range(3))


def _overlaps(lower, upper, other_minimum, other_maximum):
    """Exact inclusive AABB overlap test, also used after index lookup."""
    return all(other_maximum[i] >= lower[i] and other_minimum[i] <= upper[i]
               for i in range(3))


class _BoundsIndex:
    """Conservative per-root uniform grid for immutable broad-phase AABBs."""
    CELL_SIZE = 64.
    MAX_CELLS = 256

    def __init__(self):
        self.entries = {}
        self.cells = {}
        self.root_tokens = {}
        self.root_wide = {}

    def clear(self):
        self.entries.clear()
        self.cells.clear()
        self.root_tokens.clear()
        self.root_wide.clear()

    def remove(self, token):
        entry = self.entries.pop(token, None)
        if entry is None:
            return
        root, _, _, _, _, _, cell_keys = entry
        self.root_tokens.get(root, set()).discard(token)
        self.root_wide.get(root, set()).discard(token)
        for cell in cell_keys or ():
            bucket = self.cells.get((root, cell))
            if bucket is not None:
                bucket.discard(token)
                if not bucket:
                    del self.cells[(root, cell)]

    @classmethod
    def _cell_keys(cls, lower, upper):
        if lower is None or upper is None:
            return None
        if (len(lower) != 3 or len(upper) != 3
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in tuple(lower)+tuple(upper))
                or any(lower[i] >= upper[i] for i in range(3))):
            return None
        lo = tuple(math.floor(value/cls.CELL_SIZE) for value in lower)
        hi = tuple(math.floor(value/cls.CELL_SIZE) for value in upper)
        span = tuple(hi[i]-lo[i]+1 for i in range(3))
        count = 1
        for length in span:
            if length <= 0 or length > cls.MAX_CELLS//count:
                return None
            count *= length
        return tuple((x, y, z)
                     for x in range(lo[0], hi[0]+1)
                     for y in range(lo[1], hi[1]+1)
                     for z in range(lo[2], hi[2]+1))

    def upsert(self, token, root, payload, lower, upper, order):
        self.remove(token)
        cell_keys = self._cell_keys(lower, upper)
        entry = (root, token, payload, lower, upper, order, cell_keys)
        self.entries[token] = entry
        self.root_tokens.setdefault(root, set()).add(token)
        if cell_keys is None:
            self.root_wide.setdefault(root, set()).add(token)
        else:
            for cell in cell_keys:
                self.cells.setdefault((root, cell), set()).add(token)

    def query(self, root, lower, upper):
        query_cells = self._cell_keys(lower, upper)
        if query_cells is None:
            tokens = set(self.root_tokens.get(root, ()))
        else:
            tokens = set(self.root_wide.get(root, ()))
            for cell in query_cells:
                tokens.update(self.cells.get((root, cell), ()))
        entries = [self.entries[token] for token in tokens if token in self.entries]
        entries.sort(key=lambda entry: entry[5])
        return [(entry[1], entry[2], entry[3], entry[4]) for entry in entries]


def _received_pose(world, key):
    props = world.instances[key]['properties']
    versions = world.property_versions.get(key, {})
    names = [name for name in ('CFrame', 'ReplicationPV') if name in props]
    if not names:
        creation = world.creation.get(key, {})
        if not all(creation.get(flag) for flag in ('fresh', 'complete', 'ordinary')):
            raise ValueError('received_pose_unavailable')
        frame = _constructor_velocities().get(_class_name(world, world.instances[key]), {}).get(
            'properties', {}).get('CFrame', {}).get('value', {}).get('$cframe')
        if frame is None:
            raise ValueError('constructor_pose_unavailable')
        name = None
    else:
        name = max(names, key=lambda name: versions.get(name, 0))
        frame = props[name]
    position, rotation = frame.get('position'), frame.get('rotation')
    if 'rotation_id' in frame:
        rotation = remote_cframe(frame)['$cframe']['rotation']
    elif type(rotation) is str:
        rotation = remote_cframe({'rotation_id': 0, 'packed_rotation': rotation,
                                  'position': position})['$cframe']['rotation']
    elif type(rotation) is int:
        rotation = (native_compact_selector_to_matrix(rotation) if name == 'ReplicationPV'
                    else native_property_selector_to_matrix(rotation))
    elif rotation is None:
        rotation = native_property_selector_to_matrix(frame.get('rotation_id'))
    if not _vector(position, 3) or not _rotation(rotation):
        raise ValueError('received_pose_unavailable')
    return tuple(position), tuple(rotation)


def _connected(world):
    parents = _parents(world)
    keys = set()
    for node in world.instances.values():
        kind = _class_name(world, node)
        if _is_a(kind, 'JointInstance', parents) or kind == 'WeldConstraint':
            keys.update(node['properties'].get(name) for name in ('Part0', 'Part1'))
        if _is_a(kind, 'Constraint', parents):
            for name in ('Attachment0', 'Attachment1'):
                attachment = world.instances.get(node['properties'].get(name))
                if attachment is not None:
                    keys.add(attachment.get('parent'))
        if _is_a(kind, 'BodyMover', parents) and kind != 'BodyVelocity':
            keys.add(node.get('parent'))
    return keys


class OwnedPartSimulation:
    MAX_BODIES = 32

    def __init__(self):
        self.world = None
        self.bodies = {}
        self.local = {}
        self.geometry = GroundGeometry(())
        self.last_rebuild = None
        self.last_tick = None
        self.dirty_feedback = False
        self.connected = set()
        self.characters = set()
        self.controllers = {}
        self.blockers = []
        self.dynamic_keys = []
        self.disabled_reason = None
        self.scene_revision = None
        self.collision_contexts = {}
        self.collider_index = _BoundsIndex()
        self.blocker_index = _BoundsIndex()
        self.static_geometry_dirty = set()
        self.skipped = Counter()
        self.steps = self.contacts = 0
        self.discarded_wall_seconds = 0.

    def _scene_changed(self, world):
        if self.scene_revision is None or self.last_rebuild is None:
            return True
        actions = world.actions
        if actions and self.scene_revision < actions[0]['sequence']-1:
            return True
        for action in reversed(actions):
            if action['sequence'] <= self.scene_revision:
                break
            if action['kind'] in ('create', 'remove'):
                return True
            names = set(action.get('fields', {}))
            if names & {'Parent', 'Part0', 'Part1', 'Attachment0', 'Attachment1', 'NetworkOwnerV3',
                        'Character', 'Enabled', 'Active', 'PhysicsRepRootPart', 'PhysicsRepRootRef',
                        'Anchored', 'CanCollide', 'Shape', 'shape', 'Size', 'size',
                        'CollisionGroup', 'CollisionGroupReplicate', 'CollisionGroupData', 'CollisionGroups'}:
                return True
            if names & {'CFrame', 'ReplicationPV'}:
                key = action['key']
                if key in world.instances and self.field(world, key, 'Anchored') is True:
                    self.static_geometry_dirty.add(key)
        self.scene_revision = world.revision
        return False

    def _pose(self, world, key):
        entry = self.local.get((key, 'CFrame'))
        if entry is not None:
            revision, value = entry
            if max(world.property_versions.get(key, {}).get(n, 0) for n in ('CFrame', 'ReplicationPV')) <= revision:
                return tuple(value['position']), tuple(value['rotation'])
            del self.local[(key, 'CFrame')]
        return _received_pose(world, key)

    def _collision_context(self, world, key):
        if key not in self.collision_contexts:
            context = context_from_world(world, self.geometry, key, key)
            context = replace(context, groups=tuple((other, self.group(world, other))
                              for other, _ in context.groups))
            self.collision_contexts[key] = (context, context.allowed_sources(self.group(world, key)))
        return self.collision_contexts[key]

    def _rebuild_bounds_indices(self, world):
        self.collider_index.clear()
        self.blocker_index.clear()
        for order, collider in enumerate(self.geometry.colliders):
            self.collider_index.upsert(collider.source, _workspace(world, collider.source),
                                      collider, collider.minimum, collider.maximum, order)
        for order, (root, bounds, key) in enumerate(self.blockers):
            if bounds is None:
                lower = upper = None
            elif isinstance(bounds, UnmodeledBounds):
                lower = tuple(bounds.center[i]-bounds.radius for i in range(3))
                upper = tuple(bounds.center[i]+bounds.radius for i in range(3))
            else:
                lower, upper = bounds.minimum, bounds.maximum
            self.blocker_index.upsert(key, root, (bounds, key), lower, upper, order)

    def _refresh_bounds_index_key(self, world, key):
        self.collider_index.remove(key)
        self.blocker_index.remove(key)
        for collider in self.geometry.colliders:
            if collider.source == key:
                order = max((entry[5] for entry in self.collider_index.entries.values()), default=-1)+1
                self.collider_index.upsert(key, _workspace(world, key), collider,
                                           collider.minimum, collider.maximum, order)
                break
        for root, bounds, blocker_key in self.blockers:
            if blocker_key == key:
                if bounds is None:
                    lower = upper = None
                elif isinstance(bounds, UnmodeledBounds):
                    lower = tuple(bounds.center[i]-bounds.radius for i in range(3))
                    upper = tuple(bounds.center[i]+bounds.radius for i in range(3))
                else:
                    lower, upper = bounds.minimum, bounds.maximum
                order = max((entry[5] for entry in self.blocker_index.entries.values()), default=-1)+1
                self.blocker_index.upsert(key, root, (bounds, key), lower, upper, order)
                break

    def _refresh_static_geometry_key(self, world, key):
        """Replace one static Part's collision entry after a pose-only change."""
        prior_sources = {item.source for item in self.geometry.colliders}
        colliders = [item for item in self.geometry.colliders if item.source != key]
        unsupported = [item for item in self.geometry.unsupported if item[0] != key]
        blockers = [item for item in self.blockers if item[2] != key]
        node = world.instances.get(key)
        if (node is not None and self.field(world, key, 'Anchored') is True
                and self.field(world, key, 'CanCollide') is True):
            try:
                if (_class_name(world, node) != 'Part'
                        or self.field(world, key, 'Shape', ('shape',)) != 1):
                    raise ValueError('static_shape_unsupported')
                size = self.field(world, key, 'Size', ('size',))
                if not _vector(size, 3) or any(value <= 0 for value in size):
                    raise ValueError('static_size_unavailable')
                if (key, 'Parent') in self.local:
                    raise ValueError('local_static_parent_unsupported')
                position, rotation = self._pose(world, key)
                colliders.append(OBB(position, tuple(value*.5 for value in size), rotation, key))
            except (ValueError, TypeError, KeyError):
                unsupported.append((key, 'static_shape_or_pose_unavailable'))
                bounds = None
                try:
                    size = self.field(world, key, 'Size', ('size',))
                    position, rotation = self._pose(world, key)
                    bounds = OBB(position, tuple(value*.5 for value in size), rotation, key)
                except (ValueError, TypeError, KeyError):
                    try:
                        size = self.field(world, key, 'Size', ('size',))
                        props = node['properties']
                        versions = world.property_versions.get(key, {})
                        name = max((n for n in ('CFrame', 'ReplicationPV') if n in props),
                                   key=lambda n: versions.get(n, 0))
                        position = props[name]['position']
                        if (not _vector(size, 3) or any(value <= 0 for value in size)
                                or not isinstance(position, (list, tuple)) or len(position) != 3
                                or any(type(value) not in (int, float) or not math.isfinite(value)
                                       for value in position)):
                            raise ValueError('unknown bounds')
                        bounds = UnmodeledBounds(tuple(position), math.hypot(*size)*.5)
                    except (ValueError, TypeError, KeyError):
                        pass
                blockers.append((_workspace(world, key), bounds, key))
        self.geometry = GroundGeometry(tuple(colliders), tuple(unsupported))
        self.blockers = blockers
        if prior_sources != {item.source for item in self.geometry.colliders}:
            # Cached group projections depend on which collider sources exist;
            # their positions are read from geometry separately on each step.
            self.collision_contexts.clear()
        self._refresh_bounds_index_key(world, key)

    def _refresh_dirty_static_geometry(self, world):
        if not self.static_geometry_dirty:
            return
        dirty, self.static_geometry_dirty = self.static_geometry_dirty, set()
        for key in dirty:
            self._refresh_static_geometry_key(world, key)

    def _servo_state(self, world, key):
        sources = [velocity_servo_values(self.field(world, child, 'Velocity'),
                    self.field(world, child, 'MaxForce'), self.field(world, child, 'P'))
                   for child in self.controllers.get(key, ())]
        if not sources:
            return (), None
        props = world.instances[key]['properties']
        explicit = props.get('Mass')
        if type(explicit) in (int, float) and math.isfinite(explicit) and explicit > 0 and not any(
                (key, name) in self.local for name in ('Size', 'Material', 'CustomPhysicalProperties')):
            return sources, float(explicit)
        # An ordinary fresh default block has a measured native constructor
        # mass. Volume scaling preserves its density; changed/unknown material
        # state cannot borrow that default mass.
        creation = world.creation.get(key, {})
        if (not all(creation.get(flag) for flag in ('fresh', 'complete', 'ordinary'))
                or any(name in props or (key, name) in self.local for name in ('Material', 'MaterialVariant'))
                or self.field(world, key, 'CustomPhysicalProperties') is not None):
            raise ValueError('body_velocity_mass_unavailable')
        defaults = _constructor_velocities()['Part']['properties']
        mass = defaults['Mass']['value']
        baseline = defaults['Size']['value']['$vector3']
        size = self.field(world, key, 'Size', ('size',))
        return sources, mass*math.prod(size)/math.prod(baseline)

    def _bind(self, world):
        if self.world is None:
            self.world = world
        if self.world is not world:
            raise ValueError('Owned part simulation cannot cross world sessions')

    def field(self, world, key, name, aliases=()):
        entry = self.local.get((key, name))
        if entry is not None:
            revision, value = entry
            if world.property_versions.get(key, {}).get(name, 0) <= revision:
                return value
            del self.local[(key, name)]
        return _field(world, key, name, aliases)

    def group(self, world, key):
        value = self.field(world, key, 'CollisionGroup')
        return part_group({'CollisionGroup': value}) if value is not None else part_group(world.instances[key]['properties'])

    def eligible(self, world, key):
        node = world.instances.get(key)
        if node is None or _class_name(world, node) != 'Part':
            return False
        props = node['properties']
        if (type(world.client_scope) is not int or world.client_scope <= 0
                or type(props.get('NetworkOwnerV3')) is not int
                or props['NetworkOwnerV3'] != world.client_scope
                or _field(world, key, 'Anchored') is not False
                or self.field(world, key, 'Anchored') is not False
                or key in self.connected
                or any(props.get(name) not in (None, key)
                       for name in ('PhysicsRepRootPart', 'PhysicsRepRootRef'))):
            return False
        if (self.field(world, key, 'Shape', ('shape',)) != 1
                or type(self.field(world, key, 'CanCollide')) is not bool):
            return False
        size = self.field(world, key, 'Size', ('size',))
        if not _vector(size, 3) or any(v <= 0 for v in size):
            return False
        cursor, seen = key, set()
        while cursor in world.instances and cursor not in seen:
            seen.add(cursor)
            if cursor in self.characters or (cursor, 'Parent') in self.local:
                return False
            node = world.instances[cursor]
            kind = _class_name(world, node)
            if kind == 'WorldModel':
                return False
            if kind == 'Workspace':
                return True
            cursor = node.get('parent')
        return False

    def _rebuild(self, world, now):
        self.connected = _connected(world)
        self.characters = {node['properties'].get('Character')
                           for node in world.instances.values()
                           if _class_name(world, node) == 'Player'}
        colliders, unsupported = [], []
        self.controllers = {}
        self.blockers = []
        self.dynamic_keys = []
        self.collision_contexts = {}
        self.static_geometry_dirty.clear()
        for key, node in world.instances.items():
            if _class_name(world, node) == 'BodyVelocity':
                self.controllers.setdefault(node.get('parent'), []).append(key)
        for key, node in world.instances.items():
            if self.field(world, key, 'CanCollide') is not True:
                continue
            if self.field(world, key, 'Anchored') is not True:
                if self.field(world, key, 'Anchored') is False:
                    self.dynamic_keys.append(key)
                continue
            try:
                if (_class_name(world, node) != 'Part'
                        or self.field(world, key, 'Shape', ('shape',)) != 1):
                    raise ValueError('static_shape_unsupported')
                size = self.field(world, key, 'Size', ('size',))
                if not _vector(size, 3) or any(v <= 0 for v in size):
                    raise ValueError('static_size_unavailable')
                if (key, 'Parent') in self.local:
                    raise ValueError('local_static_parent_unsupported')
                position, rotation = self._pose(world, key)
                colliders.append(OBB(position, tuple(v*.5 for v in size), rotation, key))
            except (ValueError, TypeError, KeyError):
                unsupported.append((key, 'static_shape_or_pose_unavailable'))
                bounds = None
                try:
                    size = self.field(world, key, 'Size', ('size',))
                    position, rotation = self._pose(world, key)
                    bounds = OBB(position, tuple(v*.5 for v in size), rotation, key)
                except (ValueError, TypeError, KeyError):
                    # Opaque rotation cannot locate an exact collision shape,
                    # but a sphere enclosing the supplied size is a conservative
                    # blocker. Its bounds are never used as a collision surface.
                    try:
                        size = self.field(world, key, 'Size', ('size',))
                        props = node['properties']
                        versions = world.property_versions.get(key, {})
                        name = max((n for n in ('CFrame', 'ReplicationPV') if n in props),
                                   key=lambda n: versions.get(n, 0))
                        position = props[name]['position']
                        if (not _vector(size, 3) or any(v <= 0 for v in size)
                                or not isinstance(position, (list, tuple)) or len(position) != 3
                                or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)):
                            raise ValueError('unknown bounds')
                        radius = math.hypot(*size)*.5
                        bounds = UnmodeledBounds(tuple(position), radius)
                    except (ValueError, TypeError, KeyError):
                        pass
                self.blockers.append((_workspace(world, key), bounds, key))
        self.geometry = GroundGeometry(tuple(colliders), tuple(unsupported))
        self._rebuild_bounds_indices(world)
        self.last_rebuild = now
        self.scene_revision = world.revision
        for key in world.instances:
            if key in self.bodies or len(self.bodies) >= self.MAX_BODIES or not self.eligible(world, key):
                continue
            try:
                self._servo_state(world, key)
                position, rotation = self._pose(world, key)
                (linear, angular), source = _velocities(world.instances[key]['properties'],
                    world.creation.get(key), 'Part')
                self.bodies[key] = Body(position, rotation, tuple(linear), tuple(angular),
                                        world.revision, now, source)
            except (ValueError, TypeError, KeyError):
                self.skipped['initial_state_unavailable'] += 1

    def observe(self, world, mutations, revision, now):
        self._bind(world)
        if type(revision) not in (int, float) or not math.isfinite(revision) or revision < 0:
            return
        if self.last_rebuild is None:
            self._rebuild(world, now)
        for change in mutations:
            if change.get('kind') != 'set':
                continue
            try:
                key = parse_key(change.get('id'))
                name, value = change.get('property'), change.get('value')
                if key not in world.instances or world.property_versions.get(key, {}).get(name, 0) > revision:
                    continue
                kind = _class_name(world, world.instances[key])
                static_frame = name == 'CFrame' and kind == 'Part' and self.field(world, key, 'Anchored') is True
                if (name in LOCAL_FIELDS or static_frame) and kind in ('Part', 'BodyVelocity'):
                    if (key, name) not in self.local and len(self.local) >= 1024:
                        self.disabled_reason = 'local_geometry_overlay_budget'
                        continue
                    decoded = value.get('$vector3', value) if isinstance(value, dict) else value
                    if static_frame:
                        decoded = value.get('$cframe', {})
                        if not _vector(decoded.get('position'), 3) or not _rotation(decoded.get('rotation')):
                            continue
                        try:
                            old_position, old_rotation = self._pose(world, key)
                        except (ValueError, TypeError, KeyError):
                            old_position = old_rotation = None
                        if (old_position == tuple(decoded['position'])
                                and old_rotation == tuple(decoded['rotation'])):
                            continue
                    self.local[(key, name)] = (int(revision), decoded)
                    if static_frame:
                        self.static_geometry_dirty.add(key)
                    elif name not in {'AssemblyLinearVelocity', 'AssemblyAngularVelocity',
                                      'Velocity', 'P', 'MaxForce'}:
                        self.last_rebuild = None
                body = self.bodies.get(key)
                if body is None:
                    continue
                if max((world.property_versions.get(key, {}).get(n, 0) for n in POSE_FIELDS)) > revision:
                    continue
                if name == 'CFrame':
                    frame = value.get('$cframe', {})
                    if not _vector(frame.get('position'), 3) or not _rotation(frame.get('rotation')):
                        continue
                    body.position = tuple(frame['position'])
                    body.rotation = tuple(frame['rotation'])
                    # Teleporting changes pose; it does not reset momentum or
                    # invent a velocity from the teleport distance.
                elif name in ('AssemblyLinearVelocity', 'AssemblyAngularVelocity'):
                    if not _vector(decoded, 3):
                        continue
                    if name == 'AssemblyLinearVelocity': body.linear = tuple(decoded)
                    else: body.angular = tuple(decoded)
                else:
                    continue
                body.revision = int(revision)
                body.last_at = now
                self.dirty_feedback = True
            except (ValueError, TypeError, KeyError, AttributeError):
                self.skipped['invalid_local_mutation'] += 1

    def advance(self, world, now, *, force=False):
        self._bind(world)
        if self.disabled_reason:
            return {}
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('Simulation time must be finite')
        if self.last_tick is not None and now < self.last_tick:
            raise ValueError('Simulation clock moved backward')
        if not force and self.last_tick is not None and now-self.last_tick < 1/60:
            return {}
        self.last_tick = now
        if self._scene_changed(world):
            self._rebuild(world, now)
        else:
            self._refresh_dirty_static_geometry(world)
        # The topology cache is refreshed by every relevant received action
        # (including journal gaps). Do not re-walk every class ancestor for
        # every body tick; owner and pose revisions are still checked below.
        poses = {}
        for key, body in list(self.bodies.items()):
            if not self.eligible(world, key):
                del self.bodies[key]
                self.skipped['authority_or_shape_changed'] += 1
                continue
            versions = world.property_versions.get(key, {})
            if max((versions.get(name, 0) for name in POSE_FIELDS)) > body.revision:
                del self.bodies[key]
                self.skipped['newer_received_state'] += 1
                self.last_rebuild = None
                continue
            workspace = _workspace(world, key)
            gravity = world.instances[workspace]['properties'].get('Gravity')
            if type(gravity) not in (int, float) or not math.isfinite(gravity):
                body.last_at = now
                self.skipped['received_gravity_unavailable'] += 1
                continue
            gravity = struct.unpack('<f', struct.pack('<f', max(0., min(1000., gravity))))[0]
            elapsed = max(0., now-body.last_at)
            dt = min(.25, elapsed)
            self.discarded_wall_seconds += elapsed-dt
            body.last_at = now
            if not dt:
                continue
            try:
                geometry = GroundGeometry(())
                if self.field(world, key, 'CanCollide'):
                    context, allowed = self._collision_context(world, key)
                    group = self.group(world, key)
                    size = self.field(world, key, 'Size', ('size',))
                    # Broad phase includes the one-stud bounded penetration
                    # correction as well as the entire accelerated sweep.
                    end = tuple(body.position[i]+body.linear[i]*dt-(.5*gravity*dt*dt if i == 1 else 0.)
                                for i in range(3))
                    radius = max(size)*.5+1.
                    lower = tuple(min(body.position[i], end[i])-radius for i in range(3))
                    upper = tuple(max(body.position[i], end[i])+radius for i in range(3))
                    for other, (bounds, _) , bound_minimum, bound_maximum in self.blocker_index.query(
                            workspace, lower, upper):
                        if not context.table.collidable(group, self.group(world, other)):
                            continue
                        if (bounds is None or (bounds.intersects(lower, upper)
                                if isinstance(bounds, UnmodeledBounds)
                                else _overlaps(lower, upper, bound_minimum, bound_maximum))):
                            raise ValueError('unmodeled_static_geometry_near_motion')
                    for other in self.dynamic_keys:
                        if other == key or other not in world.instances or _workspace(world, other) != workspace:
                            continue
                        if not context.table.collidable(group, self.group(world, other)):
                            continue
                        other_size = self.field(world, other, 'Size', ('size',))
                        if not _vector(other_size, 3) or any(v <= 0 for v in other_size):
                            raise ValueError('unmodeled_dynamic_geometry_bounds')
                        other_body = self.bodies.get(other)
                        if other_body is not None:
                            other_position = other_body.position
                        else:
                            props = world.instances[other]['properties']
                            versions = world.property_versions.get(other, {})
                            names = [n for n in ('CFrame', 'ReplicationPV') if n in props]
                            if not names:
                                raise ValueError('unmodeled_dynamic_geometry_bounds')
                            other_position = props[max(names, key=lambda n: versions.get(n, 0))].get('position')
                        if not _vector(other_position, 3):
                            raise ValueError('unmodeled_dynamic_geometry_bounds')
                        other_radius = math.hypot(*other_size)*.5
                        if all(other_position[i]+other_radius >= lower[i] and other_position[i]-other_radius <= upper[i] for i in range(3)):
                            raise ValueError('dynamic_body_contact_unsupported')
                    selected = []
                    for source, collider, bound_minimum, bound_maximum in self.collider_index.query(
                            workspace, lower, upper):
                        if (source in allowed
                                and _overlaps(lower, upper, bound_minimum, bound_maximum)):
                            selected.append(collider)
                    geometry = GroundGeometry(tuple(selected), self.geometry.unsupported)
                sources, mass = self._servo_state(world, key)
                result = step_owned_part(body.position, body.rotation, body.linear, body.angular,
                    self.field(world, key, 'Size', ('size',)), dt, gravity, geometry,
                    velocity_sources=sources, mass=mass)
                if result.unsupported_reason:
                    self.skipped[result.unsupported_reason] += 1
                    continue
                body.position, body.linear = result.position, result.linear_velocity
                body.simulated_seconds += result.simulated_time
                body.velocity_source = 'local_owned_part_body_velocity_model' if sources else 'local_owned_part_gravity_model'
                body.revision = world.revision
                self.steps += 1
                self.contacts += len(result.contacts)
                poses[key] = (body.position, body.rotation, body.revision)
                self.dirty_feedback = True
            except (ValueError, TypeError, KeyError) as error:
                self.skipped[str(error)[:100]] += 1
        return poses

    def velocities(self, key, pose):
        body = self.bodies.get(key)
        if body is not None and (body.position, body.rotation, body.revision) == pose:
            return (body.linear, body.angular), body.velocity_source
        return None

    def feedback(self, world):
        self._bind(world)
        if self.disabled_reason:
            return None
        if not self.dirty_feedback:
            return None
        self.dirty_feedback = False
        frames = []
        for key, body in self.bodies.items():
            if not self.eligible(world, key):
                continue
            if max((world.property_versions.get(key, {}).get(n, 0) for n in POSE_FIELDS)) > body.revision:
                continue
            frames.append({'id': '%d:%d' % key, 'cframe': {'$cframe': {
                'position': list(body.position), 'rotation': list(body.rotation)}},
                'linear_velocity': list(body.linear), 'angular_velocity': list(body.angular)})
        return {'scope': world.client_scope, 'source_revision': world.revision, 'frames': frames} if frames else None

    def report(self):
        return {'model': 'isolated_axis_aligned_block_gravity_inelastic_static_contacts',
                'active_bodies': len(self.bodies), 'steps': self.steps, 'contacts': self.contacts,
                'static_colliders': len(self.geometry.colliders),
                'unsupported_static_geometry': len(self.geometry.unsupported),
                'body_velocity_controllers': sum(len(v) for v in self.controllers.values()),
                'disabled_reason': self.disabled_reason,
                'skipped': dict(self.skipped), 'discarded_wall_seconds': self.discarded_wall_seconds,
                'native_solver_parity': False, 'server_acceptance_confirmed': False}
