"""Observed completion intervals and configuration for this runtime's physics job.

Native 102c0f9a0 -> 102c0fb84 updates the interval average at job +176.
1008e94d8 identifies +176 as its coefficient, not a simulation timestep.
104029408 checks that coefficient and the native world-bounds configuration.
These measurements do not stand in for RakNet or packet queue statistics.
"""
import math
import struct
import time

from session_integrity import observe_physics_smoothing, observe_physics_world_bounds


DEFAULT_WORLD_BOUNDS = (-1000000.0, -1000000.0, -1000000.0,
                        1000000.0, 1000000.0, 1000000.0)


def native_world_bounds_match(bounds):
    if (not isinstance(bounds, tuple) or len(bounds) != 6
            or any(type(value) not in (int, float) for value in bounds)):
        raise ValueError('Physics world bounds must contain six numbers')
    try:
        minimum_x, minimum_y, minimum_z, maximum_x, maximum_y, _ = struct.unpack(
            '<6f', struct.pack('<6f', *bounds))
    except (OverflowError, struct.error):
        raise ValueError('Physics world bounds must be representable as float32') from None
    return not (minimum_x > -950000.0 or minimum_y > -960000.0
                or minimum_z > -970000.0 or maximum_x < 960000.0
                or maximum_y < 970000.0)


class PhysicsJobStatistics:
    def __init__(self, *, clock=None):
        self.clock = clock or time.monotonic
        self.smoothing = 0.05
        self.world_bounds = DEFAULT_WORLD_BOUNDS
        self._last_completion = None
        self.completed_jobs = 0
        self.samples = 0
        self.last_interval = None
        self.mean_interval = None
        self.variance = 0.0

    def completed(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('Physics completion clock must be finite')
        if self._last_completion is not None and now < self._last_completion:
            raise ValueError('Physics completion clock moved backward')
        coefficient = self.smoothing
        if type(coefficient) not in (int, float) or not math.isfinite(coefficient):
            raise ValueError('Physics interval coefficient must be finite')
        bounds_match = native_world_bounds_match(self.world_bounds)
        observe_physics_smoothing(0.049 <= coefficient <= 0.051)
        observe_physics_world_bounds(bounds_match)
        if self._last_completion is not None:
            interval = now - self._last_completion
            mean = (interval if self.mean_interval is None else
                    coefficient * interval + (1.0 - coefficient) * self.mean_interval)
            self.variance = ((interval - mean) ** 2 * coefficient
                             + (1.0 - coefficient) * self.variance)
            self.mean_interval = mean
            self.last_interval = interval
            self.samples += 1
        self._last_completion = now
        self.completed_jobs += 1

    def report(self):
        return {'source': 'local_physics_job_completions',
                'completed_jobs': self.completed_jobs,
                'interval_samples': self.samples,
                'smoothing_coefficient': self.smoothing,
                'world_bounds': list(self.world_bounds),
                'last_interval_seconds': self.last_interval,
                'mean_interval_seconds': self.mean_interval,
                'interval_variance_seconds_squared': self.variance}
