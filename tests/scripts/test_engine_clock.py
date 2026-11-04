"""Actual device-clock behavior and the failing Animate seed expression."""
import time
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class EngineClockTests(unittest.TestCase):
    def test_tick_uses_device_epoch_time_and_supports_animation_seed(self):
        host = PersistentLuauHost(SNAPSHOT)
        self.addCleanup(host.close)
        before = time.time()
        result = host.call({'op': 'execute', 'source': '''
local now=tick()
math.randomseed(now)
return now, type(math.random()), type(tick)
''', 'timeout_ms': 100})
        after = time.time()
        self.assertTrue(result['ok'], result)
        timestamp, random_type, clock_type = result['values']
        self.assertLessEqual(before, timestamp)
        self.assertLessEqual(timestamp, after)
        self.assertEqual((random_type, clock_type), ('number', 'function'))


if __name__ == '__main__':
    unittest.main()
