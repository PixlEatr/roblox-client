"""Process-held exclusive lease for the shared Roblox client account."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import socket
import stat


class SessionLeaseBusy(RuntimeError):
    """Another process may already own the shared client session."""


@contextmanager
def acquire_session_lease(root):
    """Acquire the account lease, then check for legacy console owners.

    The lock file is intentionally persistent: flock ownership belongs to the
    open descriptor, so process exit releases the lock without unlink races.
    """
    root = Path(root)
    lock_path = root / "secrets/client-session.lock"
    if not hasattr(os, "O_NOFOLLOW"):
        raise SessionLeaseBusy("This platform cannot safely open the client session lease")
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise SessionLeaseBusy("Cannot safely open the client session lease") from None
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()):
            raise SessionLeaseBusy("Client session lease must be an owned regular file")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionLeaseBusy("Another game_session process already owns the client session") from None

        _probe_legacy_console_sockets(root)
        yield
    finally:
        os.close(fd)


def _probe_legacy_console_sockets(root):
    """Detect live pre-lease owners without sending requests or changing paths."""
    for name in ("client-script.sock", "client-chat.sock"):
        path = Path(root) / "secrets" / name
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISSOCK(metadata.st_mode):
            # Leave validation and any existing file entirely to its owner.
            continue

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            try:
                probe.connect(str(path))
            except (ConnectionRefusedError, FileNotFoundError):
                # A stale socket cannot own a session. Do not unlink it here.
                continue
            except OSError:
                # An inconclusive probe is safer to treat as occupied.
                raise SessionLeaseBusy("An existing client console may still be active") from None
            raise SessionLeaseBusy("An existing client console is already running")
        finally:
            probe.close()
