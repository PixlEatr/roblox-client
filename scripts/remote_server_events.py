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

from game_wire import encode_unsigned
from remote_client_rpc import event_id, parse_key, variant
from replication_out import guid


class RemoteServerEvents:
    def __init__(self, capabilities=0x100000):
        if capabilities != 0x100000:
            raise ValueError('Unsupported remote event capability set')
        self.world = None
        self.sent = 0
        self.unreliable_sent = 0
        self.rejected = collections.Counter()

    def send(self, world, event, transport, *, unreliable_transport=None):
        if world is None:
            raise ValueError('Received world is not ready')
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('Remote events cannot cross world sessions')
        target = parse_key(event.get('node'))
        node = world.instances.get(target)
        class_name = world.schema['classes'][node['class']]['name'] if node is not None else None
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

    def flush(self, world, queue, transport, *, unreliable_transport=None):
        for _ in range(min(16, len(queue))):
            event = queue[0]
            try:
                self.send(world, event, transport, unreliable_transport=unreliable_transport)
            except ValueError as error:
                self.rejected[str(error)] += 1
            queue.popleft()

    def summary(self):
        return {'records_submitted': self.sent, 'unreliable_records_submitted': self.unreliable_sent,
                'rejected': dict(self.rejected),
                'server_delivery_confirmed': False}
