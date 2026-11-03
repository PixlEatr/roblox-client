"""One-shot coordinator for a normal Luau walk task during a physics trial.

This observes local script completion only. It never treats send attempts,
Luau completion, or predicted poses as server-confirmed movement.
"""
import math
import re
import time

from local_runtime import MAX_SOURCE_BYTES


class PhysicsWalkExercise:
    MAX_DURATION_SECONDS = 65.0
    REQUIRED_WALK_SECONDS = 60.0
    EXPECTED_RETURN = 'walk_completed'

    def __init__(self, source):
        if not isinstance(source, str) or not source.strip():
            raise ValueError('Walk source must be nonempty Luau text')
        if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
            raise ValueError('Walk source exceeds the runtime source limit')
        self._source = source
        self._state = 'waiting_for_physics_send'
        self._reason = None
        self._submission_attempted = False
        self._submitted_at = None
        self._submission_delay = 0.
        self._job_id = None
        self._luau_state = None
        self._luau_completed = False
        self._close_called = False
        self._error_type = None
        self._close_error_type = None

    def tick(self, now, trial_report, execute_source, results, connected, close):
        """Advance once from the caller/network thread and return safe status.

        ``execute_source`` is called at most once after a real physics send
        attempt is observed. It must be the application's normal Luau submit
        path. ``results`` is a caller-owned mapping of completed job IDs; no
        background or network work is performed here.
        """
        if self._terminal:
            return self.report(now)
        if type(now) not in (int, float) or not math.isfinite(now):
            return self._finish('failed', 'invalid_clock', close, now)
        if not connected:
            return self._finish('stopped', 'disconnected', close, now)

        trial_report = trial_report if isinstance(trial_report, dict) else {}
        driver_stop = trial_report.get('stop_reason')
        if driver_stop is not None:
            return self._finish('stopped', 'physics_driver_stopped', close, now)

        if not self._submission_attempted:
            attempts = trial_report.get('network_send_attempt_count', 0)
            if type(attempts) is not int or attempts <= 0:
                return self.report(now)
            self._submission_attempted = True
            self._submitted_at = float(now)
            self._state = 'submitting'
            try:
                submission_started = time.monotonic()
                submission = execute_source(
                    self._source, timeout_ms=1000, label='<physics-walk>')
                # World synchronization happens before the source starts.
                # Keep the task's 65-second timeout separate from that wait.
                self._submission_delay = round(max(0.,time.monotonic()-submission_started),3)
            except Exception as error:
                return self._finish('failed', 'luau_submission_error', close, now,
                                    error_type=type(error).__name__)
            if not isinstance(submission, dict):
                return self._finish('failed', 'invalid_luau_submission_result', close, now)
            job_id = submission.get('job_id')
            if type(job_id) is not int or job_id < 1:
                reason = 'luau_submission_failed' if submission.get('ok') is False else 'missing_luau_job_id'
                return self._finish('failed', reason, close, now,
                                    error_type=submission.get('error_type'))
            self._job_id = job_id
            self._luau_state = submission.get('state')
            if submission.get('completed') is True or self._luau_state == 'completed':
                return self._accept_completion(submission, close, now)
            if submission.get('ok') is False or self._luau_state not in (None, 'running'):
                return self._finish('failed', 'luau_job_failed', close, now,
                                    error_type=submission.get('error_type'))
            self._state = 'running'
            return self.report(now)

        elapsed = float(now) - self._submitted_at - self._submission_delay
        if elapsed >= self.MAX_DURATION_SECONDS:
            return self._finish('timed_out', 'luau_job_timeout', close, now)
        if not isinstance(results, dict):
            return self._finish('failed', 'invalid_results_mapping', close, now)
        completed = results.get(self._job_id)
        if completed is None:
            return self.report(now)
        if not isinstance(completed, dict):
            return self._finish('failed', 'invalid_luau_job_result', close, now)
        self._luau_state = completed.get('state')
        if completed.get('completed') is not True and self._luau_state != 'completed':
            if completed.get('ok') is False or self._luau_state in ('failed', 'cancelled'):
                return self._finish('failed', 'luau_job_failed', close, now,
                                    error_type=completed.get('error_type'))
            return self.report(now)
        return self._accept_completion(completed, close, now)

    @property
    def _terminal(self):
        return self._state in ('completed', 'failed', 'stopped', 'timed_out')

    def _accept_completion(self, result, close, now):
        if result.get('ok') is not True:
            return self._finish('failed', 'luau_job_failed', close, now,
                                error_type=result.get('error_type'))
        values = result.get('values')
        if values != [self.EXPECTED_RETURN]:
            return self._finish('failed', 'unexpected_luau_result', close, now)
        elapsed = float(now) - self._submitted_at
        if elapsed < self.REQUIRED_WALK_SECONDS:
            return self._finish('failed', 'luau_completed_too_early', close, now)
        self._luau_completed = True
        return self._finish('completed', 'luau_walk_completed', close, now)

    def _finish(self, state, reason, close, now, *, error_type=None):
        self._state = state
        self._reason = reason
        if error_type and isinstance(error_type, str):
            self._error_type = (error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', error_type)
                                else 'Exception')
        if not self._close_called:
            self._close_called = True
            try:
                if callable(close):
                    close()
                else:
                    self._close_error_type = 'InvalidCloseCallback'
            except Exception as error:
                self._close_error_type = type(error).__name__[:80]
        return self.report(now)

    def report(self, now=None):
        elapsed = None
        if self._submitted_at is not None and type(now) in (int, float) and math.isfinite(now):
            elapsed = max(0.0, round(float(now) - self._submitted_at, 3))
        return {
            'state': self._state,
            'reason': self._reason,
            'submission_attempted': self._submission_attempted,
            'job_id': self._job_id,
            'luau_state': self._luau_state,
            'luau_completed': self._luau_completed,
            'error_type': self._error_type,
            'elapsed_since_submission_seconds': elapsed,
            'submission_preparation_seconds': self._submission_delay,
            'close_called': self._close_called,
            'close_error_type': self._close_error_type,
            'server_movement_confirmed': False,
            'walk_seconds_verified': 0,
        }
