"""Exercise thread telemetry emitted by the compiled persistent Luau worker."""
import json
from pathlib import Path
import unittest

from persistent_runtime import PersistentLuauHost


class RuntimeThreadIntegrityTests(unittest.TestCase):
    def test_real_vm_loads_use_protected_copies_and_do_not_expose_script_contents(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root / 'runtime/tests/world_snapshot.json').read_text())
        with PersistentLuauHost(snapshot) as host:
            initial = host.initial_result['runtime_protected_integrity']
            self.assertGreater(initial['constructions'], 0)
            self.assertEqual(initial['constructions'], initial['copies'])
            result = host.call({'op': 'execute', 'source':
                'local secret_fixture = "private-test-string"; return assert(loadstring("return 42"))()',
                'timeout_ms': 1000})
            self.assertEqual(result['values'], [42])
            observed = result['runtime_protected_integrity']
            self.assertEqual(observed, {'version': 1, 'constructions': initial['constructions'] + 2,
                                       'copies': initial['copies'] + 2, 'failures': 0})
            self.assertNotIn('private-test-string', str(observed))
            result = host.call({'op': 'execute', 'source': 'local =', 'timeout_ms': 1000})
            self.assertFalse(result['ok'])
            self.assertEqual(result['runtime_protected_integrity']['failures'], 0)
            self.assertEqual(result['runtime_protected_integrity']['copies'], observed['copies'] + 1)
            before_error = result['runtime_protected_integrity']
            result = host.call({'op': 'unsupported-inert-operation', 'timeout_ms': 1000})
            self.assertFalse(result['ok'])
            self.assertEqual(result['runtime_protected_integrity'], before_error)
            with PersistentLuauHost(snapshot) as other:
                self.assertEqual(other.initial_result['runtime_protected_integrity'], initial)

    def test_loadstring_nested_loading_and_compile_errors_keep_thread_observations(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root / 'runtime/tests/world_snapshot.json').read_text())
        with PersistentLuauHost(snapshot) as host:
            count = host.initial_result['runtime_thread_integrity']['checks']
            result = host.call({'op': 'execute', 'source':
                'local f = assert(loadstring([[return assert(loadstring("return 42"))()]])); return f()',
                'timeout_ms': 1000})
            self.assertEqual(result['values'], [42])
            count += 3  # outer command and two real nested loadstring calls
            self.assertEqual(result['runtime_thread_integrity'], {'version': 1, 'checks': count, 'failures': 0})
            result = host.call({'op': 'execute', 'source':
                'local f, message = loadstring("local ="); return f == nil, type(message)',
                'timeout_ms': 1000})
            self.assertEqual(result['values'], [True, 'string'])
            count += 2
            self.assertEqual(result['runtime_thread_integrity']['checks'], count)
            result = host.call({'op': 'execute', 'source': 'local =', 'timeout_ms': 1000})
            self.assertFalse(result['ok'])
            self.assertEqual(result['runtime_thread_integrity'], {'version': 1, 'checks': count + 1, 'failures': 0})

    def test_real_script_loads_report_owned_counters_and_new_workers_start_fresh(self):
        root = Path(__file__).resolve().parents[1]
        snapshot = json.loads((root / 'runtime/tests/world_snapshot.json').read_text())
        with PersistentLuauHost(snapshot) as first:
            initial = first.initial_result['runtime_thread_integrity']
            self.assertGreaterEqual(initial['checks'], 1)
            self.assertEqual(initial['failures'], 0)
            result = first.call({'op': 'execute', 'source': 'return 6 * 7', 'timeout_ms': 1000})
            self.assertEqual(result['values'], [42])
            self.assertEqual(result['runtime_thread_integrity'],
                             {'version': 1, 'checks': initial['checks'] + 1, 'failures': 0})
            # A user global cannot overwrite the native worker's report.
            result = first.call({'op': 'execute', 'source':
                'getgenv().runtime_thread_integrity = {checks=999,failures=999}; return true',
                'timeout_ms': 1000})
            self.assertEqual(result['runtime_thread_integrity']['checks'], initial['checks'] + 2)
            with PersistentLuauHost(snapshot) as second:
                self.assertEqual(second.initial_result['runtime_thread_integrity'], initial)


if __name__ == '__main__':
    unittest.main()
