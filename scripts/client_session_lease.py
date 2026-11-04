"""Process-held exclusive lease for the shared Roblox client account."""
import os
from contextlib import contextmanager
if os.name == "nt":
    import msvcrt
else:
    import fcntl
from pathlib import Path
from platform_files import open_nofollow, private_descriptor
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
    if os.name != "nt" and not hasattr(os, "O_NOFOLLOW"):
        raise SessionLeaseBusy("This platform cannot safely open the client session lease")
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = open_nofollow(lock_path, flags, 0o600)
    except OSError as error:
        raise SessionLeaseBusy("Cannot safely open the client session lease") from None
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or (os.name != "nt" and metadata.st_uid != os.getuid()) or (os.name == "nt" and not private_descriptor(fd))):
            raise SessionLeaseBusy("Client session lease must be an owned regular file")
        if os.name != "nt": os.fchmod(fd, 0o600)
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
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
        if os.name != 'nt' and not stat.S_ISSOCK(metadata.st_mode):
            continue
        from local_transport import probe
        try: probe(path)
        except (ConnectionRefusedError, FileNotFoundError): continue
        except (OSError, ValueError):
            raise SessionLeaseBusy("An existing client console may still be active") from None
        raise SessionLeaseBusy("An existing client console is already running")
