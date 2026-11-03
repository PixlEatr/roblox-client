"""Ordinary InvokeServer requests, correlated within one received world.

Wire layout and hidden Player are documented in replication_rpc.py. Local VM
tokens are separate from per-receiver wire counters. No speculative replies,
retries, or completion deadlines are used. Primitive variants only for now.
"""
import collections
import struct

from game_wire import encode_unsigned
from login_packets import encode_signed
from remote_client_rpc import event_id, parse_key, variant
from replication_out import guid
from world_snapshot import _encode


class RemoteServerRpc:
    def __init__(self, user_id, capabilities=0x100000, *, disabled_reason=None):
        if type(user_id) is not int or user_id <= 0 or capabilities != 0x100000:
            raise ValueError('Unsupported remote invocation session')
        self.user_id = user_id
        self.disabled_reason = disabled_reason
        self.world = None
        self.sequence = 0
        self.counters = {}
        self.pending = {}
        self.sent = self.succeeded = self.server_errors = self.cancelled = 0
        self.rejected = collections.Counter()

    def bind(self, world):
        if world is None:
            raise ValueError('Remote invocation world is not ready')
        if self.world is None:
            self.world = world
            self.sequence = world.event_sequence
            world.preserve_remote_success_variants = True
        if world is not self.world:
            raise ValueError('Remote invocations cannot cross world sessions')

    def player(self, world):
        candidates = [key for key, node in world.instances.items()
                      if world.schema['classes'][node['class']]['name'] == 'Player'
                      and node['properties'].get('UserId') == self.user_id]
        if len(candidates) != 1:
            raise ValueError('Issued Player is missing or ambiguous')
        parent = world.instances[candidates[0]].get('parent')
        if (parent not in world.top_containers or parent not in world.instances
                or world.schema['classes'][world.instances[parent]['class']]['name'] != 'Players'):
            raise ValueError('Issued Player is not parented to Players')
        return candidates[0]

    def send(self, world, call, transport):
        self.bind(world)
        if self.disabled_reason:
            raise ValueError(self.disabled_reason)
        target = parse_key(call.get('node'))
        node = world.instances.get(target)
        if node is None or world.schema['classes'][node['class']]['name'] != 'RemoteFunction':
            raise ValueError('RemoteFunction receiver is not present in this session')
        token = call.get('request_id')
        if type(token) is not int or not 0 < token < 2**63:
            raise ValueError('Invalid runtime invocation token')
        if any(p['request_id'] == token for p in self.pending.values()):
            raise ValueError('Duplicate runtime invocation token')
        if len(self.pending) >= 512:
            raise ValueError('Remote invocation pending limit exceeded')
        values = call.get('arguments')
        if not isinstance(values, list) or len(values) > 256:
            raise ValueError('Invalid remote invocation tuple')
        descriptor = event_id(world, target, 'RemoteOnInvokeServer', (10, 28, 29))
        success = event_id(world, target, 'RemoteOnInvokeSuccess', (10, 29))
        failure = event_id(world, target, 'RemoteOnInvokeError', (10, 2))
        body = encode_unsigned(len(values)) + b''.join(variant(world, v) for v in values)
        if len(body) > 65536:
            raise ValueError('Remote invocation tuple exceeds limit')
        request = self.counters.get(target, 0) + 1
        if request >= 2**31:
            raise ValueError('Remote invocation wire counter exhausted')
        payload = (b'\x83\x07' + guid(target) + struct.pack('<H', descriptor)
                   + encode_signed(request) + guid(self.player(world)) + body + b'\0')
        # Reserve before submission: an ambiguous transport failure must never
        # reuse the same ID. The session owner treats transport errors as fatal.
        self.counters[target] = request
        self.pending[target, request] = dict(node=call['node'], request_id=token,
                                            receiver=node, success=success, failure=failure,
                                            after=world.event_sequence)
        if transport(1, payload) is False:
            raise OSError('Remote invocation transport did not confirm submission')
        self.sent += 1

    def observe(self, world, results, active=None):
        self.bind(world)
        for key, pending in list(self.pending.items()):
            if active is not None and pending['request_id'] not in active:
                del self.pending[key]
                self.cancelled += 1
            elif world.instances.get(key[0]) is not pending['receiver']:
                results.append(dict(node=pending['node'], request_id=pending['request_id'],
                                    ok=False, error='RemoteFunction receiver was removed'))
                del self.pending[key]
        journal = list(world.event_journal)
        if journal and journal[0][0] > self.sequence + 1:
            raise ValueError('Remote invocation event journal was overrun')
        for sequence, event in journal:
            if sequence <= self.sequence:
                continue
            args = event['arguments']
            if len(args) != 2 or type(args[0]) is not int:
                continue
            key = (event['key'], args[0])
            pending = self.pending.get(key)
            if pending is None or sequence <= pending['after']:
                continue
            success = event['event'] == pending['success']
            failure = event['event'] == pending['failure']
            if not success and not failure:
                continue
            response = dict(node=pending['node'], request_id=pending['request_id'], ok=success)
            if success:
                if not isinstance(args[1], list) or len(args[1]) > 256:
                    raise ValueError('Invalid RemoteFunction success tuple')
                response['values'] = _encode(args[1], 29)
                self.succeeded += 1
            else:
                if not isinstance(args[1], bytes):
                    raise ValueError('Invalid RemoteFunction error value')
                response['error'] = args[1].decode('utf-8', errors='replace')[:4096]
                self.server_errors += 1
            results.append(response)
            del self.pending[key]
        self.sequence = world.event_sequence

    def flush(self, world, runtime, transport):
        if world is None:
            return
        self.observe(world, runtime.remote_server_results, runtime.active_remote_server_requests)
        queue = runtime.remote_server_calls
        for _ in range(min(16, len(queue))):
            call = queue.popleft()
            if call['request_id'] not in runtime.active_remote_server_requests:
                continue
            try:
                self.send(world, call, transport)
            except ValueError as error:
                self.rejected[str(error)] += 1
                runtime.remote_server_results.append(dict(node=call['node'], request_id=call['request_id'],
                                                         ok=False, error=str(error)))
        if len(runtime.remote_server_results) > 512:
            raise ValueError('Remote invocation result queue exceeded limit')

    def summary(self):
        return dict(requests_sent=self.sent, success_replies_received=self.succeeded,
                    error_replies_received=self.server_errors, pending=len(self.pending),
                    cancelled=self.cancelled, rejected=dict(self.rejected))
