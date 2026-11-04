"""Persistent callback CPU slices must preserve progress and foreground access."""
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from persistent_runtime import PersistentLuauHost


class CpuSliceTests(unittest.TestCase):
    def setUp(self):
        self.host=PersistentLuauHost(json.loads((ROOT/'tests/fixtures/world_snapshot.json').read_text()))
        self.addCleanup(self.host.close)

    def test_long_callback_finishes_across_tiny_slices_without_losing_progress(self):
        result=self.host.call({'op':'execute','timeout_ms':1000,'source':'''getgenv().sliceResult=nil
task.delay(.02,function()
    local sum=0
    for i=1,2000000 do sum+=i end
    sliceResult=sum
    print("CPU_SLICE_FINISHED")
end)
return "armed"'''})
        self.assertTrue(result['ok'],result)
        time.sleep(.03)
        output=result.get('output','');polls=0;deadline=time.monotonic()+8
        while 'CPU_SLICE_FINISHED' not in output and time.monotonic()<deadline:
            result=self.host.call({'op':'poll','timeout_ms':1});polls+=1
            self.assertTrue(result['ok'],result)
            self.assertFalse(result['task_errors'],result)
            output+=result.get('output','')
        self.assertIn('CPU_SLICE_FINISHED',output)
        self.assertGreater(polls,1)
        self.assertEqual(self.host.call({'op':'execute','source':'return sliceResult'})['values'],[2000001000000])

    def test_foreground_infinite_loop_still_times_out(self):
        result=self.host.call({'op':'execute','source':'while true do end','timeout_ms':25})
        self.assertFalse(result['ok'],result)
        self.assertTrue(result['timed_out'],result)
        self.assertEqual(self.host.call({'op':'execute','source':'return 42'})['values'],[42])

    def test_non_yielding_background_task_remains_bounded(self):
        self.host.call({'op':'execute','source':'task.delay(.02,function()while true do end end)'})
        time.sleep(.03)
        errors=[];deadline=time.monotonic()+8
        while not errors and time.monotonic()<deadline:
            result=self.host.call({'op':'poll','timeout_ms':50})
            self.assertTrue(result['ok'],result)
            errors.extend(result['task_errors'])
        self.assertEqual(len(errors),1,errors)
        self.assertEqual(self.host.call({'op':'execute','source':'return 73'})['values'],[73])


if __name__=='__main__':unittest.main()
