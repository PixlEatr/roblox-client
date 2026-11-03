"""A paced realtime physics job, with state used by the actual scheduler.

Native 102c0cb80 selects scheduler +184 when its +8 mode bit is set;
the realtime fallback uses 1/240 seconds. The constructor's configured
interval is 1/60. This is unrelated to the job average coefficient at +176.
"""
import math

from session_integrity import observe_physics_scheduler


class PhysicsJobScheduler:
    def __init__(self):
        self.configured_interval_enabled = True
        self.configured_interval_seconds = 1.0 / 60.0
        self._deadline_us = None
        self._last_clock_us = None
        self.admitted_jobs = 0
        self.skipped_intervals = 0

    def admit(self, now):
        if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now < 2**44:
            raise ValueError('Physics scheduler requires a bounded monotonic clock')
        if type(self.configured_interval_enabled) is not bool:
            raise ValueError('Physics scheduler mode must be a boolean')
        interval = self.configured_interval_seconds
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 0.001 <= interval <= 1:
            raise ValueError('Configured physics interval must be within 1..1000 ms')
        now_us = int(now * 1_000_000)
        if self._last_clock_us is not None and now_us < self._last_clock_us:
            raise ValueError('Physics scheduler clock moved backward')
        self._last_clock_us = now_us
        if self._deadline_us is None:
            self._deadline_us = now_us
        if now_us < self._deadline_us:
            return False
        interval_us = (interval if self.configured_interval_enabled else 1.0 / 240.0) * 1_000_000
        missed = int((now_us - self._deadline_us) / interval_us)
        self._deadline_us += int(interval_us * (missed + 1))
        self.skipped_intervals += missed
        self.admitted_jobs += 1
        observe_physics_scheduler(self.configured_interval_enabled)
        return True

    def report(self):
        return {'source': 'local_realtime_physics_job_scheduler',
                'configured_interval_enabled': self.configured_interval_enabled,
                'configured_interval_seconds': self.configured_interval_seconds,
                'effective_interval_seconds': (self.configured_interval_seconds
                                               if self.configured_interval_enabled else 1.0 / 240.0),
                'admitted_jobs': self.admitted_jobs,
                'skipped_intervals': self.skipped_intervals}
