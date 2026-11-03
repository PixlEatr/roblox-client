"""Session-scoped observations from the native ping integrity producers.

Native 1013f6eb4 / 1013f741c set global flag 0x40 when the RSB container
checksum comparison fails. The observation below comes from that actual
comparison in bytecode_container, before its existing rejection is raised.
World-script entry points report actual OS-thread ownership checks, and the
physics job reports its native configuration comparisons. Sticky flags belong
only to this session; no observations come from disk or another connection.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock


_current_observer = ContextVar('native_session_container_observer', default=None)
_candidate_probe = ContextVar('native_session_container_candidate_probe', default=False)


@contextmanager
def container_candidate_probe():
    """A format probe cannot establish that a native container was rejected."""
    token = _candidate_probe.set(True)
    try:
        yield
    finally:
        _candidate_probe.reset(token)


def bind_container_observer(function, *args, **kwargs):
    """Carry only this observer binding into one submitted worker job."""
    observer, probe = _current_observer.get(), _candidate_probe.get()

    def run():
        observer_token = _current_observer.set(observer)
        probe_token = _candidate_probe.set(probe)
        try:
            return function(*args, **kwargs)
        finally:
            _candidate_probe.reset(probe_token)
            _current_observer.reset(observer_token)
    return run


def observe_container_checksum(matches):
    """Record only an executed checksum comparison in the active session."""
    if type(matches) is not bool:
        raise TypeError('checksum comparison must be a boolean')
    observer = _current_observer.get()
    if observer is not None:
        observer._container_checksum(matches, candidate=_candidate_probe.get())


def observe_runtime_thread_report(source, report):
    observer = _current_observer.get()
    if observer is not None:
        observer.observe_runtime_thread_report(source, report)


def observe_runtime_protected_report(source, report):
    observer = _current_observer.get()
    if observer is not None:
        observer.observe_runtime_protected_report(source, report)


def observe_runtime_oneshot_report(result):
    """Called once by the process adapter, never by script or packet input."""
    observer = _current_observer.get()
    if observer is not None:
        observer.observe_runtime_oneshot_report(result)


def observe_physics_smoothing(matches):
    if type(matches) is not bool:
        raise TypeError('Physics smoothing comparison must be a boolean')
    observer = _current_observer.get()
    if observer is not None:
        observer._physics_comparison('smoothing', matches)


def observe_physics_gravity(matches):
    if type(matches) is not bool:
        raise TypeError('Physics gravity comparison must be a boolean')
    observer = _current_observer.get()
    if observer is not None:
        observer._physics_comparison('gravity', matches)


def observe_physics_scheduler(enabled):
    if type(enabled) is not bool:
        raise TypeError('Physics scheduler mode must be a boolean')
    observer = _current_observer.get()
    if observer is not None:
        observer._physics_comparison('scheduler', enabled)


def observe_physics_world_bounds(matches):
    if type(matches) is not bool:
        raise TypeError('Physics world-bounds comparison must be a boolean')
    observer = _current_observer.get()
    if observer is not None:
        observer._physics_comparison('world_bounds', matches)


def _protected_counts(report):
    fields = ('constructions', 'copies', 'failures')
    if (not isinstance(report, dict) or set(report) != {'version', *fields}
            or type(report['version']) is not int or report['version'] != 1
            or any(type(report[k]) is not int or not 0 <= report[k] < 2**64 for k in fields)
            or report['failures'] > report['constructions'] + report['copies']):
        raise ValueError('Invalid protected-script integrity observation')
    return tuple(report[k] for k in fields)


def _thread_counts(report):
    if (not isinstance(report, dict) or set(report) != {'version', 'checks', 'failures'}
            or type(report['version']) is not int or report['version'] != 1
            or any(type(report[k]) is not int or not 0 <= report[k] < 2**64
                   for k in ('checks', 'failures'))
            or report['failures'] > report['checks']):
        raise ValueError('Invalid runtime thread-integrity observation')
    return report['checks'], report['failures']


class SessionIntegrityState:
    def __init__(self):
        self._lock = Lock()
        self._observed_global_flags = 0
        self._container_checks = 0
        self._container_failures = 0
        self._candidate_checks = 0
        self._candidate_failures = 0
        self._runtime_thread_sources = {}
        self._thread_checks = 0
        self._thread_failures = 0
        self._runtime_protected_sources = {}
        self._protected_constructions = 0
        self._protected_copies = 0
        self._protected_failures = 0
        self._oneshot_invocations = 0
        self._oneshot_missing_reports = 0
        self._physics_checks = {'smoothing': 0, 'gravity': 0, 'scheduler': 0, 'world_bounds': 0}
        self._physics_failures = {'smoothing': 0, 'gravity': 0, 'scheduler': 0, 'world_bounds': 0}

    def _physics_comparison(self, kind, matches):
        with self._lock:
            self._physics_checks[kind] += 1
            if not matches:
                self._physics_failures[kind] += 1
                self._observed_global_flags |= 0x200000 if kind == 'scheduler' else 0x80

    def observe_runtime_oneshot_report(self, result):
        # Each process has independent counters. Aggregate once at the adapter
        # boundary, without retaining terminated host objects or using the
        # persistent-worker source limit as a limit on authenticated programs.
        protected = result.get('runtime_protected_integrity') if isinstance(result, dict) else None
        thread = result.get('runtime_thread_integrity') if isinstance(result, dict) else None
        counts = _protected_counts(protected) if protected is not None else None
        threads = _thread_counts(thread) if thread is not None else None
        with self._lock:
            self._oneshot_invocations += 1
            if counts is None:
                self._oneshot_missing_reports += 1
            else:
                self._protected_constructions += counts[0]
                self._protected_copies += counts[1]
                self._protected_failures += counts[2]
                if counts[2]:
                    self._observed_global_flags |= 0x200
            if threads is not None:
                self._thread_checks += threads[0]
                self._thread_failures += threads[1]
                if threads[1]:
                    self._observed_global_flags |= 0x10

    def observe_runtime_protected_report(self, source, report):
        if source is None:
            raise ValueError('Invalid protected-script integrity observation')
        current = _protected_counts(report)
        with self._lock:
            previous = self._runtime_protected_sources.get(source, (0, 0, 0))
            delta = tuple(now-old for now, old in zip(current, previous))
            if any(value < 0 for value in delta) or delta[2] > delta[0] + delta[1]:
                raise ValueError('Inconsistent protected-script integrity counters')
            if source not in self._runtime_protected_sources and len(self._runtime_protected_sources) >= 8:
                raise ValueError('Protected-script integrity source limit exceeded')
            self._runtime_protected_sources[source] = current
            self._protected_constructions += delta[0]
            self._protected_copies += delta[1]
            self._protected_failures += delta[2]
            if current[2]:
                self._observed_global_flags |= 0x200

    def observe_runtime_thread_report(self, source, report):
        # The source is the live host object, never a caller-supplied session ID.
        if source is None:
            raise ValueError('Invalid runtime thread-integrity observation')
        current = _thread_counts(report)
        with self._lock:
            previous = self._runtime_thread_sources.get(source, (0, 0))
            if any(now < old for now, old in zip(current, previous)):
                raise ValueError('Runtime thread-integrity counters decreased')
            if current[1]-previous[1] > current[0]-previous[0]:
                raise ValueError('Runtime thread failures exceed new checks')
            if source not in self._runtime_thread_sources and len(self._runtime_thread_sources) >= 8:
                raise ValueError('Runtime thread-integrity source limit exceeded')
            self._runtime_thread_sources[source] = current
            self._thread_checks += current[0] - previous[0]
            self._thread_failures += current[1] - previous[1]
            if current[1]:
                self._observed_global_flags |= 0x10

    @contextmanager
    def observe_containers(self):
        """Scope synchronous/authored decoding to this connection, with cleanup.

        Context-local binding avoids a process-global 'current session' and
        restores any enclosing observer even when verification raises. It
        transfers no observations implicitly. A submitted preparation job
        explicitly captures this observer through bind_container_observer.
        """
        token = _current_observer.set(self)
        try:
            yield
        finally:
            _current_observer.reset(token)

    def _container_checksum(self, matches, *, candidate=False):
        with self._lock:
            if candidate:
                self._candidate_checks += 1
                self._candidate_failures += int(not matches)
                return
            self._container_checks += 1
            if not matches:
                self._container_failures += 1
                self._observed_global_flags |= 0x40

    def status_word(self):
        # Every native writer ORs into the global word. The DataModel set
        # mirrors the subset that also inserts there, so the global covers both.
        with self._lock:
            return self._observed_global_flags

    def public_report(self):
        with self._lock:
            return {
                'scope': 'this_native_session',
                'wire_status_available': True,
                'complete_native_producer_inventory': True,
                'observed_global_flags': self._observed_global_flags,
                'container_checksum_checks': self._container_checks,
                'container_checksum_failures': self._container_failures,
                'container_candidate_checks': self._candidate_checks,
                'container_candidate_failures': self._candidate_failures,
                'script_thread_checks': self._thread_checks,
                'script_thread_failures': self._thread_failures,
                'script_thread_coverage': 'world_script_entry_points',
                'protected_string_constructions': self._protected_constructions,
                'protected_string_copies': self._protected_copies,
                'protected_string_failures': self._protected_failures,
                'oneshot_worker_invocations': self._oneshot_invocations,
                'oneshot_worker_missing_reports': self._oneshot_missing_reports,
                'physics_comparison_checks': dict(self._physics_checks),
                'physics_comparison_failures': dict(self._physics_failures),
                'physics_coverage': 'world_bounds_solver_gravity_interval_average_and_realtime_scheduler',
                'physics_unavailable_producers': [],
                'observed_producers': (['rsb_container_checksum'] if self._container_checks else [])
                    + (['script_load_thread_ownership'] if self._thread_checks else [])
                    + (['protected_string_integrity'] if self._protected_constructions else [])
                    + (['physics_interval_smoothing'] if self._physics_checks['smoothing'] else [])
                    + (['physics_gravity_consistency'] if self._physics_checks['gravity'] else [])
                    + (['physics_scheduler_mode'] if self._physics_checks['scheduler'] else [])
                    + (['physics_world_bounds'] if self._physics_checks['world_bounds'] else []),
                'unavailable_producers': [],
            }
