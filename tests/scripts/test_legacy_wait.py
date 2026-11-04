import time
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class LegacyWaitTests(unittest.TestCase):
    def host(self):
        host=PersistentLuauHost(SNAPSHOT);self.addCleanup(host.close);return host

    def execute(self, host, source):
        result=host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertTrue(result['ok'],result.get('error'))
        job=result['job_id'];end=time.monotonic()+3
        while True:
            for done in result.get('completed_jobs',[]):
                if done['job_id']==job:
                    self.assertTrue(done['ok'],done.get('error'));return done['values']
            if time.monotonic()>end:self.fail('wait did not resume')
            time.sleep(.005);result=host.call({'op':'poll','timeout_ms':50})

    def test_wait_returns_elapsed_duration_and_engine_uptime(self):
        values=self.execute(self.host(),'''
local before=elapsedTime()
local values=table.pack(wait(.04))
local after=elapsedTime()
return values.n,values[1],values[2],before,after
''')
        count,duration,uptime,before,after=values
        self.assertEqual(count,2);self.assertGreaterEqual(duration,.04)
        self.assertGreaterEqual(uptime,before+.04);self.assertLessEqual(uptime,after)
        self.assertLess(abs((uptime-before)-duration),.05)

    def test_default_zero_and_negative_wait_use_legacy_minimum(self):
        values=self.execute(self.host(),'''
local a=wait();local b=wait(0);local c=wait(-1)
return a,b,c
''')
        self.assertTrue(all(value>=.029 for value in values))

    def test_task_wait_keeps_single_result_and_shorter_duration(self):
        values=self.execute(self.host(),'''
local results=table.pack(task.wait(.001))
return results.n,results[1]
''')
        self.assertEqual(values[0],1);self.assertGreaterEqual(values[1],.001)

    def test_wait_can_yield_inside_bindable_callback(self):
        values=self.execute(self.host(),'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function()local duration,clock=wait(.03);return duration,clock end
local duration,clock=f:Invoke()
return duration>=.03,clock>=duration
''')
        self.assertEqual(values,[True,True])

    def test_invalid_durations_raise_before_suspending(self):
        values=self.execute(self.host(),'''
return pcall(wait,0/0),pcall(wait,math.huge),pcall(wait,90000),pcall(wait,{})
''')
        self.assertEqual(values[:4],[False]*4)

    def test_uptime_increases_across_requests_without_using_epoch_time(self):
        host=self.host();first=self.execute(host,'return elapsedTime()')[0]
        time.sleep(.03);second=self.execute(host,'return elapsedTime()')[0]
        self.assertGreaterEqual(second-first,.03)
        self.assertLess(second,60)


if __name__=='__main__':unittest.main()
