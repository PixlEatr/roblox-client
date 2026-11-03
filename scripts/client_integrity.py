"""Local file-integrity observations for this client, not Roblox attestation.

The baseline is supplied by a trusted build/release step. Never create it from
the files being examined at session startup: that would accept existing edits.
Findings do not assign Roblox flag bits, request bans, or open a connection.
"""
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import threading


@dataclass(frozen=True)
class Finding:
    artifact: str
    kind: str


class ClientIntegrityMonitor:
    def __init__(self, baseline, *, max_bytes=128 * 1024 * 1024):
        if not isinstance(baseline, dict) or not 1 <= len(baseline) <= 1024:
            raise ValueError('a nonempty pinned baseline of at most 1024 files is required')
        if type(max_bytes) is not int or not 1 <= max_bytes <= 512 * 1024 * 1024:
            raise ValueError('max_bytes must be 1..512 MiB')
        self._baseline = {}
        for label, entry in baseline.items():
            if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', label):
                raise ValueError('artifact labels must be short opaque names')
            if not isinstance(entry, dict) or set(entry) != {'path', 'sha256'}:
                raise ValueError('each artifact needs an absolute path and pinned SHA256')
            path, expected = Path(entry['path']), entry['sha256']
            if not path.is_absolute():
                raise ValueError('artifact paths must be absolute')
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected):
                raise ValueError('invalid pinned SHA256')
            self._baseline[label] = (path, expected.lower())
        self._max_bytes = max_bytes
        self._findings = set()
        self._last_findings = ()
        self._checks = 0
        self._lock = threading.Lock()

    @staticmethod
    def _identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @classmethod
    def _check_file(cls, path, expected, remaining):
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode):
            return 'symlink', 0
        if not stat.S_ISREG(before.st_mode):
            return 'not_regular_file', 0
        if before.st_size > remaining:
            return 'size_limit', 0
        # Reject a symlink substituted between lstat and open. A nonblocking
        # open also prevents a swapped FIFO/device from hanging the monitor.
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(path, flags)
        used = 0
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode)
                    or cls._identity(before) != cls._identity(opened)):
                return 'changed_during_check', 0
            digest = hashlib.sha256()
            while True:
                chunk = os.read(fd, min(1024 * 1024, remaining - used + 1))
                if not chunk:
                    break
                used += len(chunk)
                if used > remaining:
                    return 'size_limit', used
                digest.update(chunk)
            after_fd, after_path = os.fstat(fd), path.lstat()
            if (cls._identity(opened) != cls._identity(after_fd)
                    or cls._identity(opened) != cls._identity(after_path)):
                return 'changed_during_check', used
            return (None if hmac.compare_digest(digest.hexdigest(), expected)
                    else 'content_mismatch'), used
        finally:
            os.close(fd)

    def check(self):
        """Take bounded observations; retain findings for this monitor's lifetime.

        A mismatch demonstrates a baseline difference, not a person's intent.
        """
        with self._lock:
            remaining = self._max_bytes
            findings = []
            for label, (path, expected) in self._baseline.items():
                if remaining <= 0:
                    kind, used = 'size_limit', 0
                else:
                    try:
                        kind, used = self._check_file(path, expected, remaining)
                    except FileNotFoundError:
                        kind, used = 'missing', 0
                    except OSError:
                        kind, used = 'unreadable_or_replaced', 0
                remaining -= used
                if kind:
                    findings.append(Finding(label, kind))
            self._findings.update(findings)
            self._last_findings = tuple(findings)
            self._checks += 1
            return self._report()

    def _report(self):
        return {'scope': 'local_file_integrity',
                'state': ('unchecked' if not self._checks else
                          'baseline_difference_observed' if self._findings else 'matches_pinned_baseline'),
                'checks': self._checks, 'files': len(self._baseline),
                'current_findings': [{'artifact': f.artifact, 'kind': f.kind} for f in self._last_findings],
                'session_findings': [{'artifact': f.artifact, 'kind': f.kind}
                                     for f in sorted(self._findings, key=lambda f: (f.artifact, f.kind))],
                'roblox_native_integrity_implemented': False}

    def report(self):
        with self._lock:
            return self._report()
