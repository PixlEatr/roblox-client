"""Authored poses for received, locally owned, isolated physics parts.

Uses the existing character sender's packet sequence and transport. This is
not a general assembly solver: connected parts and other characters are
excluded, and ownership/anchoring are checked again before each submission.
"""
import collections
import math
import struct
import json
from functools import lru_cache
from pathlib import Path

from physics_assemblies import _class_name, _parents, _is_a
from physics_history import CurrentRecord
from physics_packet import TransformHeader
from physics_root_record import RootRecordEnvelope, encode_client_current_body, encode_instance_reference
from physics_rotation import RotationField, native_matrix3x3_to_quaternion
from physics_velocity import decode as decode_velocity, decode_native_short_velocity
from remote_client_rpc import parse_key
from world_snapshot import _node_snapshot


def _vector(value, count):
    return (isinstance(value, (list, tuple)) and len(value) == count
            and all(type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e9 for v in value))


def _rotation(value):
    if not _vector(value, 9):
        return False
    rows = [value[i:i+3] for i in (0, 3, 6)]
    if any(abs(sum(a*b for a,b in zip(rows[i], rows[j])) - (i == j)) > 1e-4
           for i in range(3) for j in range(3)):
        return False
    a,b,c,d,e,f,g,h,i = value
    return abs(a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)-1) < 1e-4


@lru_cache(maxsize=1)
def _constructor_velocities():
    return json.loads((Path(__file__).resolve().parents[1]/'runtime/class-constructors-745.json').read_text())['classes']


def _velocities(properties, creation=None, class_name=None):
    pv = properties.get('ReplicationPV', {})
    packed = pv.get('packed_velocities') if isinstance(pv, dict) else None
    if not isinstance(packed, list) or len(packed) != 2:
        # A complete fresh creation with no PV field initializes a local body
        # from its constructor. These are modeled initial velocities, not
        # velocities measured from the server or a claim of force simulation.
        if ('ReplicationPV' not in properties and creation is not None
                and all(creation.get(flag) for flag in ('fresh','complete','ordinary'))):
            fields=_constructor_velocities().get(class_name,{}).get('properties',{})
            values=[fields.get(name,{}).get('value',{}).get('$vector3')
                    for name in ('AssemblyLinearVelocity','AssemblyAngularVelocity')]
            if all(_vector(value,3) for value in values):
                return values, 'local_constructor_initial_state'
        raise ValueError('received_velocity_unavailable')
    out = []
    mode = pv.get('velocity_mode', 'compact')
    if mode not in ('compact', 'short'):
        raise ValueError('received_velocity_invalid')
    for value in packed:
        # Type-57 omission explicitly encodes the zero-vector branch.
        if value is None:
            out.append((0., 0., 0.))
        elif (isinstance(value, list) and len(value) == 2
              and type(value[0]) is int and 0 <= value[0] <= 255
              and type(value[1]) is int and 0 <= value[1] <= (0xffff if mode == 'short' else 0xffffffff)):
            raw = bytes((value[0],)) + (struct.pack('<H' if mode == 'short' else '<I', value[1]) if value[0] else b'')
            out.append((decode_native_short_velocity(raw) if mode == 'short' else decode_velocity(raw)).vector)
        else:
            raise ValueError('received_velocity_invalid')
    return out, 'received_replication_pv'


class OwnedPartPoses:
    def __init__(self):
        # Materialize immutable constructor data before starting the network
        # loop. iCloud may offload this file; opening it for the first authored
        # pose can otherwise suspend packet processing for several minutes.
        _constructor_velocities()
        self.world = None
        self.pending = collections.OrderedDict()
        self.accepted = self.sent = 0
        self.rejected = collections.Counter()
        self.velocity_sources = collections.Counter()
        self.coalesced = 0
        self.recent_submitted = collections.deque(maxlen=16)

    def _bind(self, world):
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('Authored part poses cannot cross world sessions')

    def observe(self, world, mutations, revision):
        self._bind(world)
        if type(revision) not in (int, float) or not math.isfinite(revision) or revision < 0:
            return
        for change in mutations:
            if change.get('kind') != 'set' or change.get('property') != 'CFrame':
                continue
            try:
                key = parse_key(change.get('id'))
                node = world.instances.get(key)
                if (node is not None and (type(world.client_scope) is not int
                        or world.client_scope <= 0
                        or type(node['properties'].get('NetworkOwnerV3')) is not int
                        or node['properties']['NetworkOwnerV3'] != world.client_scope)):
                    self.rejected['ownership_not_current_at_write'] += 1
                    continue
                frame = change.get('value', {}).get('$cframe', {})
                position, rotation = frame.get('position'), frame.get('rotation')
                if key not in world.instances or not _vector(position, 3) or not _rotation(rotation):
                    raise ValueError('invalid_received_part_pose')
                if key not in self.pending and len(self.pending) >= 64:
                    raise ValueError('part_pose_queue_full')
                if key in self.pending:
                    self.coalesced += 1
                self.pending[key] = (tuple(position), tuple(rotation), int(revision))
                self.accepted += 1
            except (TypeError, ValueError, AttributeError):
                self.rejected['invalid_or_unreceived_pose'] += 1

    def prepare(self, world, *, limit=8, wire_format='legacy', isr_null_root=False):
        if wire_format not in ('legacy', 'isr'):
            raise ValueError('Unknown authored pose wire format')
        if isr_null_root and wire_format != 'isr':
            raise ValueError('Explicit ISR root requires ISR wire format')
        self._bind(world)
        if not self.pending:
            return [], []
        parents = _parents(world)
        characters = {v['properties'].get('Character') for v in world.instances.values()
                      if _class_name(world, v) == 'Player'}
        # Any received rigid edge excludes the part. Unknown Enabled state
        # cannot justify treating a connected assembly as a single part.
        connected = set()
        for item in world.instances.values():
            if (_is_a(_class_name(world, item), 'JointInstance', parents)
                    or _class_name(world, item) == 'WeldConstraint'):
                connected.update(item['properties'].get(name) for name in ('Part0', 'Part1'))
        records, keys = [], []
        for key, (position, rotation, revision) in list(self.pending.items()):
            if len(records) >= limit:
                break
            reason = None
            node = world.instances.get(key)
            if node is None or not _is_a(_class_name(world, node), 'BasePart', parents):
                reason = 'part_removed_or_not_basepart'
            elif (type(world.client_scope) is not int or world.client_scope <= 0
                  or type(node['properties'].get('NetworkOwnerV3')) is not int
                  or node['properties']['NetworkOwnerV3'] != world.client_scope):
                reason = 'ownership_not_current'
            elif _node_snapshot(world, key, node)['properties'].get('Anchored') is not False:
                reason = 'part_not_known_unanchored'
            elif key in connected or node['properties'].get('PhysicsRepRootPart') not in (None, key):
                reason = 'connected_assembly_unsupported'
            elif max((world.property_versions.get(key, {}).get(name, 0)
                      for name in ('CFrame', 'ReplicationPV', 'Parent')), default=0) > revision:
                reason = 'newer_received_pose'
            if reason is None and isr_null_root:
                creation = world.creation.get(key, {})
                # The experiment authors world-space state for a fresh isolated
                # body. It must not detach a received root or change assemblies.
                if (not all(creation.get(flag) for flag in ('fresh','complete','ordinary'))
                        or any(node['properties'].get(name) is not None
                               for name in ('PhysicsRepRootPart','PhysicsRepRootRef'))
                        or any(world.property_versions.get(key, {}).get(name, 0) > revision
                               for name in ('PhysicsRepRootPart','PhysicsRepRootRef'))):
                    reason = 'explicit_null_root_not_supported'
            cursor, seen = key, set()
            in_workspace = False
            while cursor in world.instances and cursor not in seen:
                seen.add(cursor)
                if cursor in characters:
                    reason = 'character_pose_excluded'
                    break
                if _class_name(world, world.instances[cursor]) == 'Workspace':
                    in_workspace = True
                    break
                cursor = world.instances[cursor].get('parent')
            if reason is None and not in_workspace:
                reason = 'outside_received_workspace'
            if reason:
                self.rejected[reason] += 1
                del self.pending[key]
                continue
            try:
                (linear, angular), velocity_source = _velocities(node['properties'],world.creation.get(key),_class_name(world,node))
                if wire_format == 'isr':
                    from isr_sender import encode_pv, encode_record
                    records.append(encode_record(world.schema, key, node['class'],
                        encode_pv(position, rotation, linear, angular),
                        explicit_null_root=isr_null_root))
                    keys.append(key)
                    self.velocity_sources[velocity_source] += 1
                    continue
                rot = RotationField(0, 'native-quaternion', native_matrix3x3_to_quaternion(rotation))
                record = CurrentRecord(TransformHeader(0, False, False, False, 0), position,
                                       rot, linear, angular, 'compact',
                                       reference_bytes=encode_instance_reference(0, None))
                # No humanoid state, child assemblies, or motors exist in this
                # deliberately isolated-part path. Use compact velocities.
                body = encode_client_current_body(record, d76c_flags=0x20)
                records.append(RootRecordEnvelope(encode_instance_reference(world.client_scope, key), body)
                               .encode(stream_available=True, record_enabled=True))
                keys.append(key)
                self.velocity_sources[velocity_source] += 1
            except (TypeError, ValueError, OverflowError):
                self.rejected['pose_or_received_velocity_unencodable'] += 1
                del self.pending[key]
        return records, keys

    def acknowledge(self, keys):
        for key in keys:
            pose = self.pending.pop(key, None)
            if pose is not None:
                self.recent_submitted.append({'id':'%d:%d'%key,'position':list(pose[0])})
        self.sent += len(keys)

    def report(self):
        return {'authored_pose_writes': self.accepted, 'records_submitted': self.sent,
                'pending_parts': len(self.pending), 'rejected': dict(self.rejected),
                'prepared_velocity_sources':dict(self.velocity_sources),
                'writes_coalesced_before_send':self.coalesced,
                'recent_submitted_poses':list(self.recent_submitted),
                'server_acceptance_confirmed': False, 'scope': 'owned_isolated_parts'}
