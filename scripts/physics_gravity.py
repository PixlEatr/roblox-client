"""Apply received Workspace gravity to the actual local prediction solver.

The native Gravity registration at 100f2e104 names getter 100f1192c.
1040d015c compares the cached value with the solver accessor 1011ff8fc.
No constructor default or missing network value is treated as an observation.
"""
import math
import struct

from session_integrity import observe_physics_gravity


class ReceivedGravity:
    def __init__(self):
        self._controller = None
        self._expected = None

    def apply(self, workspace, controller, *, observe=True):
        raw = workspace.get('properties', {}).get('Gravity') if isinstance(workspace, dict) else None
        finite_number = type(raw) is int or (type(raw) is float and math.isfinite(raw))
        if not finite_number or controller is None:
            self._controller = None
            self._expected = None
            return
        # Native setters use float32 and clamp finite input to 0..1000.
        value = struct.unpack('<f', struct.pack('<f', min(1000.0, max(0.0, raw))))[0]
        if controller is not self._controller or value != self._expected:
            controller.gravity = value
            self._controller = controller
            self._expected = value
        # Read the same field that step_vertical consumes. Do not compare two
        # copied report fields or replace a changed solver value before checking.
        if observe:
            observe_physics_gravity(controller.gravity == self._expected)

    def report(self):
        return {'source': 'received_workspace' if self._expected is not None else None,
                'configured_gravity': self._expected,
                'comparison_available': self._expected is not None}
