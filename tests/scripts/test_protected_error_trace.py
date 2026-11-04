"""Optional caught-error diagnostics preserve execution and stay bounded/private."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from persistent_runtime import PersistentLuauHost
from test_world_snapshot import fixture
from world_snapshot import make_world_snapshot


@unittest.skipIf(os.name == 'nt', 'private diagnostic writer is POSIX-only')
class ProtectedErrorTraceTests(unittest.TestCase):
    def run_source(self, directory, source, match='attempt to index nil'):
        world, _ = fixture()
        with patch.dict(os.environ, {'RCR_ERROR_TRACE_DIRECTORY': str(directory),
                                     'RCR_ERROR_TRACE_MATCH': match}):
            host = PersistentLuauHost(make_world_snapshot(world, 123))
            try:
                result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['completed'], result)
                return result
            finally:
                host.close()

    def test_caught_error_preserves_pcall_result_and_records_pre_unwind_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_source(folder, '''
local function observed_failure()
    local missing
    return missing[7]
end
local ok,message=pcall(observed_failure)
return ok,message,6*7
''')
            self.assertFalse(result['values'][0])
            self.assertIn('attempt to index nil', result['values'][1])
            self.assertEqual(result['values'][2], 42)
            files = list(Path(folder).glob('*.json'))
            self.assertTrue(files)
            records = [json.loads(p.read_text()) for p in files]
            self.assertTrue(any('observed_failure' == frame.get('name')
                                for record in records for frame in record['frames']))
            for path in files:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertLessEqual(path.stat().st_size, 1024*1024)

    def test_matching_error_storm_is_bounded_and_unmatched_errors_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_source(folder, '''
for i=1,40 do local ok=pcall(function() local x;return x[i] end);assert(not ok) end
return "finished"
''')
            self.assertEqual(result['values'], ['finished'])
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 8)
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_source(folder, 'return pcall(function()error("other failure")end)')
            self.assertFalse(result['values'][0])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_unprotected_directory_or_missing_filter_leaves_execution_unchanged(self):
        source = 'local ok=pcall(function()local x;return x[1]end);return ok'
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o755)
            self.assertEqual(self.run_source(folder, source)['values'], [False])
            self.assertEqual(list(Path(folder).iterdir()), [])
            os.chmod(folder, 0o700)
            self.assertEqual(self.run_source(folder, source, match='')['values'], [False])
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
