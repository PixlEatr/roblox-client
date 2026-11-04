import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runtime_preflight


class RuntimePreflightTests(unittest.TestCase):
    def test_cloud_only_dependency_is_rejected_without_reading_contents(self):
        with patch.object(runtime_preflight.Path, 'stat',
                          return_value=SimpleNamespace(st_flags=runtime_preflight.DATALESS)):
            result = runtime_preflight._probe()
        self.assertEqual(result, {'ok': False, 'reason': 'dependency_cloud_only',
                                  'dependency': 'bytecode_inspector'})

    def test_stalled_child_is_bounded(self):
        with patch.object(runtime_preflight.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired('probe', 2)) as run:
            result = runtime_preflight.check_runtime_dependencies(timeout=2)
        self.assertEqual(result, {'ok': False, 'reason': 'dependency_probe_timeout'})
        self.assertEqual(run.call_args.kwargs['timeout'], 2)

    def test_crashed_or_malformed_child_cannot_report_ready(self):
        for code, output in [(1, '{"ok":true}'), (0, 'broken'), (0, '[]'),
                             (0, '{"ok":"yes"}')]:
            with self.subTest(code=code, output=output), patch.object(
                    runtime_preflight.subprocess, 'run',
                    return_value=SimpleNamespace(returncode=code, stdout=output)):
                self.assertFalse(runtime_preflight.check_runtime_dependencies()['ok'])

    def test_launcher_stops_before_invite_or_credentials_on_failure(self):
        import validate_typed_chat_live
        with patch('sys.argv', ['validate_typed_chat_live.py']), patch.object(
                validate_typed_chat_live, 'check_runtime_dependencies',
                return_value={'ok': False, 'reason': 'dependency_probe_timeout'}), patch.object(
                validate_typed_chat_live.Path, 'read_text') as read:
            with self.assertRaises(validate_typed_chat_live.SessionError):
                validate_typed_chat_live.main()
        read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
