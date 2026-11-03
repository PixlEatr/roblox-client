"""Current-session RTT reaches Stats without inventing absent measurements."""
import json
from pathlib import Path
import struct
import time
import unittest

from dummy_sync import TimeSyncProbe
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_world_snapshot import fixture

ROOT = Path(__file__).resolve().parents[1]
PING = 'game:GetService("Stats").Network.ServerStatsItem["Data Ping"]'


class SessionStatsTests(unittest.TestCase):
    def host(self, statistics=None):
        snapshot = json.loads((ROOT/'runtime/tests/world_snapshot.json').read_text())
        if statistics is not None:
            snapshot['session_stats'] = statistics
        host = PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        result = host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        return result['values']

    def test_missing_measurement_is_unknown_and_new_session_does_not_inherit(self):
        measured = self.host({'network_rtt_ms': 125.25})
        self.assertEqual(self.execute(measured, f'return {PING}:GetValue()'), [125.25])
        fresh = self.host()
        values = self.execute(fresh, f'local ok=pcall(function()return {PING}:GetValue()end);return ok')
        self.assertEqual(values, [False])

    def test_same_stats_item_tracks_new_measurement_and_withdrawal(self):
        host = self.host({'network_rtt_ms': 12.75})
        self.assertEqual(self.execute(host, f'getgenv().ping={PING};return ping:GetValue(),ping:GetValueString()'),
                         [12.75, '12.750 ms'])
        for sample in ({'network_rtt_ms': 31.125}, {}):
            update = host.call({'op': 'update', 'delta': {
                'version': 1, 'delta': True, 'actions': [], 'session_stats': sample}})
            self.assertTrue(update['ok'], update)
        self.assertEqual(self.execute(host, f'local ok=pcall(function()return ping:GetValue()end);return ping=={PING},ok'),
                         [True, False])

    def test_invalid_measurement_is_rejected_without_replacing_last_sample(self):
        host = self.host({'network_rtt_ms': 20})
        for sample in ({'network_rtt_ms': -1}, {'network_rtt_ms': True},
                       {'network_rtt_ms': '20'}, {'clean': True}):
            result = host.call({'op': 'update', 'delta': {
                'version': 1, 'delta': True, 'actions': [], 'session_stats': sample}})
            self.assertFalse(result['ok'], result)
            self.assertEqual(self.execute(host, f'return {PING}:GetValue()'), [20])

    def test_provider_updates_without_world_revision_and_background_acknowledges(self):
        world, _ = fixture()
        sample = {}
        runtime = PersistentClientScriptRuntime(lambda: world, 123,
            session_stats_provider=lambda: sample)
        self.addCleanup(runtime.close)
        runtime._ensure()
        sample['network_rtt_ms'] = 57.5
        runtime.synchronize()
        self.assertEqual(self.execute(runtime.host, f'return {PING}:GetValue()'), [57.5])
        sample['network_rtt_ms'] = 81.25
        deadline = time.monotonic()+3
        while runtime._session_stats_sent != sample and time.monotonic() < deadline:
            runtime.poll_background()
            time.sleep(.01)
        self.assertEqual(runtime._session_stats_sent, sample)
        self.assertEqual(self.execute(runtime.host, f'return {PING}:GetValue()'), [81.25])

    def test_probe_exports_only_a_matching_response_from_its_own_session(self):
        probe = TimeSyncProbe()
        self.assertEqual(probe.runtime_statistics(), {})
        probe.start(1.)
        probe.due(1., 1000000)
        payload = b'\3'+struct.pack('<QQQ', 1000000, 0, 0)
        self.assertTrue(probe.receive(payload, 1.125))
        self.assertEqual(probe.runtime_statistics(), {'network_rtt_ms': 125})
        self.assertFalse(probe.receive(payload, 2.))
        self.assertEqual(probe.runtime_statistics(), {'network_rtt_ms': 125})
        self.assertEqual(TimeSyncProbe().runtime_statistics(), {})


if __name__ == '__main__':
    unittest.main()
