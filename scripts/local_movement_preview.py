"""Connect authored Luau movement intentions to a separate local prediction.

The prediction never modifies received world evidence or sends network packets.
It is a horizontal walking preview, not Roblox character physics.
"""
import math
from movement_controller import Controller
from movement_camera import camera_movement_error, movement_direction


class LocalMovementPreview:
    def __init__(self, snapshot):
        self.controller = None
        self.target = None
        self.accepted = 0
        self.rejected = 0
        self.last_reason = None
        self.last_sequence = 0
        self.simulated_seconds = 0.
        nodes = {node['id']: node for node in snapshot['nodes']}
        self.player_id = snapshot.get('local_player')
        player = nodes.get(self.player_id, {})
        character = player.get('properties', {}).get('Character', {})
        character_id = character.get('$ref') if isinstance(character, dict) else None
        humanoid = next((n for n in nodes.values() if n.get('parent') == character_id
                         and n['class_name'] == 'Humanoid'), {}) if character_id else {}
        root = next((n for n in nodes.values() if n.get('parent') == character_id
                     and n.get('name') == 'HumanoidRootPart'), {}) if character_id else {}
        self.humanoid_id = humanoid.get('id')
        properties = root.get('properties', {})
        self.anchored = properties.get('Anchored')
        position = properties.get('Position', {}).get('$vector3')
        if position is None:
            position = properties.get('CFrame', {}).get('$cframe', {}).get('position')
        if position is not None and self.humanoid_id:
            speed = humanoid.get('properties', {}).get('WalkSpeed', 16)
            try:
                self.controller = Controller(position, speed)
            except ValueError:
                self.last_reason = 'unsupported_initial_pose_or_speed'
        else:
            self.last_reason = 'character_not_resolved'

    def consume(self, intents):
        for intent in intents:
            sequence = intent['sequence']
            if sequence <= self.last_sequence:
                raise ValueError('Movement intentions must be consumed once, in order')
            self.last_sequence = sequence
            if intent['instance_id'] not in (self.player_id, self.humanoid_id):
                reason = 'different_character'
            elif self.controller is None:
                reason = 'character_not_resolved'
            elif self.anchored is True:
                reason = 'anchored'
            elif camera_movement_error(intent):
                reason = camera_movement_error(intent)
            else:
                reason = None
            if reason:
                self.rejected += 1
                self.last_reason = reason
                continue
            if intent['kind'] == 'Move':
                self.controller.set_direction(movement_direction(intent))
                self.target = None
            elif intent['kind'] == 'MoveTo':
                # Controller validation is also used for target coordinates.
                target = Controller(intent['vector']).position
                self.target = target
                x, _, z = self.controller.position
                self.controller.set_direction((target[0]-x, 0, target[2]-z), normalize=True)
            else:
                raise ValueError('Unknown native movement intention')
            self.accepted += 1
            self.last_reason = None

    def step(self, dt):
        if self.controller is None:
            return self.report()
        # Bound even stopped steps, and avoid an unbounded simulation catch-up.
        if not isinstance(dt, (int, float)) or isinstance(dt, bool) or not math.isfinite(dt) or not 0 <= dt <= .25:
            raise ValueError('Preview dt must be finite and within 0..0.25 seconds')
        remaining = dt
        if self.target is not None:
            x, _, z = self.controller.position
            distance = math.hypot(self.target[0]-x, self.target[2]-z)
            speed = self.controller.walk_speed
            if speed > 0:
                remaining = min(dt, distance/speed)
        self.controller.step(remaining)
        self.simulated_seconds += remaining
        if self.target is not None:
            x, _, z = self.controller.position
            if math.hypot(self.target[0]-x, self.target[2]-z) <= 1e-8:
                self.controller.stop()
                self.target = None
        return self.report()

    def report(self):
        return {'mode': 'local_prediction', 'server_confirmed': False,
                'network_packets_sent': 0, 'accepted_intents': self.accepted,
                'rejected_intents': self.rejected, 'last_reason': self.last_reason,
                'predicted_position': list(self.controller.position) if self.controller else None,
                'simulated_seconds': self.simulated_seconds}
