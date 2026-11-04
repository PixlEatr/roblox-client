import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from client_integrity import ClientIntegrityMonitor


class ClientIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / 'client.bin'
        self.original = b'pinned build\0binary\xff'
        self.file.write_bytes(self.original)
        self.baseline = {'client': {'path': str(self.file),
                                  'sha256': hashlib.sha256(self.original).hexdigest()}}

    def test_unchecked_is_distinct_from_measured_baseline_match(self):
        monitor = ClientIntegrityMonitor(self.baseline)
        self.assertEqual(monitor.report()['state'], 'unchecked')
        report = monitor.check()
        self.assertEqual(report['state'], 'matches_pinned_baseline')
        self.assertFalse(report['roblox_native_integrity_implemented'])
        self.assertNotIn(str(self.file), str(report))

    def test_actual_content_edit_is_retained_even_after_restoration(self):
        monitor = ClientIntegrityMonitor(self.baseline)
        self.file.write_bytes(b'changed build')
        self.assertEqual(monitor.check()['current_findings'],
                         [{'artifact': 'client', 'kind': 'content_mismatch'}])
        self.file.write_bytes(self.original)
        result = monitor.check()
        self.assertEqual(result['current_findings'], [])
        self.assertEqual(result['state'], 'baseline_difference_observed')
        self.assertEqual(len(result['session_findings']), 1)

    def test_missing_symlink_and_nonregular_files_are_not_reported_matching(self):
        self.file.unlink()
        self.assertEqual(ClientIntegrityMonitor(self.baseline).check()['current_findings'][0]['kind'], 'missing')
        target = self.file.with_name('target'); target.write_bytes(self.original)
        self.file.symlink_to(target)
        self.assertEqual(ClientIntegrityMonitor(self.baseline).check()['current_findings'][0]['kind'], 'symlink')
        self.file.unlink(); self.file.mkdir()
        self.assertEqual(ClientIntegrityMonitor(self.baseline).check()['current_findings'][0]['kind'], 'not_regular_file')

    def test_read_failure_and_budget_exhaustion_remain_visible(self):
        monitor = ClientIntegrityMonitor(self.baseline)
        with patch('client_integrity.os.read', side_effect=PermissionError):
            self.assertEqual(monitor.check()['current_findings'][0]['kind'], 'unreadable_or_replaced')
        self.assertEqual(ClientIntegrityMonitor(self.baseline, max_bytes=1).check()['current_findings'][0]['kind'], 'size_limit')

    def test_same_size_change_during_read_is_detected(self):
        import os
        original_read = os.read
        mutated = [False]
        def read(fd, count):
            chunk = original_read(fd, count)
            if chunk and not mutated[0]:
                mutated[0] = True
                self.file.write_bytes(b'X' * len(self.original))
            return chunk
        with patch('client_integrity.os.read', side_effect=read):
            finding = ClientIntegrityMonitor(self.baseline).check()['current_findings'][0]
        self.assertEqual(finding['kind'], 'changed_during_check')

    def test_baseline_is_explicit_and_copied_not_enrolled_from_live_files(self):
        for value in ({}, {'client': {'path': 'relative', 'sha256': '0'*64}},
                      {'client': {'path': str(self.file), 'sha256': 'invalid'}}):
            with self.assertRaises(ValueError): ClientIntegrityMonitor(value)
        monitor = ClientIntegrityMonitor(self.baseline)
        self.file.write_bytes(b'edited before first check')
        self.baseline['client']['sha256'] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.assertEqual(monitor.check()['state'], 'baseline_difference_observed')


if __name__ == '__main__': unittest.main()
