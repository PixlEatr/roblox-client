"""Regression for persistent callbacks when a poll budget is almost exhausted."""
import copy
import json
from pathlib import Path
import time
import unittest

from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust host first')
class SchedulerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def test_burst_callbacks_survive_one_ms_polls_without_false_errors(self):
        count = 3000
        pid = self.host.process.pid
        queued = self.host.call({'op': 'execute', 'timeout_ms': 2000, 'source': f'''
getgenv().budgetSeen = 0
getgenv().budgetTarget = {count}
getgenv().budgetErrors = 0
getgenv().budgetEvent = Instance.new("BindableEvent")
budgetEvent.Event:Connect(function(record, index)
    if type(record) ~= "table" or record.index ~= index
        or #record.body ~= 256 or index ~= budgetSeen + 1 then
        budgetErrors += 1
        error("callback order or payload changed")
    end
    budgetSeen += 1
    if budgetSeen == budgetTarget then print("SCHEDULER_BURST_DRAINED", budgetSeen) end
end)
for i = 1, budgetTarget do
    budgetEvent:Fire({{index = i, body = string.rep("q", 256)}}, i)
end
return budgetTarget
'''})
        self.assertTrue(queued['ok'], queued)
        self.assertEqual(queued['values'], [count])
        self.assertEqual(queued['task_errors'], [])

        deadline = time.monotonic() + 12
        polls = 0
        output = queued.get('output', '')
        while 'SCHEDULER_BURST_DRAINED' not in output and time.monotonic() < deadline:
            result = self.host.call({'op': 'poll', 'timeout_ms': 1})
            polls += 1
            self.assertTrue(result['ok'], result)
            self.assertEqual(result.get('task_errors'), [], result)
            output += result.get('output', '')

        self.assertIn('SCHEDULER_BURST_DRAINED', output)
        self.assertGreater(polls, 0)
        self.assertEqual(self.host.process.pid, pid)
        final = self.host.call({'op': 'execute', 'timeout_ms': 100, 'source':
                                'return budgetSeen, budgetErrors, budgetTarget'})
        self.assertTrue(final['ok'], final)
        self.assertEqual(final['values'], [count, 0, count])


if __name__ == '__main__':
    unittest.main()
