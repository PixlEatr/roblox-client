"""Outgoing legacy-PV ISR framing; transport and simulation clock are external.

The native sender is 10449F078/10449F868/104CDA610. Its clock is updated
from the physics-step signal by 104D2A4C8, not from incoming server steps.
Without delivery-history clock observations the header offset remains unknown
(INT_MAX), as in the native constructor. This module creates no acknowledgments.
"""
import math
import struct

from physics_packet import TransformHeader, encode_transform_component
from physics_root_record import encode_instance_reference
from physics_rotation import RotationField, native_matrix3x3_to_quaternion
from isr_clock import IsrClockEstimator


UNKNOWN_OFFSET = 0x7fffffff
MAX_DATAGRAM_BYTES = 1100


def _integer(value, lower, upper, name):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('Invalid ISR '+name)
    return value


def encode_pv(position, rotation, linear_velocity, angular_velocity):
    """LOD 0 PV, with all fields present and a native six-byte quaternion.

    Omitting zero components is optional in the reader. Keeping the fields
    explicit avoids relying on the native writer's configurable thresholds.
    """
    if (not isinstance(rotation, (tuple, list)) or len(rotation) != 9
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in rotation)):
        raise ValueError('Invalid ISR rotation matrix')
    rows = [rotation[i:i+3] for i in (0, 3, 6)]
    if any(abs(sum(a*b for a,b in zip(rows[i], rows[j])) - (i == j)) > 1e-4
           for i in range(3) for j in range(3)):
        raise ValueError('Invalid ISR rotation matrix')
    a,b,c,d,e,f,g,h,i = rotation
    if abs(a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)-1) > 1e-4:
        raise ValueError('Invalid ISR rotation matrix')
    for vector in (position, linear_velocity, angular_velocity):
        if (not isinstance(vector, (tuple, list)) or len(vector) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) >= 1e9
                       for v in vector)):
            raise ValueError('Invalid ISR position or velocity')
    return encode_transform_component(
        header=TransformHeader(0, False, False, False, 0),
        position_value=position,
        rotation=RotationField(0, 'native-quaternion', native_matrix3x3_to_quaternion(rotation)),
        linear_velocity=linear_velocity, angular_velocity=angular_velocity,
        velocity_mode='compact')


def encode_record(schema, key, class_index, pv, *, explicit_null_root=False):
    """Serialize PV and optionally an explicit unbound root in schema order."""
    if not isinstance(key, tuple) or len(key) != 2:
        raise ValueError('ISR requires a received scoped instance key')
    scope, object_id = key
    _integer(scope, 1, 0xfffffffe, 'scope')
    _integer(object_id, 0, 0xffffffff, 'object id')
    _integer(class_index, 0, min(65535, len(schema['classes'])-1), 'class')
    props = schema['classes'][class_index].get('isr_properties', [])
    if not 1 <= len(props) <= 256:
        raise ValueError('ISR property table absent or too large')
    candidates = [i for i, index in enumerate(props)
                  if schema['properties'][index]['name'] == 'ReplicationPV'
                  and schema['properties'][index]['type'] == 57]
    if len(candidates) != 1:
        raise ValueError('ISR schema has no unique legacy PV property')
    if not isinstance(pv, bytes) or not pv or len(pv) > 65535:
        raise ValueError('Invalid ISR PV body')
    selected = {candidates[0]: pv}
    if explicit_null_root:
        roots = [i for i, index in enumerate(props)
                 if schema['properties'][index]['name'] == 'PhysicsRepRootPart'
                 and schema['properties'][index]['type'] == 28]
        if len(roots) != 1:
            raise ValueError('ISR schema has no unique instance root property')
        selected[roots[0]] = b'\x00'
    body = b''.join(selected[i] for i in sorted(selected))
    if len(body) > 65535:
        raise ValueError('ISR record body exceeds length field')
    mask = sum(1 << i for i in selected)
    words = b''.join(struct.pack('<Q', mask >> (64*i) & 0xffffffffffffffff)
                     for i in range((len(props)+63)//64))
    return (encode_instance_reference(scope, key)
            + struct.pack('<HH', class_index, len(props)) + words
            + struct.pack('<H', len(body)) + body)


def encode_packets(records, *, local_step, remote_to_local_offset=None):
    """Bounded datagrams at current local step (LOD 0, per-group age zero).

    ``remote_to_local_offset`` is the native remote-step minus local-step
    estimate, when supplied by real session observations. None is unknown.
    No remote timestamp is reused as a local clock.
    """
    _integer(local_step, 0, 0xffffffff, 'local physics step')
    if remote_to_local_offset is None:
        header_offset = UNKNOWN_OFFSET
    else:
        _integer(remote_to_local_offset, -0x7ffffffe, 0x7ffffffe, 'clock offset')
        header_offset = -remote_to_local_offset
    # All records represent current authored state, so their group age is 0.
    prefix = b'\xa1' + struct.pack('<IIi', local_step, 1, header_offset)
    packets, group = [], []
    size = len(prefix)+4
    for record in records:
        if not isinstance(record, bytes) or not record:
            raise ValueError('ISR records must be nonempty bytes')
        if len(record)+len(prefix)+4 > MAX_DATAGRAM_BYTES:
            raise ValueError('ISR record exceeds datagram limit')
        if size+len(record) > MAX_DATAGRAM_BYTES:
            packets.append(prefix+struct.pack('<HBB', len(group), 0, 0)+b''.join(group))
            group, size = [], len(prefix)+4
        group.append(record)
        size += len(record)
    if group:
        packets.append(prefix+struct.pack('<HBB', len(group), 0, 0)+b''.join(group))
    return packets


class OwnedIsrSender:
    """One-session sender for validated current, authored isolated-part poses.

    The caller supplies its actual local simulation step. This deliberately
    does not estimate a remote offset without transport delivery history.
    """
    def __init__(self, queue, *, explicit_null_root=False):
        self.queue = queue
        self.explicit_null_root = explicit_null_root
        self.world = None
        self.last_step = None
        self.packets_sent = self.records_sent = 0
        self.error = None
        self.clock = IsrClockEstimator()
        self.delivery_tracking = False
        from owned_part_contacts import OwnedPartContacts
        self.contacts = OwnedPartContacts()

    def observe_remote(self, world, step):
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('ISR clock cannot cross world sessions')
        self.clock.observe_remote(step)

    def send(self, world, *, local_step, legacy_pv_enabled, send_unreliable,
             track_delivery=False, send_reliable=None):
        if self.error is not None or not legacy_pv_enabled:
            return
        _integer(local_step, 0, 0xffffffff, 'local physics step')
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('ISR sender cannot cross world sessions')
        if (self.last_step is not None
                and (local_step-self.last_step) & 0xffffffff >= 0x80000000):
            raise ValueError('ISR local simulation clock moved backward')
        self.last_step = local_step
        records, keys = self.queue.prepare(world, wire_format='isr',
                                          isr_null_root=self.explicit_null_root)
        offset = self.clock.update(local_step)
        packets = encode_packets(records, local_step=local_step,
                                 remote_to_local_offset=offset)
        # At most eight records, with <=256 properties and bounded PV fields,
        # fit one 1100-byte datagram. Keep one submission/commit boundary.
        if len(packets) > 1:
            raise ValueError('Authored ISR batch unexpectedly requires fragmentation')
        if not packets:
            return
        try:
            if track_delivery:
                self.delivery_tracking = True
                result = send_unreliable(5, packets[0], application=4,
                    on_delivery=lambda outcome: self.clock.observe_delivery(outcome,local_step))
            else:
                result = send_unreliable(5, packets[0], application=4)
        except Exception:
            self.error = 'transport_submission_ambiguous_no_retry'
            return
        if result is False:
            self.error = 'transport_submission_ambiguous_no_retry'
            return
        self.packets_sent += 1
        self.records_sent += len(records)
        if send_reliable is not None:
            self.contacts.observe_submitted(world,
                {key: self.queue.pending[key] for key in keys}, send_reliable)
        self.queue.acknowledge(keys)  # Local submission, never a server ACK.

    def report(self):
        return {'packets_submitted': self.packets_sent, 'records_submitted': self.records_sent,
                'last_local_step': self.last_step, 'remote_to_local_offset': self.clock.offset,
                'header_offset': UNKNOWN_OFFSET if self.clock.offset is None else -self.clock.offset,
                'delivery_ack_tracking': self.delivery_tracking,
                'explicit_null_root_experiment': self.explicit_null_root,
                'clock_estimator': self.clock.report(),
                'contacts': self.contacts.report(),
                'server_acceptance_confirmed': False, 'error': self.error,
                'scope': 'owned_isolated_parts', 'application': 4, 'channel': 5}

    def tick_prediction(self, world, prediction, *, legacy_pv_enabled, send_unreliable,
                        track_delivery=False, send_reliable=None):
        # The physics driver reports None while identity/geometry is warming
        # up. No local clock exists then, so retain the authored queue.
        clock = (prediction or {}).get('simulation_step_clock')
        if clock is not None:
            self.send(world, local_step=clock['step'], legacy_pv_enabled=legacy_pv_enabled,
                      send_unreliable=send_unreliable, track_delivery=track_delivery,
                      send_reliable=send_reliable)
        return {**self.report(), 'clock':clock, 'authored_part_poses':self.queue.report()}
