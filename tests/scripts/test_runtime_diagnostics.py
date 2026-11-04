from pathlib import Path
import os
import tempfile
import threading
import time
import unittest

from runtime_diagnostics import CoalescedPrivateSnapshotWriter


class RuntimeDiagnosticsWriterTests(unittest.TestCase):
    def test_pending_batches_coalesce_and_close_flushes_latest(self):
        entered = threading.Event()
        release = threading.Event()
        written = []

        def writer(path, value):
            if value["n"] == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            written.append(value["n"])

        queue = CoalescedPrivateSnapshotWriter(writer)
        path = Path("status.json")
        self.assertTrue(queue.submit([(path, {"n": 1})]))
        self.assertTrue(entered.wait(1))
        self.assertTrue(queue.submit([(path, {"n": 2})]))
        self.assertTrue(queue.submit([(path, {"n": 3})]))
        closer_result = []
        closer = threading.Thread(target=lambda: closer_result.append(queue.close(flush=True, timeout=2)))
        closer.start()
        self.assertFalse(queue.submit([(path, {"n": 4})]))
        release.set()
        closer.join(2)
        self.assertFalse(closer.is_alive())
        self.assertEqual(closer_result, [True])
        self.assertEqual(written, [1, 3])

    def test_submit_does_not_wait_for_active_disk_writer(self):
        entered = threading.Event()
        release = threading.Event()
        written = []

        def writer(path, value):
            entered.set()
            release.wait(2)
            written.append(value)

        queue = CoalescedPrivateSnapshotWriter(writer)
        path = Path("status.json")
        self.assertTrue(queue.submit([(path, "active")]))
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        self.assertTrue(queue.submit([(path, "pending")]))
        self.assertLess(time.monotonic() - started, 0.25)
        release.set()
        self.assertTrue(queue.close(flush=True, timeout=2))
        self.assertEqual(written, ["active", "pending"])

    def test_worker_error_is_terminal_and_only_type_is_public(self):
        called = threading.Event()

        def writer(_path, _value):
            called.set()
            raise OSError("secret-token-must-not-escape")

        queue = CoalescedPrivateSnapshotWriter(writer)
        self.assertTrue(queue.submit([(Path("private.json"), {"ok": True})]))
        self.assertTrue(called.wait(1))
        self.assertTrue(queue._finished.wait(1))
        self.assertTrue(queue.failed)
        self.assertEqual(queue.error_type, "OSError")
        self.assertNotIn("secret-token", str(queue.error_type))
        self.assertFalse(queue.submit([(Path("private.json"), {})]))
        self.assertTrue(queue.close(timeout=1))

    def test_private_writer_uses_owner_only_mode_and_refuses_symlinks(self):
        # Exercise the same writer the background worker imports lazily.
        from runtime_diagnostics import _load_private_writer
        write_private = _load_private_writer()
        with tempfile.TemporaryDirectory(prefix="runtime-diagnostics-") as directory:
            root = Path(directory)
            target = root / "target.json"
            destination = root / "private.json"
            write_private(destination, {"state": "safe"})
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)
            target.write_text("untouched")
            symlink = root / "link.json"
            symlink.symlink_to(target)
            with self.assertRaises(OSError):
                write_private(symlink, {"state": "overwrite"})
            self.assertEqual(target.read_text(), "untouched")

    def test_rejects_oversized_batches_and_async_fails_invalid_nested_values(self):
        written = []
        queue = CoalescedPrivateSnapshotWriter(lambda *args: written.append(args))
        with self.assertRaises(ValueError):
            queue.submit([(Path("a"), 1), (Path("b"), 2), (Path("c"), 3)])
        self.assertTrue(queue.submit([
            (Path("a"), {"valid": True}),
            (Path("b"), {"nested": [object()]}),
        ]))
        self.assertTrue(queue._finished.wait(1))
        self.assertEqual(queue.error_type, "TypeError")
        self.assertEqual(written, [])
        self.assertFalse(queue.submit([(Path("a"), 3)]))
        self.assertTrue(queue.close(timeout=1))


if __name__ == "__main__":
    unittest.main()
