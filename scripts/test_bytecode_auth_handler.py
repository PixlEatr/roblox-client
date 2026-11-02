"""Control-flow tests use synthetic inputs, never live authentication answers."""
import struct
import unittest

from bytecode_check import BytecodeAuthError, BytecodeAuthHandler, native_unsigned_result


JOB = '00000000-0000-0000-0000-000000000001'


class HandlerTests(unittest.TestCase):
    def handler(self, evaluate=None, verify=None, max_checks=16):
        self.calls = []
        def evaluator(program, args, **kwargs):
            self.calls.append((program, args, kwargs))
            return {'ok': True, 'values': [123.75, 999]}
        return BytecodeAuthHandler(JOB, max_checks=max_checks,
            unpack=lambda blob: b'original-' + blob,
            verify=verify or (lambda program: {'verification': True}),
            normalize=lambda program: (b'compiled-local-fixture', {}),
            evaluate=evaluate or evaluator)

    def packet(self, first=2, second=3):
        return b'\x9b' + struct.pack('<III', first, second, 4) + b'test'

    def test_native_argument_order_reply_echo_first_numeric_return_and_replay(self):
        handler = self.handler()
        response = handler.handle(self.packet())
        self.assertEqual(response, b'\x9b' + struct.pack('<II', 3, 123))
        self.assertEqual(self.calls[0][1], (3, 2))
        self.assertEqual(self.calls[0][2]['session_job_id'], JOB)
        self.assertEqual(handler.handle(self.packet()), response)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(handler.summary()['replays'], 1)

    def test_failed_signature_does_not_normalize_execute_or_reply(self):
        handler = self.handler(verify=lambda program: {'verification': False})
        handler.normalize = lambda _: self.fail('Normalization before verification')
        with self.assertRaises(BytecodeAuthError): handler.handle(self.packet())
        self.assertEqual(self.calls, [])
        self.assertEqual(handler.responses_prepared, 0)

    def test_runtime_failure_and_non_numeric_result_do_not_reply(self):
        for output in ({'ok': False, 'values': [], 'error': 'private fixture error'},
                       {'ok': True, 'values': []},
                       {'ok': True, 'values': [True]},
                       {'ok': True, 'values': ['123']},
                       {'ok': True, 'values': [float('inf')]}):
            handler = self.handler(evaluate=lambda *args, **kwargs: output)
            with self.assertRaises(BytecodeAuthError): handler.handle(self.packet())
            self.assertEqual(handler.responses_prepared, 0)
            self.assertNotIn('private fixture error', str(handler.summary()))

    def test_checks_are_bounded_without_consuming_budget_for_replays(self):
        handler = self.handler(max_checks=1)
        handler.handle(self.packet())
        handler.handle(self.packet())
        with self.assertRaises(BytecodeAuthError): handler.handle(self.packet(4, 5))
        self.assertEqual(len(self.calls), 1)

    def test_native_unsigned_cast_saturates_instead_of_wrapping(self):
        for number, expected in ((-1.25, 0), (-0.25, 0), (1.75, 1),
                                  (4294967295, 4294967295),
                                  (4294967296, 4294967295)):
            self.assertEqual(native_unsigned_result(number), expected)
        for invalid in (True, '1', float('nan'), float('inf')):
            with self.assertRaises(ValueError): native_unsigned_result(invalid)


if __name__ == '__main__':
    unittest.main()
