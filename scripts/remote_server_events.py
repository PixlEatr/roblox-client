"""Client-authored ordinary RemoteEvent calls on received instances.

Pinned native FireServer 10401d3d8 selects OnRemoteServerEvent; 103f80940
supplies one Tuple argument. The current item-7 schema writer and capability
set are shared with replication_rpc. No hidden Player is serialized here.
UnreliableRemoteEvent uses the same item writer but modern channel kind 3:
104D81988 selects sender+32 for SendMode 1; 104D82134 recognizes that buffer
and passes kind 3 to peer+56. 104D81A90 writes the same 0x83 header. The
1044C5FFC transport implementation forwards that channel kind unchanged.
"""
import collections
import struct
import math

from game_wire import encode_unsigned
from remote_client_rpc import event_id, parse_key, variant, string_bytes
from login_packets import encode_signed
from replication_out import guid
from owned_part_contacts import encode_contacts
from physics_assemblies import _class_name, _is_a, _parents


def typed_signal_value(world, type_id, value):
    """Encode an argument using this session's received reflection signature."""
    if type_id in (0, 3):
        if value is not None:
            raise ValueError('Signal argument must be nil')
        return b''
    if type_id in (2, 8, 33):
        return string_bytes(value)
    if type_id == 7:
        if isinstance(value, dict) and set(value) == {'$enum'}:
            value = value['$enum'].get('value')
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            raise ValueError('Signal enum must be an unsigned integer')
        return encode_unsigned(value)
    if type_id == 9:
        if type(value) is not bool:
            raise ValueError('Signal argument must be a boolean')
        return bytes([value])
    if type_id == 10:
        if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value or not -(1<<31) <= value < (1<<31):
            raise ValueError('Signal argument must be an int32')
        return encode_signed(int(value))
    if type_id in (11, 12):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Signal argument must be a finite number')
        try:
            return struct.pack('<f' if type_id == 11 else '<d', value)
        except (OverflowError, struct.error) as error:
            raise ValueError('Signal number exceeds wire range') from error
    # The existing datatype encoder supplies the same representation used by
    # the decoder. Strip its variant type tag only after verifying that tag.
    encoded = variant(world, value)
    compatible = {29:30, 32:31, 69:28}.get(type_id,type_id)
    if not encoded or encoded[0] != compatible:
        raise ValueError('Signal argument does not match received signature')
    return encoded[1:]


class RemoteServerEvents:
    def __init__(self, capabilities=0x100000):
        if capabilities != 0x100000:
            raise ValueError('Unsupported remote event capability set')
        self.world = None
        self.sent = 0
        self.unreliable_sent = 0
        self.reflected_sent = 0
        self.script_contacts_sent = 0
        self.parents = None
        self.rejected = collections.Counter()

    def send(self, world, event, transport, *, unreliable_transport=None):
        if world is None:
            raise ValueError('Received world is not ready')
        if self.world is None:
            self.world = world
            self.parents = _parents(world)
        if world is not self.world:
            raise ValueError('Remote events cannot cross world sessions')
        if 'touch' in event:
            touch = event['touch']
            if not isinstance(touch, dict) or set(touch) != {'first','second','began'} or type(touch['began']) is not bool:
                raise ValueError('Invalid script touch request')
            first, second = parse_key(touch['first']), parse_key(touch['second'])
            nodes = [world.instances.get(key) for key in (first,second)]
            if first == second or any(not _is_a(_class_name(world,node),'BasePart',self.parents) for node in nodes):
                raise ValueError('Script touch requires two received BaseParts')
            scope = getattr(world,'client_scope',None)
            if type(scope) is not int or scope <= 0 or not any(node['properties'].get('NetworkOwnerV3')==scope for node in nodes):
                raise ValueError('Script touch requires current received ownership of a participant')
            payload = encode_contacts([(first,second,touch['began'])])
            if transport(7,payload,application=4) is False:
                raise OSError('Touch transport did not confirm submission')
            self.sent += 1
            self.script_contacts_sent += 1
            return
        target = parse_key(event.get('node'))
        node = world.instances.get(target)
        class_name = world.schema['classes'][node['class']]['name'] if node is not None else None
        if 'event' in event:
            if node is None:
                raise ValueError('Signal receiver is not present in this session')
            name, values, types = event['event'], event.get('arguments'), event.get('argument_types')
            if not isinstance(name, str) or not isinstance(values, list) or not isinstance(types, list) or len(types)!=len(values) or len(types)>256:
                raise ValueError('Invalid reflected signal arguments')
            descriptor=event_id(world,target,name,tuple(types))
            body=b''.join(typed_signal_value(world,t,v) for t,v in zip(types,values))
            if len(body)>65536:
                raise ValueError('Reflected signal exceeds wire limit')
            payload=b'\x83\x07'+guid(target)+struct.pack('<H',descriptor)+body+b'\0'
            if transport(1,payload) is False:
                raise OSError('Signal transport did not confirm submission')
            self.sent+=1;self.reflected_sent+=1
            return
        if class_name not in ('RemoteEvent', 'UnreliableRemoteEvent'):
            raise ValueError('RemoteEvent receiver is not present in this session')
        unreliable = class_name == 'UnreliableRemoteEvent'
        if unreliable and unreliable_transport is None:
            raise ValueError('UnreliableRemoteEvent transport is unavailable')
        values = event.get('arguments')
        if not isinstance(values, list) or len(values) > 256:
            raise ValueError('Invalid remote event tuple')
        descriptor = event_id(world, target, 'OnRemoteServerEvent', (29,))
        body = encode_unsigned(len(values)) + b''.join(variant(world, value) for value in values)
        if len(body) > 65536:
            raise ValueError('Remote event tuple exceeds limit')
        if unreliable and len(body) > 1000:
            raise ValueError('UnreliableRemoteEvent payload exceeds 1000 bytes; dropped')
        payload = b'\x83\x07' + guid(target) + struct.pack('<H', descriptor) + body + b'\0'
        send = unreliable_transport if unreliable else transport
        if send(3 if unreliable else 1, payload) is False:
            raise OSError('Remote event transport did not confirm submission')
        self.sent += 1
        self.unreliable_sent += unreliable

    def flush(self, world, queue, transport, *, unreliable_transport=None, max_events=16):
        if type(max_events) is not int or not 0 <= max_events <= 16:
            raise ValueError('Remote event flush budget must be an integer from 0 to 16')
        count = min(max_events, len(queue))
        for _ in range(count):
            event = queue[0]
            try:
                self.send(world, event, transport, unreliable_transport=unreliable_transport)
            except ValueError as error:
                self.rejected[str(error)] += 1
            queue.popleft()
        return count

    def summary(self):
        return {'records_submitted': self.sent, 'unreliable_records_submitted': self.unreliable_sent,
                'reflected_records_submitted': self.reflected_sent,
                'script_contact_records_submitted': self.script_contacts_sent,
                'rejected': dict(self.rejected),
                'server_delivery_confirmed': False}
