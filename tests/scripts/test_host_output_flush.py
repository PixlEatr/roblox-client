"""Host output is returned promptly without dropping unfinished VM tasks."""
import copy
import os
import time
import unittest
from unittest import mock

from persistent_runtime import PersistentLuauHost
from test_cframe_transforms import SNAPSHOT


class HostOutputFlushTests(unittest.TestCase):
    def setUp(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        for class_name, name in [('RemoteEvent', 'Event'), ('RemoteFunction', 'Remote')]:
            snapshot['nodes'].append(dict(id='scope:' + name, class_name=class_name,
                name=name, parent='scope:workspace', properties={}))
        with mock.patch.dict(os.environ, {'RCR_LUAU_LARGE_TEST': '1'}):
            self.host = PersistentLuauHost(snapshot)
        self.addCleanup(self.host.close)

    def execute(self, source, budget=250):
        return self.host.call(dict(op='execute', source=source, timeout_ms=budget))

    def finish_busy_tasks(self):
        result = self.execute('releaseBusy=true;return true')
        self.assertTrue(result['ok'], result)
        for _ in range(5):
            if result['pending_tasks'] == 0:
                break
            result = self.host.call(dict(op='poll', timeout_ms=250))
        self.assertEqual(result['pending_tasks'], 0, result)
        self.assertFalse(result['remote_server_events'])
        self.assertFalse(result['remote_server_calls'])
        return self.execute('return outputTaskFinished,ordinaryFinished')

    def test_callback_output_does_not_wait_for_the_rest_of_a_large_slice(self):
        start = time.monotonic()
        result = self.execute('''releaseBusy=false;ordinaryFinished=0
task.defer(function()
    workspace.Event:FireServer("actual queued output")
    while not releaseBusy do end
    outputTaskFinished=true
end)
for i=1,32 do task.defer(function()
    while not releaseBusy do end
    ordinaryFinished+=1
end) end
return true''')
        elapsed = time.monotonic() - start
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['remote_server_events']), 1, result)
        self.assertEqual(result['remote_server_events'][0]['arguments'], ['actual queued output'])
        self.assertGreaterEqual(result['pending_tasks'], 33, result)
        # A generous bound compared with the 2 ms callback quantum, but below
        # the 250 ms transaction that previously withheld the queued output.
        self.assertLess(elapsed, .18)
        self.assertEqual(self.finish_busy_tasks()['values'], [True, 32])

    def test_pending_request_and_reply_continuation_flush_without_losing_work(self):
        start = time.monotonic()
        result = self.execute('''releaseBusy=false;ordinaryFinished=0
for i=1,32 do task.defer(function()
    while not releaseBusy do end
    ordinaryFinished+=1
end) end
local value=workspace.Remote:InvokeServer("actual request")
workspace.Event:FireServer(value)
while not releaseBusy do end
outputTaskFinished=true
return true''')
        self.assertLess(time.monotonic() - start, .18)
        self.assertEqual(len(result['remote_server_calls']), 1, result)
        request = result['remote_server_calls'][0]
        self.assertIn(request['request_id'], result['active_remote_server_requests'])
        start = time.monotonic()
        response = self.host.call(dict(op='remote_server_results', timeout_ms=250,
            responses=[dict(node=request['node'], request_id=request['request_id'],
                            ok=True, values=['actual fixture response'])]))
        self.assertLess(time.monotonic() - start, .18)
        self.assertEqual(len(response['remote_server_events']), 1, response)
        self.assertEqual(response['remote_server_events'][0]['arguments'], ['actual fixture response'])
        self.assertFalse(response['active_remote_server_requests'])
        self.assertEqual(self.finish_busy_tasks()['values'], [True, 32])


if __name__ == '__main__':
    unittest.main()
