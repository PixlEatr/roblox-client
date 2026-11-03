"""Session-local CFrame receipt acknowledgments (ordinary item 10).

Studio 0.745: 1060906D0 queues the item for the schema's designated property;
106090A78 resets/increments its receipt counter. 106096624 writes the GUID,
property ID, optional counter, and local simulation step. This synchronizes
property receipt; it is neither an integrity report nor proof of PV acceptance.
"""
import struct

from game_wire import encode_unsigned
from replication_out import guid


def encode_property_ack(key, property_id, version, local_step):
    for value, maximum, name in ((property_id, 65534, 'property'),
                                 (version, 0xffffffff, 'version'),
                                 (local_step, 0xffffffff, 'local step')):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError('Invalid property acknowledgment '+name)
    if key is None:
        raise ValueError('Property acknowledgment requires a received instance')
    return (b'\x83\x0a' + guid(key) + struct.pack('<H', property_id)
            + b'\x01' + encode_unsigned(version) + struct.pack('<I', local_step) + b'\0')


class PropertyAcknowledgments:
    """Coalesce unsent receipts, retaining only counters from this world.

    Native sends the latest unsent counter once; redundant queued native items
    carry a false version-present byte. We omit those redundant no-op items.
    Failed/ambiguous transport submissions are terminal for this queue.
    """
    def __init__(self, world, *, max_entries=100000):
        self.world = world
        self.max_entries = max_entries
        self.versions = {}
        self.pending = {}
        self.observed = self.resets = self.sent = self.packets_sent = 0
        self.error = None

    def observe(self, *, local_step):
        if type(local_step) is not int or not 0 <= local_step <= 0xffffffff:
            raise ValueError('Property receipts require a local simulation step')
        world = self.world
        changes = world.received_property_changes
        world.received_property_changes = ()
        for key, pi, reset, instance in changes:
            if world.instances.get(key) is not instance:
                continue
            cls = world.schema['classes'][instance['class']]
            if cls.get('unknown_u16', 65535) != pi:
                continue
            prop = world.schema['properties'][pi]
            # Current schema maps physical classes to CFrame. Fail closed for
            # a future different designation until its class semantics are known.
            if prop['name'] != 'CFrame' or prop['type'] != 27:
                continue
            token = (key, pi)
            previous = self.versions.get(token)
            version = previous[1] if previous is not None and previous[0] is instance else 0
            version = 0 if reset else (version + 1) & 0xffffffff
            if token not in self.versions and len(self.versions) >= self.max_entries:
                self.prune()
                if len(self.versions) >= self.max_entries:
                    raise ValueError('Too many property receipt counters')
            self.versions[token] = (instance, version)
            self.pending[token] = (instance, version, local_step)
            self.observed += 1
            self.resets += int(reset)

    def prune(self):
        for token, (instance, _) in list(self.versions.items()):
            if self.world.instances.get(token[0]) is not instance:
                self.versions.pop(token)
                self.pending.pop(token, None)

    def flush(self, send):
        if self.error is not None:
            return
        items, tokens = [], []
        size = 2
        def submit():
            try:
                send(1, b'\x83'+b''.join(items)+b'\0')
            except Exception:
                self.error = 'transport_submission_ambiguous_no_retry'
                raise
            for token in tokens:
                self.pending.pop(token)
            self.sent += len(tokens)
            self.packets_sent += 1
        for token, (instance, version, step) in list(self.pending.items()):
            key, pi = token
            if self.world.instances.get(key) is not instance:
                self.pending.pop(token)
                self.versions.pop(token, None)
                continue
            body = encode_property_ack(key, pi, version, step)[1:-1]
            if items and size+len(body) > 16384:
                submit()
                items, tokens, size = [], [], 2
            items.append(body)
            tokens.append(token)
            size += len(body)
        if items:
            submit()

    def summary(self):
        return {'received_cframe_changes': self.observed, 'received_resets': self.resets,
                'replies_sent': self.sent, 'packets_sent': self.packets_sent,
                'pending': len(self.pending),
                'tracked_properties': len(self.versions), 'error': self.error,
                'source': 'current_session_received_property_changes',
                'clock_source': 'completed_local_simulation_intervals',
                'server_physics_acceptance_confirmed': False}
