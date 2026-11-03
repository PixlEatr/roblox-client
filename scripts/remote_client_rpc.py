"""Correlated replies to ordinary server-issued RemoteFunction invocations.

Uses the item-7 layout already documented in replication_rpc.py. This is not
an arbitrary remote invocation API: only an observed, outstanding request in
the current World may be answered. Values cover primitive Luau values,
nested tables, supported engine datatypes and received instance references;
other datatypes fail explicitly.
"""
import math
import struct

from game_wire import encode_unsigned
from login_packets import encode_signed
from replication_out import guid
from remote_datatypes import engine_variant


def event_id(world, target, name, types):
    ci = world.instances[target]['class']
    matches = [world.schema['events'][i] for i in world.schema['classes'][ci]['events']
               if world.schema['events'][i]['name'] == name]
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous remote callback event')
    event = matches[0]
    if (event['class'] != ci or type(event['id']) is not int
            or not 0 <= event['id'] <= 65535
            or tuple(p['type'] for p in event['arguments']) != types):
        raise ValueError('Unsupported remote callback event signature')
    return event['id']


def string_bytes(value):
    if isinstance(value, str):
        data = value.encode('utf-8')
    elif isinstance(value, dict) and set(value) == {'$bytes_hex'} and isinstance(value['$bytes_hex'], str):
        data = bytes.fromhex(value['$bytes_hex'])
    else:
        raise ValueError('Expected remote string')
    if len(data) > 65536:
        raise ValueError('Remote string exceeds limit')
    return encode_unsigned(len(data)) + data


def variant(world, value, depth=0, budget=None):
    if budget is None:
        budget = [10000]
    if depth > 16 or budget[0] <= 0:
        raise ValueError('Remote value nesting or item limit exceeded')
    budget[0] -= 1
    if value is None:
        return b'\x00'
    if type(value) is bool:
        return b'\x09' + bytes([value])
    if type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('Nonfinite remote number unsupported')
        return b'\x0c' + struct.pack('<d', value)
    if isinstance(value, str) or (isinstance(value, dict) and '$bytes_hex' in value):
        return b'\x02' + string_bytes(value)
    engine = engine_variant(value)
    if engine is not None:
        return engine
    if isinstance(value, dict) and set(value) == {'$ref'}:
        if value['$ref'] is None:
            return b'\x1c\x00'
        key = parse_key(value['$ref'])
        if key not in world.instances:
            raise ValueError('Remote result reference was not received in this session')
        return b'\x1c' + guid(key)
    if isinstance(value, dict) and set(value) == {'$remote_array'}:
        items = value['$remote_array']
        if not isinstance(items, list) or len(items) > budget[0]:
            raise ValueError('Invalid remote array')
        # Native 104DAE900: ValueArray (30) is distinct from outer Tuple (29).
        return b'\x1e' + encode_unsigned(len(items)) + b''.join(
            variant(world, item, depth+1, budget) for item in items)
    if isinstance(value, dict) and set(value) == {'$remote_map'}:
        items = value['$remote_map']
        if not isinstance(items, list) or len(items) > budget[0]:
            raise ValueError('Invalid remote dictionary')
        parts = []; keys = set()
        for pair in items:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError('Invalid remote dictionary entry')
            key = string_bytes(pair[0])
            if key in keys:
                raise ValueError('Duplicate remote dictionary key')
            keys.add(key)
            parts.append(key + variant(world, pair[1], depth+1, budget))
        return b'\x1f' + encode_unsigned(len(items)) + b''.join(parts)
    raise ValueError('Remote result datatype is not yet supported')


def parse_key(value):
    if not isinstance(value, str):
        raise ValueError('Remote receiver must be a received instance ID')
    parts = value.split(':')
    if len(parts) != 2 or any(not p.isascii() or not p.isdecimal() for p in parts):
        raise ValueError('Remote receiver must be a received instance ID')
    key = tuple(map(int, parts))
    guid(key)
    return key


class RemoteClientRpc:
    def __init__(self, capabilities=0x100000):
        if capabilities != 0x100000:
            raise ValueError('Unsupported remote callback capability set')
        self.world = None
        self.last_event = None
        self.pending = {}
        self.received = self.sent = self.errors_sent = 0

    def observe(self, world):
        if world is None:
            return
        if self.world is None:
            self.world = world
        if world is not self.world:
            raise ValueError('Remote callbacks cannot cross world sessions')
        events = list(world.events)
        start = 0
        if self.last_event is not None:
            start = next((i+1 for i,e in enumerate(events) if e is self.last_event), None)
            if start is None:
                raise ValueError('Remote callback event journal was overrun')
        for event in events[start:]:
            descriptor = world.schema['events'][event['event']]
            if descriptor['name'] != 'RemoteOnInvokeClient':
                continue
            target = event['key']
            if target not in world.instances or world.schema['classes'][world.instances[target]['class']]['name'] != 'RemoteFunction':
                raise ValueError('Invalid remote callback receiver')
            if event['event'] != event_id(world, target, 'RemoteOnInvokeClient', (10,29)):
                raise ValueError('Remote callback event mismatch')
            args = event['arguments']
            if (len(args) != 2 or type(args[0]) is not int or not -(2**31) <= args[0] < 2**31
                    or not isinstance(args[1], list) or len(args[1]) > 256):
                raise ValueError('Malformed remote callback request')
            key = (target, args[0])
            if key in self.pending or len(self.pending) >= 512:
                raise ValueError('Duplicate or excessive pending remote callback')
            self.pending[key] = True
            self.received += 1
        if events:
            self.last_event = events[-1]

    def send_reply(self, world, reply, send):
        if world is not self.world:
            raise ValueError('Remote reply belongs to a different world')
        target = parse_key(reply.get('node'))
        request = reply.get('request_id')
        if type(request) is not int or (target, request) not in self.pending:
            raise ValueError('Remote reply has no outstanding server request')
        if target not in world.instances:
            raise ValueError('Remote callback receiver is no longer present')
        if type(reply.get('ok')) is not bool:
            raise ValueError('Remote callback outcome is missing')
        success = reply['ok']
        if success:
            try:
                values = reply.get('values')
                if not isinstance(values, list) or len(values) > 256:
                    raise ValueError('Invalid remote result tuple')
                body = encode_unsigned(len(values)) + b''.join(variant(world,v) for v in values)
                if len(body) > 262144:
                    raise ValueError('Remote result tuple exceeds limit')
            except (ValueError, OverflowError, struct.error) as error:
                success = False
                body = string_bytes(str(error))
        else:
            error = reply.get('error')
            if not isinstance(error, str):
                raise ValueError('Remote callback error must be a string')
            body = string_bytes(error[:4096])
        event = event_id(world,target,'RemoteOnInvokeSuccess' if success else 'RemoteOnInvokeError',
                         (10,29) if success else (10,2))
        payload = b'\x83\x07' + guid(target) + struct.pack('<H',event) + encode_signed(request) + body + b'\0'
        send(1,payload)
        del self.pending[(target,request)]
        self.sent += 1
        self.errors_sent += not success

    def summary(self):
        return {'requests_received':self.received,'replies_sent':self.sent,
                'error_replies_sent':self.errors_sent,'pending_requests':len(self.pending)}
