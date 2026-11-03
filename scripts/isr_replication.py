"""Bounded receive-only instance-state replication for the selected native build.

0x10449E0A8 reads the packet header; 0x10449FDC8 reads groups, including
their LOD; 0x104CDBED0 reads schema-directed records. The legacy PV readers
are 0x104F213A4 / 0x104F2189C / 0x104F219F8 / 0x104F21B54.
No acknowledgement, outgoing state, or server acceptance is synthesized.
"""
import collections
import math
import struct

from physics_rotation import decode_native_quaternion, native_compact_selector_to_matrix
from physics_velocity import decode as decode_velocity, decode_native_short_velocity
from wire_reader import Reader, WireError
from world_replication import World


MAX_PACKET_BYTES = 1024 * 1024
MAX_RECORDS = 4096


def _f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def decode_short_quaternion(raw):
    """0x104CBF13C: largest component plus three signed ten-bit components."""
    if len(raw) != 4:
        raise WireError('ISR short quaternion requires four bytes')
    word = int.from_bytes(raw, 'little')
    omitted = word >> 30
    values = []
    for shift in (20, 10, 0):
        signed = (word >> shift) & 1023
        if signed & 512:
            signed -= 1024
        values.append(_f32(_f32(signed / 511.0) * 0.7071067690849304))
    squared = _f32(_f32(_f32(values[0]**2) + _f32(values[1]**2)) + _f32(values[2]**2))
    values.insert(omitted, _f32(math.sqrt(max(_f32(1.0 - squared), 0.0))))
    return tuple(values)


def _matrix(q):
    x, y, z, w = q
    return [_f32(v) for v in (
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y))]


def read_legacy_pv(reader, lod):
    flags = reader.u8()
    position = [0., 0., 0.] if flags & 128 else World.compact_position(reader)
    selector = flags & 31
    if selector == 0:
        raw_rotation = reader.take(6 if lod < 2 else 4)
        q = decode_native_quaternion(raw_rotation)[0] if lod < 2 else decode_short_quaternion(raw_rotation)
        rotation = _matrix(q)
    elif 1 <= selector <= 24:
        rotation = list(native_compact_selector_to_matrix(selector))
        raw_rotation = b''
    else:
        # Selector 31 is a separate seven-float native representation, not
        # the compact quaternion used by outgoing physics records.
        raise WireError('Unsupported ISR PV rotation selector %d' % selector)
    velocity_mode = 'compact' if lod == 0 else 'short'
    packed, vectors = [], []
    for mask in (64, 32):
        if flags & mask:
            packed.append(None)
            vectors.append([0., 0., 0.])
        else:
            prefix = reader.u8()
            tail = reader.take(4 if lod == 0 else 2) if prefix else b''
            raw = bytes((prefix,)) + tail
            decoded = decode_velocity(raw) if lod == 0 else decode_native_short_velocity(raw)
            packed.append([prefix, int.from_bytes(tail, 'little')])
            vectors.append(list(decoded.vector))
    if not all(math.isfinite(x) for x in position + rotation + vectors[0] + vectors[1]):
        raise WireError('Non-finite ISR PV component')
    return {'position': list(position), 'rotation': rotation,
            'rotation_selector': selector, 'packed_rotation': raw_rotation.hex(),
            'packed_velocities': packed, 'velocity_mode': velocity_mode,
            'decoded_velocities': vectors, 'position_present': not bool(flags & 128),
            'wire_format': 'isr_legacy_pv', 'lod': lod}


def decode_packet(payload, schema, *, legacy_pv_enabled):
    """Validate the complete message before exposing any instance changes."""
    if len(payload) > MAX_PACKET_BYTES:
        raise WireError('ISR packet exceeds limit')
    r = Reader(payload)
    if r.u8() != 161:
        raise WireError('Not an ISR packet')
    timestamp, group_count, header_value = r.u32(), r.u32(), r.number('i')
    if group_count > MAX_RECORDS:
        raise WireError('ISR group count exceeds limit')
    records = []
    for _ in range(group_count):
        count, lod, time_delta = r.u16(), r.u8(), r.u8()
        if lod > 3 or len(records) + count > MAX_RECORDS:
            raise WireError('Invalid ISR group LOD or record count')
        for _ in range(count):
            scope = r.varuint()
            key = (scope, r.u32()) if scope else None
            ci, bit_count = r.u16(), r.u16()
            if ci >= len(schema['classes']) or bit_count > 256:
                raise WireError('Invalid ISR class or dirty-set size')
            props = schema['classes'][ci].get('isr_properties', [])
            mask = sum(r.u64() << (64*i) for i in range((bit_count+63)//64))
            if bit_count > len(props) or mask >> bit_count:
                raise WireError('ISR dirty-set exceeds schema')
            body = Reader(r.take(r.u16()))
            fields = {}
            for i in range(bit_count):
                if not mask >> i & 1:
                    continue
                p = schema['properties'][props[i]]
                if p['type'] in (28, 69):
                    scope = body.varuint()
                    value = (scope, body.u32()) if scope else None
                elif p['type'] == 57 and legacy_pv_enabled:
                    value = read_legacy_pv(body, lod)
                else:
                    raise WireError('Unsupported ISR property type or PV format')
                fields[p['name']] = value
            body.finish()
            records.append({'key': key, 'class': ci, 'fields': fields,
                            'lod': lod, 'time_delta': time_delta})
    r.finish()
    return {'timestamp': timestamp, 'header_value': header_value,
            'group_count': group_count, 'records': records}


class InstanceStateReceiver:
    def __init__(self, world, *, legacy_pv_enabled):
        self.world = world
        self.legacy_pv_enabled = legacy_pv_enabled
        self.counts = collections.Counter()
        self.lods = collections.Counter()
        self.last_timestamp = None
        self.last_error = None

    def receive(self, payload):
        self.counts['packets_received'] += 1
        try:
            packet = decode_packet(payload, self.world.schema,
                                   legacy_pv_enabled=self.legacy_pv_enabled)
        except (WireError, ValueError) as error:
            self.counts['packets_rejected'] += 1
            self.last_error = str(error)[:180]
            return False
        self.counts['packets_decoded'] += 1
        self.counts['records_decoded'] += len(packet['records'])
        timestamp = packet['timestamp']
        # This unreliable receive channel can deliver old datagrams. Never
        # overwrite newer received state with an older packet (u32 rollover).
        if self.last_timestamp is not None and (timestamp-self.last_timestamp) & 0xffffffff >= 0x80000000:
            self.counts['stale_packets_ignored'] += 1
            return True
        self.last_timestamp = timestamp
        for record in packet['records']:
            self.lods[record['lod']] += 1
            node = self.world.instances.get(record['key'])
            if node is None or node['class'] != record['class']:
                self.counts['unresolved_records'] += 1
                continue
            if record['fields']:
                node['properties'].update(record['fields'])
                self.world.touch(record['key'], record['fields'])
                self.counts['records_applied'] += 1
        return True

    def summary(self):
        return {**dict(self.counts), 'records_by_lod': dict(self.lods),
                'legacy_pv_enabled': self.legacy_pv_enabled,
                'last_packet_timestamp': self.last_timestamp,
                'last_decode_error': self.last_error, 'outgoing_state_implemented': False}
