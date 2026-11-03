import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from client_session_lease import SessionLeaseBusy, acquire_session_lease


class ClientSessionLeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'secrets').mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    def test_subprocess_contention_and_release_keeps_lockfile(self):
        scripts_dir = Path(__file__).resolve().parent
        code = '''import sys, time
from client_session_lease import acquire_session_lease
with acquire_session_lease(sys.argv[1]):
    print("READY", flush=True)
    time.sleep(60)
'''
        proc = subprocess.Popen(
            [sys.executable, '-c', code, str(self.root)],
            cwd=scripts_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(proc.stdout.readline().strip(), 'READY')
            with self.assertRaises(SessionLeaseBusy):
                with acquire_session_lease(self.root):
                    self.fail('contending process acquired the lease')
            proc.kill()
            proc.wait(timeout=5)
            proc.stdin.close()
            proc.stdout.close()
            stderr = proc.stderr.read()
            proc.stderr.close()
            self.assertEqual(stderr, '')
            with acquire_session_lease(self.root):
                pass
            lock_path = self.root / 'secrets/client-session.lock'
            self.assertTrue(lock_path.is_file())
            self.assertEqual(lock_path.stat().st_uid, os.getuid())
            self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_legacy_live_console_is_probed_without_sending_data(self):
        path = self.root / 'secrets/client-chat.sock'
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(1)
        try:
            with self.assertRaises(SessionLeaseBusy):
                with acquire_session_lease(self.root):
                    self.fail('active legacy console did not block the lease')
            conn, _ = listener.accept()
            conn.settimeout(0.2)
            self.assertEqual(conn.recv(1), b'')
            conn.close()
            self.assertTrue(path.exists(), 'probe must not remove another process socket')
        finally:
            listener.close()

    def test_main_does_not_refresh_allocation_when_lease_is_busy(self):
        import game_session

        def busy(_root):
            raise SessionLeaseBusy('busy for test')

        with mock.patch.object(game_session, 'acquire_session_lease', side_effect=busy), \
             mock.patch.object(game_session, 'refresh_allocation') as refresh, \
             mock.patch.object(sys, 'argv', ['game_session.py', '--refresh']):
            with self.assertRaises(SessionLeaseBusy):
                game_session.main()
        refresh.assert_not_called()

    def test_isolated_run_uses_the_shared_account_lock_before_allocating(self):
        import game_session
        isolated = self.root / 'isolated'
        (isolated / 'secrets').mkdir(parents=True)
        with acquire_session_lease(self.root), \
             mock.patch.object(game_session, 'ROOT', isolated), \
             mock.patch.object(game_session, 'refresh_allocation') as refresh, \
             mock.patch.object(sys, 'argv', ['game_session.py', '--refresh']):
            with self.assertRaises(SessionLeaseBusy):
                game_session.main(account_root=self.root)
        refresh.assert_not_called()
        self.assertFalse((isolated / 'secrets/client-session.lock').exists())


if __name__ == '__main__':
    unittest.main()
