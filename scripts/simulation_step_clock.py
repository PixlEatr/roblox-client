"""Local model clock advanced only by completed simulation intervals.

This is a simulation clock, not measured server telemetry or a peer clock
estimate. The unit rate is explicit; choosing 240 for an ISR experiment is a
local model policy, not proof of parity with every native scheduler mode.
"""
import math


class SimulationStepClock:
    def __init__(self, *, units_per_second):
        if type(units_per_second) is not int or not 1 <= units_per_second <= 1000:
            raise ValueError('Simulation clock requires an explicit bounded unit rate')
        self.units_per_second = units_per_second
        self.whole_units = 0
        self.fractional_units = 0.
        self.completed_intervals = 0

    def advance(self, simulated_delta):
        if (type(simulated_delta) not in (int, float) or not math.isfinite(simulated_delta)
                or not 0 <= simulated_delta <= .25):
            raise ValueError('Simulation clock requires a completed bounded interval')
        total = self.fractional_units + simulated_delta*self.units_per_second
        whole = math.floor(total)
        self.fractional_units = total-whole
        self.whole_units += whole
        self.completed_intervals += 1

    @property
    def step(self):
        return self.whole_units & 0xffffffff

    def report(self):
        return {'source':'completed_local_simulation_intervals', 'step':self.step,
                'units_per_second':self.units_per_second,
                'completed_intervals':self.completed_intervals,
                'native_scheduler_parity_verified':False}
