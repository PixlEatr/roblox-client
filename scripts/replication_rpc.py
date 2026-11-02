"""One ordinary, zero-argument framework RemoteFunction initialization call.

Native evidence (build 0.745.0.7451137): InvokeServer 10401d9bc supplies
request ID, Players.LocalPlayer, and the Lua argument tuple to descriptor
106a33a58 / RemoteOnInvokeServer. 10401dc7c increments the per-instance
int32 counter (constructor 10401d6a4 initializes it to zero), so the first
request is 1. 104d80008 writes item 7, target GUID, LE16 schema event ID,
then schema-typed arguments. 104dae900 writes int32 via 1015f9774 (zigzag),
Instance via 104cd1eb8, and an empty Tuple as unsigned zero. 104f23d80
substitutes local scope only for a native GUID with scope zero; received
world references already have explicit nonzero scopes.

Success/error handlers 10401fa70/10401fbc8 use the same RemoteFunction's
pending-ID map (10401f004). Only the existing capability set 0x100000 is
supported here: legacy item numbering, no additional event fields.
This module neither sends traffic nor executes or reproduces game scripts.
The descriptor, references, and reply contents are private; summary() emits
structural state only. No retry or general-purpose remote invocation API.
"""
import struct

from login_packets import encode_signed
from replication_out import guid


SUPPORTED_CAPABILITIES = 0x100000
_EVENT_TYPES = {
    'RemoteOnInvokeServer': (10, 28, 29),
    'RemoteOnInvokeSuccess': (10, 29),
    'RemoteOnInvokeError': (10, 2),
}


def _class_name(world, instance):
    return world.schema['classes'][instance['class']]['name']


class FrameworkInitRpc:
    """Prepare at most one request; correlate only replies received afterward.

    spec is the private, statically verified call descriptor. Its first class
    identifies the unnamed ReplicatedStorage service; names align with the
    remaining child classes. The historical receiver GUID is never used.
    user_id must come from the current server-issued join response.
    """

    def __init__(self, spec, user_id, capability_bits=SUPPORTED_CAPABILITIES):
        if type(capability_bits) is not int or capability_bits != SUPPORTED_CAPABILITIES:
            raise ValueError('Unsupported RPC capability set')
        if type(user_id) is not int or user_id <= 0:
            raise ValueError('RPC requires the issued account identity')
        classes = spec.get('receiver_path_classes')
        names = spec.get('receiver_path_names')
        if (spec.get('method') != 'InvokeServer' or spec.get('arguments') != []
                or not isinstance(classes, list) or not isinstance(names, list)
                or not 2 <= len(classes) <= 16 or len(names) != len(classes) - 1
                or classes[0] != 'ReplicatedStorage' or classes[-1] != 'RemoteFunction'
                or any(type(v) is not str or not v for v in classes + names)):
            raise ValueError('Unsupported framework initialization descriptor')
        self._classes = tuple(classes)
        self._names = tuple(n.encode('utf-8') for n in names)
        self._user_id = user_id
        self._world = None
        self._target = None
        self._event_ids = None
        self._last_event = None
        self._request_id = 1
        self._prepared_bytes = 0
        self._examined_events = 0
        self.state = 'waiting'
        self.result = None  # Private returned Tuple, never included in summary.
        self.error = None  # Private server error string, never included in summary.

    def _target_in(self, world):
        roots = [k for k, v in world.instances.items()
                 if _class_name(world, v) == self._classes[0]]
        if len(roots) > 1:
            raise ValueError('Ambiguous framework root')
        if not roots or roots[0] not in world.top_containers:
            return None
        target = roots[0]
        for name, cls in zip(self._names, self._classes[1:]):
            matches = [k for k, v in world.instances.items()
                       if v.get('parent') == target and v['properties'].get('Name') == name]
            if len(matches) > 1:
                raise ValueError('Ambiguous framework path')
            if not matches:
                return None
            target = matches[0]
            if _class_name(world, world.instances[target]) != cls:
                raise ValueError('Framework path class mismatch')
        return target

    def _own_player_in(self, world):
        # 10401d9bc obtains its hidden Player through 103c1f934: find the
        # Players service (100083b58/100084acc/100b029f0), then read +264.
        # The data-only equivalent uses only the currently issued identity
        # and an already replicated Player parented to that service.
        players = [k for k, v in world.instances.items()
                   if _class_name(world, v) == 'Player'
                   and v['properties'].get('UserId') == self._user_id]
        if len(players) > 1:
            raise ValueError('Ambiguous issued player identity')
        if not players:
            return None
        parent = world.instances[players[0]].get('parent')
        if (parent not in world.top_containers or parent not in world.instances
                or _class_name(world, world.instances[parent]) != 'Players'):
            return None
        return players[0]

    def _schema_events(self, world, target):
        ci = world.instances[target]['class']
        cls = world.schema['classes'][ci]
        resolved = {}
        for name, types in _EVENT_TYPES.items():
            found = [i for i in cls['events'] if world.schema['events'][i]['name'] == name]
            if len(found) != 1:
                raise ValueError('Missing or ambiguous RPC schema event')
            i = found[0]
            event = world.schema['events'][i]
            if (type(i) is not int or not 0 <= i <= 65535 or event['id'] != i
                    or event['class'] != ci
                    or tuple(a['type'] for a in event['arguments']) != types):
                raise ValueError('Unsupported RPC schema event signature')
            resolved[name] = i
        return resolved

    def prepare(self, world):
        """Return one app4/channel1 payload, or None while waiting/already used."""
        if self.state != 'waiting' or world is None:
            return None
        if world.client_scope is None or 13 not in world.tags:
            return None
        target, player = self._target_in(world), self._own_player_in(world)
        if target is None or player is None:
            return None
        events = self._schema_events(world, target)
        payload = (b'\x83\x07' + guid(target)
                   + struct.pack('<H', events['RemoteOnInvokeServer'])
                   + encode_signed(self._request_id) + guid(player) + b'\0\0')
        self._world, self._target, self._event_ids = world, target, events
        self._last_event = world.events[-1] if world.events else None
        self._prepared_bytes = len(payload)
        self.state = 'pending'
        return payload

    def poll(self, world):
        """Return True once a matching reply arrives; retain its value privately."""
        if self.state != 'pending':
            return False
        if world is not self._world:
            raise ValueError('RPC cannot cross world sessions')
        events = list(world.events)
        start = 0
        if self._last_event is not None:
            start = next((i + 1 for i, e in enumerate(events) if e is self._last_event), None)
            if start is None:
                raise ValueError('RPC event journal overflow')
        for event in events[start:]:
            self._examined_events += 1
            if event['key'] != self._target:
                continue
            success = event['event'] == self._event_ids['RemoteOnInvokeSuccess']
            failure = event['event'] == self._event_ids['RemoteOnInvokeError']
            if not success and not failure:
                continue
            args = event['arguments']
            if not isinstance(args, list) or not args or type(args[0]) is not int:
                raise ValueError('Malformed RPC response')
            if args[0] != self._request_id:
                continue
            if len(args) != 2 or not isinstance(args[1], list if success else bytes):
                raise ValueError('Malformed RPC response value')
            if success:
                self.result, self.state = args[1], 'succeeded'
            else:
                self.error, self.state = args[1], 'server_error'
            self._last_event = event
            return True
        if events:
            self._last_event = events[-1]
        return False

    def summary(self):
        return {'state': self.state, 'requests_prepared': int(self._prepared_bytes != 0),
                'request_bytes': self._prepared_bytes, 'events_examined': self._examined_events,
                'matching_reply_received': self.state in ('succeeded', 'server_error'),
                'succeeded': self.state == 'succeeded'}
