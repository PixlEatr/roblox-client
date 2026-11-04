"""Execute real container comparisons; all input programs are inert fixtures."""
from concurrent.futures import ThreadPoolExecutor
import struct
from threading import Barrier
import unittest
from unittest.mock import patch

from bytecode_check import BytecodeAuthError, BytecodeAuthHandler
from bytecode_container import MAX_BYTES, unpack_container
from session_integrity import SessionIntegrityState
from test_bytecode_container import container_fixture


def corrupt_container():
    value = bytearray(container_fixture(b'inert checksum fixture', compressed=False))
    value[-1] ^= 1
    return bytes(value)


class SessionIntegrityTests(unittest.TestCase):
    def test_oneshot_reports_validate_atomically_and_keep_failures_sticky(self):
        state = SessionIntegrityState()
        protected = {'version': 1, 'constructions': 1, 'copies': 1, 'failures': 1}
        state.observe_runtime_oneshot_report({'runtime_protected_integrity': protected})
        before = state.public_report()
        for invalid in ({'runtime_protected_integrity': {**protected, 'copies': True}},
                        {'runtime_protected_integrity': protected,
                         'runtime_thread_integrity': {'version': 1, 'checks': 0, 'failures': 1}}):
            with self.assertRaises(ValueError):
                state.observe_runtime_oneshot_report(invalid)
            self.assertEqual(state.public_report(), before)
        state.observe_runtime_oneshot_report({'runtime_protected_integrity': {**protected, 'failures': 0},
                                             'runtime_thread_integrity': {'version': 1, 'checks': 1, 'failures': 1}})
        state.observe_runtime_oneshot_report(None)
        report = state.public_report()
        self.assertEqual(report['observed_global_flags'], 0x210)
        self.assertEqual(report['oneshot_worker_invocations'], 3)
        self.assertEqual(report['oneshot_worker_missing_reports'], 1)
        self.assertEqual(report['protected_string_copies'], 2)
        self.assertEqual(report['script_thread_failures'], 1)

    def test_protected_observations_preserve_sticky_flags_without_cross_session_state(self):
        state, source = SessionIntegrityState(), object()
        report = {'version': 1, 'constructions': 2, 'copies': 2, 'failures': 1}
        state.observe_runtime_protected_report(source, report)
        state.observe_runtime_protected_report(source, report)
        state.observe_runtime_protected_report(source, {**report, 'copies': 3})
        observed = state.public_report()
        self.assertEqual(observed['protected_string_constructions'], 2)
        self.assertEqual(observed['protected_string_copies'], 3)
        self.assertEqual(observed['protected_string_failures'], 1)
        self.assertEqual(observed['observed_global_flags'], 0x200)
        self.assertEqual(SessionIntegrityState().public_report()['protected_string_failures'], 0)
        self.assertEqual(state.status_word(), 0x200)

    def test_protected_observations_reject_invalid_and_retroactive_failure_counts(self):
        state, source = SessionIntegrityState(), object()
        report = {'version': 1, 'constructions': 2, 'copies': 2, 'failures': 0}
        state.observe_runtime_protected_report(source, report)
        before = state.public_report()
        for bad in ({**report, 'failures': 1}, {**report, 'copies': True},
                    {**report, 'constructions': 1}, {**report, 'flags': 0}):
            with self.assertRaises(ValueError):state.observe_runtime_protected_report(source, bad)
            self.assertEqual(state.public_report(), before)

    def test_persistent_adapter_forwards_protected_observations_with_current_session(self):
        from persistent_runtime import PersistentClientScriptRuntime
        runtime = PersistentClientScriptRuntime(lambda: None, 1)
        runtime.host = object()
        state = SessionIntegrityState()
        result = {'ok': True, 'runtime_protected_integrity':
                  {'version': 1, 'constructions': 3, 'copies': 3, 'failures': 0}}
        with state.observe_containers():
            runtime._record(result)
            runtime._record(result)
        self.assertEqual(state.public_report()['protected_string_copies'], 3)

    def test_runtime_thread_observations_are_cumulative_and_session_owned(self):
        state = SessionIntegrityState()
        first, second = object(), object()
        state.observe_runtime_thread_report(first, {'version': 1, 'checks': 3, 'failures': 0})
        state.observe_runtime_thread_report(first, {'version': 1, 'checks': 3, 'failures': 0})
        state.observe_runtime_thread_report(first, {'version': 1, 'checks': 4, 'failures': 1})
        state.observe_runtime_thread_report(second, {'version': 1, 'checks': 2, 'failures': 0})
        report = state.public_report()
        self.assertEqual((report['script_thread_checks'], report['script_thread_failures']), (6, 1))
        self.assertEqual(report['observed_global_flags'], 0x10)
        self.assertEqual(SessionIntegrityState().public_report()['script_thread_checks'], 0)
        self.assertEqual(state.status_word(), state.public_report()['observed_global_flags'])

    def test_invalid_runtime_thread_reports_do_not_change_observations(self):
        state, source = SessionIntegrityState(), object()
        state.observe_runtime_thread_report(source, {'version': 1, 'checks': 2, 'failures': 0})
        before = state.public_report()
        for report in ({'version': 1, 'checks': 1, 'failures': 0},
                       {'version': 1, 'checks': True, 'failures': 0},
                       {'version': 1, 'checks': 2, 'failures': 3},
                       {'version': 1, 'checks': 2, 'failures': 0, 'flags': 0}):
            with self.assertRaises(ValueError):
                state.observe_runtime_thread_report(source, report)
            self.assertEqual(state.public_report(), before)

    def test_persistent_adapter_forwards_the_actual_host_report_inside_session_scope(self):
        from persistent_runtime import PersistentClientScriptRuntime
        runtime = PersistentClientScriptRuntime(lambda: None, 1)
        runtime.host = object()
        state = SessionIntegrityState()
        result = {'ok': True, 'runtime_thread_integrity': {'version': 1, 'checks': 5, 'failures': 0}}
        with state.observe_containers():
            runtime._record(result)
            runtime._record(result)
        self.assertEqual(state.public_report()['script_thread_checks'], 5)
        runtime._record({'ok': True, 'runtime_thread_integrity': {'version': 1, 'checks': 6, 'failures': 0}})
        self.assertEqual(state.public_report()['script_thread_checks'], 5)

    def test_background_catalog_probes_keep_session_scope_without_false_native_flags(self):
        from script_catalog import CodePreparationQueue
        states = [SessionIntegrityState(), SessionIntegrityState()]
        queue = CodePreparationQueue()
        try:
            # No fixture may pass signature verification or reach the VM.
            with patch('bytecode_signature.verify_signed_bytecode', return_value={'verification': False}):
                for index, (state, data) in enumerate(zip(states, (corrupt_container(), container_fixture(b'inert')))):
                    with state.observe_containers():
                        queue.resolve(index, bytes([index]), {'Bytecode': data}, {})
                    queue.pending[index][1].result(timeout=5)
                reports = [state.public_report() for state in states]
                self.assertEqual([r['container_candidate_checks'] for r in reports], [1, 1])
                self.assertEqual([r['container_candidate_failures'] for r in reports], [1, 0])
                self.assertEqual([r['container_checksum_checks'] for r in reports], [0, 0])
                self.assertEqual([r['observed_global_flags'] for r in reports], [0, 0])
                queue.resolve(2, b'2', {'Bytecode': corrupt_container()}, {})
                queue.pending[2][1].result(timeout=5)
                self.assertEqual([s.public_report() for s in states], reports)
        finally:
            queue.close()

    def test_successful_comparison_preserves_the_native_initial_status(self):
        state = SessionIntegrityState()
        contents = b'private fixture contents'
        with state.observe_containers():
            self.assertEqual(unpack_container(container_fixture(contents)), contents)
        report = state.public_report()
        self.assertEqual(report['container_checksum_checks'], 1)
        self.assertEqual(report['container_checksum_failures'], 0)
        self.assertEqual(report['observed_global_flags'], 0)
        self.assertTrue(report['wire_status_available'])
        self.assertNotIn('private fixture contents', str(report))
        self.assertEqual(state.status_word(), 0)

    def test_actual_checksum_rejection_sets_sticky_native_bit_without_accepting_input(self):
        state = SessionIntegrityState()
        with state.observe_containers():
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                unpack_container(corrupt_container())
            self.assertEqual(unpack_container(container_fixture(b'next valid container')), b'next valid container')
        report = state.public_report()
        self.assertEqual(report['container_checksum_checks'], 2)
        self.assertEqual(report['container_checksum_failures'], 1)
        self.assertEqual(report['observed_global_flags'], 0x40)
        self.assertEqual(SessionIntegrityState().public_report()['observed_global_flags'], 0)
        self.assertEqual(state.status_word(), state.public_report()['observed_global_flags'])

    def test_structural_rejections_are_not_misreported_as_checksum_failures(self):
        state = SessionIntegrityState()
        with state.observe_containers():
            with self.assertRaises(ValueError):
                unpack_container(b'short')
            self.assertEqual(state.public_report()['container_checksum_checks'], 0)
            with self.assertRaisesRegex(ValueError, 'expansion exceeds limit'):
                unpack_container(container_fixture(b'a', declared_size=MAX_BYTES + 1))
        report = state.public_report()
        self.assertEqual(report['container_checksum_checks'], 1)
        self.assertEqual(report['container_checksum_failures'], 0)
        self.assertEqual(report['observed_global_flags'], 0)

    def test_scopes_restore_after_nested_failure_and_do_not_observe_off_session_work(self):
        outer, inner = SessionIntegrityState(), SessionIntegrityState()
        valid = container_fixture(b'valid')
        with outer.observe_containers():
            unpack_container(valid)
            with self.assertRaises(ValueError), inner.observe_containers():
                unpack_container(corrupt_container())
            unpack_container(valid)
        unpack_container(valid)
        self.assertEqual(outer.public_report()['container_checksum_checks'], 2)
        self.assertEqual(outer.public_report()['observed_global_flags'], 0)
        self.assertEqual(inner.public_report()['container_checksum_checks'], 1)
        self.assertEqual(inner.public_report()['observed_global_flags'], 0x40)

    def test_overlapping_sessions_do_not_share_telemetry(self):
        barrier = Barrier(2)

        def work(damaged):
            state = SessionIntegrityState()
            with state.observe_containers():
                barrier.wait(timeout=5)
                try:
                    unpack_container(corrupt_container() if damaged else container_fixture(b'valid'))
                except ValueError:
                    if not damaged:
                        raise
            return state.public_report()

        with ThreadPoolExecutor(max_workers=2) as pool:
            bad, good = pool.map(work, (True, False))
        self.assertEqual((bad['container_checksum_checks'], good['container_checksum_checks']), (1, 1))
        self.assertEqual((bad['observed_global_flags'], good['observed_global_flags']), (0x40, 0))

    def test_auth_handler_keeps_signature_and_execution_gates_and_emits_only_checksum_bit(self):
        job = '00000000-0000-0000-0000-000000000001'

        def packet(program):
            return b'\x9b' + struct.pack('<III', 2, 3, len(program)) + program

        state = SessionIntegrityState()
        handler = BytecodeAuthHandler(job,
            verify=lambda _: self.fail('checksum failure reached signature verification'),
            normalize=lambda _: self.fail('invalid input reached opcode conversion'),
            evaluate=lambda *a, **k: self.fail('inert fixture was executed'))
        with state.observe_containers(), self.assertRaises(BytecodeAuthError):
            handler.handle(packet(corrupt_container()))
        self.assertEqual(state.public_report()['observed_global_flags'], 0x40)
        self.assertEqual(handler.responses_prepared, 0)

        state = SessionIntegrityState()
        handler = BytecodeAuthHandler(job, verify=lambda _: {'verification': False},
            normalize=lambda _: self.fail('failed signature reached opcode conversion'),
            evaluate=lambda *a, **k: self.fail('inert fixture was executed'))
        with state.observe_containers(), self.assertRaises(BytecodeAuthError):
            handler.handle(packet(container_fixture(b'unsigned inert fixture')))
        self.assertEqual(state.public_report()['container_checksum_checks'], 1)
        self.assertEqual(state.public_report()['observed_global_flags'], 0)
        self.assertEqual(handler.responses_prepared, 0)


if __name__ == '__main__':
    unittest.main()
