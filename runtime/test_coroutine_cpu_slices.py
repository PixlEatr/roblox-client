"""CPU interruptions inside ordinary coroutine.resume/wrap must make progress."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest import mock

from persistent_runtime import PersistentLuauHost

ROOT = Path(__file__).resolve().parents[1]
WORK = 'local sum=0;for i=1,3000000 do sum+=i end;'
SUM = 4500001500000


class CoroutineCpuSliceTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(os.environ, {'RCR_LUAU_LARGE_TEST': '1'}):
            self.host = PersistentLuauHost(json.loads(
                (ROOT / 'runtime/tests/world_snapshot.json').read_text()))
        self.addCleanup(self.host.close)

    def finish(self, first):
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['state'], 'running', first)
        deadline = time.monotonic() + 5
        last = first
        while time.monotonic() < deadline:
            last = self.host.call({'op': 'poll', 'timeout_ms': 2})
            self.assertTrue(last['ok'], last)
            for result in last.get('completed_jobs', []):
                if result['job_id'] == first['job_id']:
                    return result
        self.fail(f"Interrupted coroutine did not complete: {last.get('job_diagnostics')}")

    def execute(self, source):
        return self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1})

    def test_wrapped_computation_finishes_without_restarting(self):
        result = self.finish(self.execute('''
shared.started=0
return coroutine.wrap(function()
 shared.started+=1
''' + WORK + '''
 return sum,shared.started
end)()
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [SUM, 1])

    def test_nested_resume_wrap_preserve_yields_nil_values_and_resume_arguments(self):
        result = self.finish(self.execute('''
return coroutine.wrap(function()
 local co=coroutine.create(function()
''' + WORK + '''
  local value=coroutine.yield(sum,nil,"yielded")
''' + WORK + '''
  return sum,nil,value
 end)
 local first=table.pack(coroutine.resume(co))
 assert(first.n==4 and first[1] and first[3]==nil and first[4]=="yielded")
 local second=table.pack(coroutine.resume(co,"resumed"))
 assert(second.n==4 and second[1] and second[3]==nil and second[4]=="resumed")
 return first[2],second[2],coroutine.status(co)
end)()
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [SUM, SUM, 'dead'])

    def test_nested_errors_keep_the_original_object(self):
        result = self.finish(self.execute('''
local marker={reason="expected"}
local ok,value=pcall(coroutine.wrap(function()
 local success,err=coroutine.resume(coroutine.create(function()
''' + WORK + '''
  error(marker)
 end))
 assert(not success and err==marker)
 error(marker)
end))
return not ok,value==marker
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True])

    def test_task_wait_after_interruption_keeps_the_child_scheduled(self):
        result = self.finish(self.execute('''
coroutine.wrap(function()
''' + WORK + '''
 task.wait(.005)
 shared.coroutineSum=sum
end)()
while shared.coroutineSum==nil do task.wait(.005)end
return shared.coroutineSum
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [SUM])

    def test_cancel_respects_vm_parent_state_and_leaves_foreground_usable(self):
        first = self.execute('''
getgenv().cancelRoot=coroutine.running()
return coroutine.wrap(function()while true do end end)()
''')
        self.assertEqual(first['state'], 'running')
        # Luau classifies this parent as normal while a child is suspended at
        # a break. Keep the existing restriction on cancelling normal threads.
        response = self.host.call({'op': 'execute', 'timeout_ms': 20,
                                   'source': 'local ok,err=pcall(task.cancel,cancelRoot);return 42,ok,type(err)'})
        self.assertTrue(response['ok'], response)
        self.assertEqual(response['values'], [42, False, 'string'])


if __name__ == '__main__':
    unittest.main()
