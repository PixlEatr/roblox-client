import subprocess
import unittest
from unittest.mock import Mock, patch

from session_awake import prevent_idle_sleep


class SessionAwakeTests(unittest.TestCase):
    def test_assertion_is_scoped_to_session_and_released_after_error(self):
        process = Mock()
        process.poll.return_value = None
        report = {}
        with patch('session_awake.sys.platform', 'darwin'), \
                patch('session_awake.os.getpid', return_value=1234), \
                patch('session_awake.subprocess.Popen', return_value=process) as launch:
            with self.assertRaisesRegex(RuntimeError, 'session failure'):
                with prevent_idle_sleep(report):
                    launch.assert_called_once()
                    self.assertEqual(launch.call_args.args[0],
                                     ['/usr/bin/caffeinate', '-i', '-s', '-w', '1234'])
                    self.assertTrue(report['host_awake_policy']['helper_started'])
                    process.terminate.assert_not_called()
                    raise RuntimeError('session failure')
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)
        self.assertFalse(report['host_awake_policy']['prevents_lid_or_thermal_sleep'])

    def test_missing_helper_does_not_block_session(self):
        report = {}
        with patch('session_awake.sys.platform', 'darwin'), \
                patch('session_awake.subprocess.Popen', side_effect=FileNotFoundError):
            with prevent_idle_sleep(report):
                self.assertFalse(report['host_awake_policy']['helper_started'])
        self.assertEqual(report['host_awake_policy']['helper_error_type'], 'FileNotFoundError')

    def test_other_platform_does_not_launch_macos_helper(self):
        with patch('session_awake.sys.platform', 'win32'), \
                patch('session_awake.subprocess.Popen') as launch:
            with prevent_idle_sleep({}):
                launch.assert_not_called()

    def test_stuck_helper_is_reaped(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('caffeinate', 2), 0]
        with patch('session_awake.sys.platform', 'darwin'), \
                patch('session_awake.subprocess.Popen', return_value=process):
            with prevent_idle_sleep({}):
                pass
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
