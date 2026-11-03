"""Actual one-shot VM observations using locally authored compiler fixtures."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import local_runtime
from session_integrity import SessionIntegrityState


class OneShotIntegrityTests(unittest.TestCase):
    def test_rust_oneshot_nested_loadstring_uses_the_same_store(self):
        state = SessionIntegrityState()
        with state.observe_containers():
            result = local_runtime.run_source('return assert(loadstring("return 42"))()')
        self.assertEqual(result['values'], [42])
        self.assertEqual(result['runtime_protected_integrity'],
                         {'version': 1, 'constructions': 2, 'copies': 2, 'failures': 0})
        self.assertNotIn('runtime_thread_integrity', result)
        self.assertEqual(state.public_report()['protected_string_copies'], 2)

    def compiled(self, source):
        with tempfile.TemporaryDirectory() as folder:
            src, dst = Path(folder) / 'fixture.luau', Path(folder) / 'fixture.luac'
            src.write_text(source)
            subprocess.run([str(local_runtime.HOST.parent / 'luau-source-to-bytecode'),
                            str(src), str(dst)], check=True, capture_output=True, timeout=10)
            return dst.read_bytes()

    def test_fresh_bytecode_workers_aggregate_without_source_limit_or_session_leak(self):
        program = self.compiled('local a,b=...; return a+b')
        first, second = SessionIntegrityState(), SessionIntegrityState()
        with first.observe_containers():
            for i in range(17):
                result = local_runtime.run_bytecode(program, [i, 2])
                self.assertEqual(result['values'], [i + 2])
                self.assertEqual(result['runtime_protected_integrity'],
                                 {'version': 1, 'constructions': 1, 'copies': 1, 'failures': 0})
                self.assertNotIn('runtime_thread_integrity', result)
            with second.observe_containers():
                local_runtime.run_bytecode(program)
            local_runtime.run_bytecode(program, [1, 1])
        local_runtime.run_bytecode(program, [1, 1])
        observed = first.public_report()
        self.assertEqual(observed['oneshot_worker_invocations'], 18)
        self.assertEqual(observed['protected_string_copies'], 18)
        self.assertEqual(observed['oneshot_worker_missing_reports'], 0)
        self.assertEqual(observed['script_thread_checks'], 0)
        self.assertEqual(second.public_report()['protected_string_copies'], 1)
        self.assertEqual(first.status_word(), 0)

    def test_errors_and_vm_timeout_retain_real_load_observations(self):
        state = SessionIntegrityState()
        for source in ('error("local fixture")', 'while true do end'):
            with state.observe_containers():
                result = local_runtime.run_bytecode(self.compiled(source), timeout_ms=30)
            self.assertFalse(result['ok'], result)
            self.assertEqual(result['runtime_protected_integrity']['copies'], 1)
        self.assertEqual(state.public_report()['protected_string_copies'], 2)
        self.assertEqual(state.public_report()['protected_string_failures'], 0)

    def test_header_rejection_and_process_loss_are_missing_observations(self):
        state = SessionIntegrityState()
        with state.observe_containers():
            result = local_runtime.run_bytecode(b'RSB1' + bytes(24))
            self.assertFalse(result['ok'])
            with patch('local_runtime.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
                result = local_runtime.run_bytecode(b'inert')
            self.assertFalse(result['ok'])
        report = state.public_report()
        self.assertEqual(report['oneshot_worker_invocations'], 2)
        self.assertEqual(report['oneshot_worker_missing_reports'], 2)
        self.assertEqual(report['protected_string_constructions'], 0)
        self.assertTrue(report['wire_status_available'])
        self.assertEqual(state.status_word(), 0)

    def test_cpp_source_and_world_paths_report_checks_on_success_and_failure(self):
        # Select the isolated C++ candidate even if a Rust worker is installed.
        with patch('local_runtime.RUST_HOST', local_runtime.HOST.parent / 'absent-rust-worker'):
            result = local_runtime.run_source('return 42')
            self.assertEqual(result['values'], [42])
            self.assertEqual(result['runtime_protected_integrity']['copies'], 1)
            result = local_runtime.run_source('local =')
            self.assertFalse(result['ok'])
            self.assertEqual(result['runtime_protected_integrity']['failures'], 0)
            self.assertEqual(result['runtime_protected_integrity']['copies'], 1)
            root = Path(__file__).resolve().parents[1]
            snapshot = json.loads((root / 'runtime/tests/world_snapshot.json').read_text())
            for source in ('return 42', 'error("fixture")'):
                result = local_runtime.run_source(source, world_snapshot=snapshot)
                self.assertEqual(result['runtime_protected_integrity']['copies'], 1)
                self.assertEqual(result['runtime_thread_integrity'],
                                 {'version': 1, 'checks': 1, 'failures': 0})


if __name__ == '__main__':
    unittest.main()
