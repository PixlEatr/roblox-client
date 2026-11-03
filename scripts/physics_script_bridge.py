"""Route authored runtime movement intents to the owned local physics model.

This bridge is deliberately local: it does not encode or send network traffic.
Sequence IDs and receiver IDs from the runtime are preserved as supplied.
"""
from __future__ import annotations

from typing import Callable
import copy
import math
from movement_camera import camera_movement_error
from physics_forces import collect_received_force_sources, predicted_assembly_mass, received_character_motion_parts
from authored_force_state import AuthoredForceState

_AUTHORED_PROPERTIES = frozenset(('WalkSpeed', 'AutoRotate', 'Health', 'Sit', 'HipHeight', 'LeftLeg', 'PlatformStand'))


def _owned_receivers(snapshot):
    """Return local Player/Humanoid IDs and a stable character identity."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('nodes'), list):
        raise ValueError('snapshot must contain a nodes list')
    nodes = {node['id']: node for node in snapshot['nodes']
             if isinstance(node, dict) and 'id' in node}
    player_id = snapshot.get('local_player')
    player = nodes.get(player_id, {})
    character = player.get('properties', {}).get('Character', {})
    character_id = character.get('$ref') if isinstance(character, dict) else None
    humanoid = next((node for node in nodes.values()
                     if node.get('parent') == character_id
                     and node.get('class_name') == 'Humanoid'), {}) if character_id else {}
    root = next((node for node in nodes.values()
                 if node.get('parent') == character_id
                 and node.get('class_name') in ('Part', 'MeshPart', 'UnionOperation')
                 and node.get('name') == 'HumanoidRootPart'), {}) if character_id else {}
    return player_id, humanoid.get('id'), character_id, root.get('id')


class PhysicsScriptBridge:
    """Exactly-once adapter from runtime intents to an owned local controller.

    ``controller_factory`` receives the current world snapshot and may be used
    to inject a compatible controller implementation. Camera-relative Move
    records require a valid transform captured at call time by the runtime.
    """

    def __init__(self, controller_factory: Callable | None = None):
        if controller_factory is None:
            from physics_controller import OwnCharacterPhysicsController
            controller_factory = OwnCharacterPhysicsController
        if not callable(controller_factory):
            raise TypeError('controller_factory must be callable')
        self._factory = controller_factory
        self._property_overrides = {}
        self._force_state = AuthoredForceState()
        self._properties_applied = 0
        self._property_write_sequence = 0
        self._stale_property_writes = 0
        self.controller = None
        self.snapshot = None
        self._character_key = None
        self._last_sequence = 0
        self._forwarded = 0
        self._accepted = 0
        self._rejected = 0
        self._connected = False
        self._last_controller_state = None

    @property
    def last_sequence(self):
        return self._last_sequence

    def update(self, snapshot, *, connected=True):
        """Attach/update the local world; drop state on disconnect/replacement."""
        if not connected or snapshot is None:
            self.clear()
            return {'connected': False, 'reason': 'disconnected'}
        player_id, humanoid_id, character_id, root_id = _owned_receivers(snapshot)
        key = (player_id, humanoid_id, character_id, root_id)
        if not player_id or not character_id or not humanoid_id:
            self.clear()
            return {'connected': False, 'reason': 'character_not_resolved'}
        replaced = not self._connected or key != self._character_key
        same_humanoid = (self._character_key is not None
                         and self._character_key[1] == humanoid_id
                         and self._character_key[2] == character_id)
        enabled_states = (getattr(self.controller, 'state_enabled', {})
                          if replaced and same_humanoid and self.controller is not None else {})
        if replaced:
            self._property_overrides.clear()
            if not same_humanoid:self._force_state.clear()
        self._character_key = key
        self.snapshot = snapshot
        self._force_state.invalidate(snapshot)
        effective = self._effective_snapshot(invalidate=True)
        if replaced:
            self.controller = self._factory(effective)
            restore = getattr(self.controller, 'restore_state_enabled', None)
            if enabled_states and callable(restore):
                restore(enabled_states)
            self._connected = True
            self._last_controller_state = None
        else:
            self.controller.update_snapshot(effective)
        self._refresh_force_sources()
        return {'connected': True, 'character_key': key}

    def _refresh_force_sources(self):
        update = getattr(self.controller, 'update_force_sources', None)
        if callable(update):
            _, _, _, root_id = self._character_key
            force_snapshot=self._effective_snapshot()
            mass, mass_source = predicted_assembly_mass(force_snapshot, root_id)
            update(collect_received_force_sources(force_snapshot, root_id,
                                                  allow_authored=True),
                   mass=mass, mass_source=mass_source)

    def clear(self):
        """Forget controller/character state while retaining sequence watermark."""
        self.controller = None
        self.snapshot = None
        self._character_key = None
        self._property_overrides.clear()
        self._force_state.clear()
        self._connected = False
        self._last_controller_state = None

    def _effective_snapshot(self, *, invalidate=False):
        """Overlay authored control values on a detached controller view only."""
        snapshot = self._force_state.snapshot(self.snapshot)
        snapshot['allow_authored_motion']=True
        snapshot['motion_topology_history']=getattr(self.controller,'_motion_topology_history',())
        _,_,character_id,root_id=self._character_key
        snapshot['motion_topology_changed']=(
            received_character_motion_parts(snapshot,root_id,character_id)!=
            received_character_motion_parts(self.snapshot,root_id,character_id))
        humanoid_id = self._character_key[1]
        node = next(n for n in snapshot['nodes'] if n.get('id') == humanoid_id)
        properties = node.setdefault('properties', {})
        versions = node.get('property_revisions', {})
        for name, entry in list(self._property_overrides.items()):
            if invalidate and (versions.get(name) != entry['revision']
                               or (name in properties) != entry['present']
                               or properties.get(name) != entry['received_value']):
                # A later receipt supersedes the override even when it repeats
                # the old received value. Unrelated receipts leave it intact.
                del self._property_overrides[name]
                continue
            properties[name] = copy.deepcopy(entry['value'])
            node.setdefault('origins', {})[name] = 'local'
            node.setdefault('authored_control_generations',{})[name]=entry['generation']
        return snapshot

    def apply_mutations(self, mutations, *, observed_revision=None):
        """Apply supported native property writes to this owned prediction.

        Native output has already passed datatype/mutation budgets. Keep even
        unsupported numeric values here: the controller must stop invalid
        movement instead of silently retaining an older speed.
        """
        if observed_revision is not None and (type(observed_revision) not in (int, float)
                or not math.isfinite(observed_revision) or observed_revision < 0
                or observed_revision != int(observed_revision)):
            raise ValueError('worker revision must be a non-negative integer')
        if not isinstance(mutations, (list, tuple)) or len(mutations) > 10000:
            raise ValueError('mutations must be a bounded list or tuple')
        if not self._connected or self.controller is None:
            return 0
        humanoid_id = self._character_key[1]
        node = next(n for n in self.snapshot['nodes'] if n.get('id') == humanoid_id)
        properties = node.get('properties', {})
        versions = node.get('property_revisions', {})
        force_applied = self._force_state.apply(mutations,self.snapshot,observed_revision)
        applied = force_applied
        for item in mutations:
            if not isinstance(item, dict) or item.get('kind') != 'set' or item.get('id') != humanoid_id:
                continue
            name = item.get('property')
            if not isinstance(name, str) or name not in _AUTHORED_PROPERTIES or 'value' not in item:
                continue
            if observed_revision is not None and versions.get(name, 0) > observed_revision:
                # The worker ran against an older receipt snapshot. Do not
                # overwrite a newer property already applied to prediction.
                self._stale_property_writes += 1
                continue
            self._property_write_sequence+=1
            self._property_overrides[name] = {
                'generation':self._property_write_sequence,
                'value': copy.deepcopy(item['value']), 'revision': versions.get(name),
                'present': name in properties, 'received_value': copy.deepcopy(properties.get(name)),
            }
            applied += 1
        if applied:
            self.controller.update_snapshot(self._effective_snapshot())
            self._refresh_force_sources()
            self._properties_applied += applied-force_applied
        return applied

    def consume_batch(self, mutations, intents, *, observed_revision=None, after_mutations=None):
        """Replay one native response's mutation prefixes and movement in order.

        Older hosts without cursors retain the previous all-writes-first path.
        Reject malformed/mixed cursor batches before changing local state.
        """
        if not isinstance(mutations,(list,tuple)) or len(mutations)>10000:
            raise ValueError('mutations must be a bounded list or tuple')
        if not isinstance(intents,(list,tuple)) or len(intents)>256:
            raise ValueError('intents must be a bounded list or tuple')
        cursors=[];sequence=self._last_sequence
        for item in intents:
            if not isinstance(item,dict):raise TypeError('movement intent must be a dictionary')
            current=item.get('sequence')
            if type(current) is not int or current<=sequence:raise ValueError('movement sequence must increase exactly once')
            sequence=current
            if 'mutation_cursor' in item:cursors.append(item['mutation_cursor'])
        if cursors and len(cursors)!=len(intents):raise ValueError('mixed mutation cursor batch')
        previous=0
        for cursor in cursors:
            if type(cursor) is not int or not previous<=cursor<=len(mutations):
                raise ValueError('mutation cursors must be ordered within the response')
            previous=cursor
        def apply(items):
            if items:
                self.apply_mutations(items,observed_revision=observed_revision)
                if after_mutations is not None:after_mutations()
        if not cursors:
            apply(mutations);return self.consume(intents)
        previous=0;outcomes=[]
        for item,cursor in zip(intents,cursors):
            apply(mutations[previous:cursor]);previous=cursor
            outcomes.extend(self.consume([item]))
        apply(mutations[previous:])
        return outcomes

    def consume(self, intents):
        """Consume an ordered batch once, preserving each original receiver ID.

        Sequence gaps are allowed (e.g. other runtime events interleaved), but
        duplicate or reordered records fail closed. A record is marked consumed
        before delegation so an ambiguous controller exception cannot cause a
        duplicate on retry.
        """
        if not isinstance(intents, (list, tuple)):
            raise TypeError('intents must be a list or tuple')
        outcomes = []
        if not self._connected or self.controller is None:
            self._rejected += len(intents)
            return [{'sequence': item.get('sequence') if isinstance(item, dict) else None,
                     'accepted': False, 'reason': 'disconnected'} for item in intents]
        player_id, humanoid_id, _, root_id = self._character_key
        motion_parts=received_character_motion_parts(self._effective_snapshot(),root_id,self._character_key[2])
        for intent in intents:
            if not isinstance(intent, dict):
                raise TypeError('movement intent must be a dictionary')
            sequence = intent.get('sequence')
            if type(sequence) is not int or sequence <= self._last_sequence:
                raise ValueError('movement sequence must increase exactly once')
            self._last_sequence = sequence
            receiver = intent.get('instance_id')
            reason = None
            if receiver not in (player_id, humanoid_id, root_id) and not (
                    intent.get('kind') in ('Impulse','SetLinearVelocity') and receiver in motion_parts):
                reason = 'different_character'
            elif intent.get('kind') == 'ChangeState' and receiver != humanoid_id:
                reason = 'state_change_requires_humanoid'
            elif intent.get('kind') == 'SetStateEnabled' and receiver != humanoid_id:
                reason = 'state_change_requires_humanoid'
            elif intent.get('kind') in ('Move', 'MoveTo'):
                reason = camera_movement_error(intent)
            elif intent.get('kind') not in ('Move', 'MoveTo', 'Jump', 'Impulse', 'SetLinearVelocity',
                                             'ChangeState', 'SetStateEnabled'):
                reason = 'unsupported_intent'
            if reason:
                self._rejected += 1
                outcomes.append({'sequence': sequence, 'instance_id': receiver,
                                 'accepted': False, 'reason': reason})
                continue
            # Pass a shallow copy with every field, including actual receiver,
            # unchanged. Do not synthesize Player/Humanoid targets.
            accepted_before = getattr(self.controller, 'accepted', None)
            rejected_before = getattr(self.controller, 'rejected', None)
            self._forwarded += 1
            self.controller.consume([dict(intent)])
            accepted_after = getattr(self.controller, 'accepted', None)
            rejected_after = getattr(self.controller, 'rejected', None)
            # The built-in controller exposes its decision counters. Preserve
            # compatibility with injected controllers that only accept intents.
            was_rejected = (isinstance(rejected_before, int)
                            and isinstance(rejected_after, int)
                            and rejected_after > rejected_before)
            was_accepted = (isinstance(accepted_before, int)
                            and isinstance(accepted_after, int)
                            and accepted_after > accepted_before)
            accepted = not was_rejected
            reason = getattr(self.controller, 'last_reason', None) if was_rejected else None
            if accepted:
                self._accepted += 1
                outcomes.append({'sequence': sequence, 'instance_id': receiver,
                                 'accepted': True, 'reason': None})
            else:
                self._rejected += 1
                outcomes.append({'sequence': sequence, 'instance_id': receiver,
                                 'accepted': False, 'reason': reason or 'controller_rejected'})
        return outcomes

    def step(self, dt, timestamp=None):
        if not self._connected or self.controller is None:
            return {'connected': False, 'reason': 'disconnected'}
        self._last_controller_state = self.controller.step(dt, timestamp=timestamp)
        return self._last_controller_state

    def report(self):
        reporter = getattr(self.controller, 'report', None) if self.controller is not None else None
        state = reporter() if callable(reporter) else self._last_controller_state
        return {'mode': 'local_prediction', 'server_confirmed': False,
                'network_packets_sent': 0, 'connected': self._connected,
                'authored_forces': {'local_nodes':len(self._force_state.local_nodes),
                                    'overrides':len(self._force_state.overrides),
                                    'hidden_nodes':len(self._force_state.removed),
                                    'applied_writes':self._force_state.applied_writes,
                                    'stale_writes_ignored':self._force_state.stale_writes},
                'authored_properties': {'active': len(self._property_overrides),
                                        'applied_writes': self._properties_applied,
                                        'stale_writes_ignored': self._stale_property_writes,
                                        'properties': sorted(self._property_overrides)},
                'last_sequence': self._last_sequence, 'forwarded_intents': self._forwarded,
                'accepted_intents': self._accepted,
                'rejected_intents': self._rejected,
                'controller': state}
