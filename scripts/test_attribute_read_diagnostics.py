"""Read observations must remain opt-in and must not satisfy script waits."""
import copy
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from persistent_runtime import PersistentLuauHost

SNAPSHOT = json.loads((Path(__file__).resolve().parents[1] /
                       'runtime/tests/world_snapshot.json').read_text())


class AttributeReadDiagnosticsTests(unittest.TestCase):
    def host(self, enabled):
        with patch.dict(os.environ, {'RCR_TRACE_ATTRIBUTE_READS': '1' if enabled else '0'}):
            host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 50})
        self.assertTrue(result['ok'], result)
        return result

    def test_default_has_no_read_observation(self):
        result = self.execute(self.host(False), '''
local part=Instance.new("Part")
part:GetAttribute("missing")
task.wait(100)
''')
        self.assertNotIn('last_attribute_read', result['job_diagnostics'][0])

    def test_missing_attribute_is_observed_without_satisfying_wait(self):
        host = self.host(True)
        self.execute(host, 'shared.part=Instance.new("Part");return true')
        result = self.execute(host, '''
local value=shared.part:GetAttribute("ap")
while value==nil do task.wait(.01);value=shared.part:GetAttribute("ap") end
return value
''')
        self.assertEqual(result['state'], 'running')
        first = result['job_diagnostics'][0]['last_attribute_read']
        self.assertEqual((first['attribute'], first['present'], first['result_type']),
                         ('ap', False, 'nil'))
        self.assertEqual(result['mutations'], [])
        changed = self.execute(host, 'shared.part:SetAttribute("ap",false);task.wait(.05)')
        completed = {job['job_id']: job for job in changed['completed_jobs']}
        deadline = time.monotonic() + 1
        while result['job_id'] not in completed and time.monotonic() < deadline:
            time.sleep(.01)
            polled = host.call({'op': 'poll', 'timeout_ms': 50})
            completed.update((job['job_id'], job) for job in polled['completed_jobs'])
        self.assertEqual(completed[result['job_id']]['values'], [False])

    def test_present_value_is_not_recorded_and_other_jobs_do_not_inherit_reads(self):
        host = self.host(True)
        result = self.execute(host, '''
local part=Instance.new("Part");part:SetAttribute("secret","DO_NOT_RECORD_VALUE")
assert(part:GetAttribute("secret")=="DO_NOT_RECORD_VALUE")
task.wait(100)
''')
        read = result['job_diagnostics'][0]['last_attribute_read']
        self.assertEqual(read['result_type'], 'string')
        self.assertTrue(read['present'])
        self.assertNotIn('DO_NOT_RECORD_VALUE', json.dumps(result['job_diagnostics']))
        second = self.execute(host, 'task.wait(100)')
        diagnostic = next(item for item in second['job_diagnostics']
                          if item['job_id'] == second['job_id'])
        self.assertNotIn('last_attribute_read', diagnostic)

    def test_many_reads_keep_bounded_diagnostics_and_unknown_still_errors(self):
        host = self.host(True)
        result = self.execute(host, '''
local part=Instance.new("Part")
for i=1,1000 do part:GetAttribute("missing"..i) end
task.wait(100)
''')
        read = result['job_diagnostics'][0]['last_attribute_read']
        self.assertEqual(read['attribute'], 'missing1000')
        self.assertEqual(read['sequence'], 1000)
        self.assertLess(len(json.dumps(result['job_diagnostics'])), 4000)
        unknown = self.execute(host, '''
local ok,message=pcall(function()return workspace:GetAttribute("unknown")end)
assert(not ok and message:find("attribute data is unavailable",1,true))
task.wait(100)
''')
        diagnostic = next(item for item in unknown['job_diagnostics']
                          if item['job_id'] == unknown['job_id'])
        self.assertEqual(diagnostic['last_attribute_read']['result_type'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
