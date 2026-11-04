"""Opt-in, local-only attribution for actual InvokeServer calls."""
import copy
import json
import os
import unittest
from unittest import mock

from persistent_runtime import PersistentLuauHost
from test_cframe_transforms import SNAPSHOT
import test_script_execution as script_tests
from test_replication_rpc import fixture


class RemoteCallDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        _, _, keys = fixture()
        self.node = '%s:%s' % keys[3]

    def host(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        snapshot['nodes'].append(dict(id=self.node, class_name='RemoteFunction',
                                      name='DiagnosticRemote', parent='scope:workspace',
                                      properties={}))
        host = PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def test_trace_is_off_by_default_and_wire_shape_is_unchanged(self):
        with mock.patch.dict(os.environ, {'RCR_TRACE_REMOTE_CALLS': ''}):
            host = self.host()
            result = host.call({'op': 'execute', 'source':
                                'return workspace.DiagnosticRemote:InvokeServer("private-sentinel")',
                                'timeout_ms': 100})
        self.assertEqual(result['remote_call_diagnostics'], [])
        call = result['remote_server_calls'][0]
        self.assertEqual(set(call), {'node', 'request_id', 'arguments'})
        self.assertEqual(call['arguments'], ['private-sentinel'])

    def test_trace_has_bounded_caller_metadata_but_never_arguments(self):
        with mock.patch.dict(os.environ, {'RCR_TRACE_REMOTE_CALLS': '1'}):
            host = self.host()
            result = host.call({'op': 'execute', 'source':
                                'return workspace.DiagnosticRemote:InvokeServer("private-sentinel", 123456)',
                                'timeout_ms': 100})
        self.assertEqual(len(result['remote_call_diagnostics']), 1, result)
        diagnostic = result['remote_call_diagnostics'][0]
        self.assertEqual(diagnostic['request_id'], result['remote_server_calls'][0]['request_id'])
        self.assertEqual(diagnostic['node_id'], self.node)
        self.assertEqual(diagnostic['node_name'], 'DiagnosticRemote')
        self.assertIn('DiagnosticRemote', diagnostic['instance_path'])
        # Console-launched authored jobs have no script owner by design.
        self.assertEqual(diagnostic['scheduler_owner'], '')
        self.assertGreaterEqual(len(diagnostic['frames']), 1)
        self.assertLessEqual(len(diagnostic['frames']), 8)
        self.assertTrue(any('persistent-source' in frame['source'] for frame in diagnostic['frames']))
        self.assertNotIn('arguments', diagnostic)
        encoded = json.dumps(diagnostic)
        self.assertNotIn('private-sentinel', encoded)
        self.assertNotIn('123456', encoded)

    def test_trace_reports_owner_for_received_local_script(self):
        data = script_tests.snapshot()
        script_tests.add_script(data, 'DiagnosticStartup',
                                'workspace.DiagnosticRemote:InvokeServer("script-sentinel")',
                                kind='LocalScript', parent='gui')
        data['nodes'].append(dict(id=self.node, class_name='RemoteFunction',
                                  name='DiagnosticRemote', parent='scope:workspace',
                                  properties={}))
        with mock.patch.dict(os.environ, {'RCR_TRACE_REMOTE_CALLS': '1'}):
            host = PersistentLuauHost(data)
            self.addCleanup(host.close)
            result = host.initial_result
            for _ in range(10):
                if result.get('remote_call_diagnostics'):
                    break
                result = host.call({'op': 'poll', 'timeout_ms': 20})
        self.assertEqual(len(result['remote_call_diagnostics']), 1, result)
        diagnostic = result['remote_call_diagnostics'][0]
        self.assertEqual(diagnostic['scheduler_owner'], 'script:DiagnosticStartup')
        self.assertEqual(diagnostic['node_name'], 'DiagnosticRemote')
        self.assertNotIn('script-sentinel', json.dumps(diagnostic))

    def test_reply_continues_waiter_and_diagnostic_drains_once(self):
        with mock.patch.dict(os.environ, {'RCR_TRACE_REMOTE_CALLS': '1'}):
            host = self.host()
            first = host.call({'op': 'execute', 'source':
                               'local value = workspace.DiagnosticRemote:InvokeServer("secret")\nreturn value + 1',
                               'timeout_ms': 100})
            call = first['remote_server_calls'][0]
            reply = host.call({'op': 'remote_server_results', 'timeout_ms': 100,
                               'responses': [{'node': call['node'], 'request_id': call['request_id'],
                                              'ok': True, 'values': [41]}]})
            for _ in range(10):
                if any(job.get('job_id') == first['job_id'] for job in reply.get('completed_jobs', [])):
                    break
                reply = host.call({'op': 'poll', 'timeout_ms': 20})
        completed = next(job for job in reply['completed_jobs'] if job['job_id'] == first['job_id'])
        self.assertEqual(completed['values'], [42])
        self.assertEqual(len(first['remote_call_diagnostics']), 1)
        self.assertEqual(reply['remote_call_diagnostics'], [])
        drained = host.call({'op': 'poll', 'timeout_ms': 10})
        self.assertEqual(drained['remote_call_diagnostics'], [])


if __name__ == '__main__':
    unittest.main()
