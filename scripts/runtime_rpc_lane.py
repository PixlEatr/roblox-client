"""Single-flight background lane for synchronous runtime host RPCs.

The worker receives only a deep-copied command and calls the supplied host
callable. It must not be given a World, socket, chat bridge, or callbacks that
can access those objects. Results and exceptions are consumed by the caller.
"""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor


class RuntimeRpcLane:
    """Run one host.call(command) at a time without blocking the submitter.

    API:
      * ``submit(command) -> bool`` deep-copies then schedules if idle; returns
        False if a request is already outstanding. Raises after close.
      * ``submit_owned(command) -> bool`` schedules the exact object without
        copying. A True return transfers exclusive ownership to the lane: the
        caller must not mutate the command or any nested object until its result
        is consumed. A False return leaves ownership with the caller.
      * ``take() -> result | None`` is nonblocking; raises the worker exception
        when a completed request failed. An actual ``None`` result is therefore
        indistinguishable from no completion; host RPCs are expected to return
        mappings, as PersistentLuauHost does.
      * ``wait() -> result`` blocks for and consumes the outstanding request.
      * ``close()`` rejects future submissions and cancels queued work without
        waiting for an already-running host.call to return.

    A completed result remains outstanding until take()/wait() consumes it, so
    callers cannot accidentally overlap or reorder host operations.
    """

    def __init__(self, host_call):
        if not callable(host_call):
            raise TypeError('host_call must be callable')
        self._host_call = host_call
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='luau-rpc')
        self._lock = threading.Lock()
        self._consume_lock = threading.Lock()
        self._future = None
        self._closed = False

    def submit(self, command):
        # Copy while still on the caller thread; no mutable caller object crosses
        # the worker boundary, even if the caller immediately edits its input.
        captured = copy.deepcopy(command)
        return self.submit_owned(captured)

    def submit_owned(self, command):
        """Schedule an immutable, caller-owned payload without a deep copy.

        On success the caller transfers ownership and must keep every object
        reachable from ``command`` unchanged until take()/wait() consumes the
        matching result. Internal callers may use this after independently
        materializing fresh JSON-only structures; general callers should use
        submit(), which keeps its deep-copy guarantee.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError('Runtime RPC lane is closed')
            if self._future is not None:
                return False
            self._future = self._executor.submit(self._host_call, command)
            return True

    def take(self):
        """Consume a completed result immediately, or return None if pending/idle."""
        if not self._consume_lock.acquire(blocking=False):
            return None
        try:
            with self._lock:
                future = self._future
                if future is None or not future.done():
                    return None
            try:
                return future.result()
            finally:
                with self._lock:
                    if self._future is future:
                        self._future = None
        finally:
            self._consume_lock.release()

    def wait(self):
        """Block for and consume the outstanding result; propagate its error."""
        with self._consume_lock:
            with self._lock:
                if self._closed and self._future is None:
                    raise RuntimeError('Runtime RPC lane is closed')
                future = self._future
                if future is None:
                    raise RuntimeError('No runtime RPC is outstanding')
            try:
                return future.result()
            finally:
                with self._lock:
                    if self._future is future:
                        self._future = None

    def close(self):
        """Stop accepting work, cancel queued work, and return without joining."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            future = self._future
            if future is not None:
                future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @property
    def closed(self):
        with self._lock:
            return self._closed
