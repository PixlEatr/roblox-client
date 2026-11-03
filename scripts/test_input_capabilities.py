"""Local input-device availability, independent from replication and scripts."""
import copy
import unittest

from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_persistent_runtime import SNAPSHOT
from test_world_snapshot import fixture


class InputCapabilityTests(unittest.TestCase):
    def host(self, capabilities=None, received=False):
        snapshot = copy.deepcopy(SNAPSHOT)
        if capabilities is not None:
            snapshot['input_capabilities'] = capabilities
        if received:
            snapshot['nodes'].append({'id': 'fixture-input', 'class_name': 'UserInputService',
                                     'name': 'UserInputService', 'parent': 'game',
                                     'properties': {'TouchEnabled': True}})
            snapshot['services']['UserInputService'] = 'fixture-input'
        host = PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def execute(self, host, source):
        r = host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(r['ok'], r.get('error'))
        self.assertEqual(r.get('task_errors', []), [])
        return r['values']

    def update(self, host, capabilities):
        return host.call({'op': 'update', 'delta': {
            'version': 1, 'delta': True, 'actions': [], 'input_capabilities': capabilities},
            'timeout_ms': 100})

    def test_headless_backend_has_no_attached_devices_and_service_exists(self):
        self.assertEqual(self.execute(self.host(), '''
local input=game:FindService("UserInputService")
return input~=nil,input==game:GetService("UserInputService"),
    input.TouchEnabled,input.KeyboardEnabled,input.MouseEnabled,input.GamepadEnabled,
    input.AccelerometerEnabled,input.GyroscopeEnabled,input.VREnabled,
    client.propertyOrigin(input,"TouchEnabled")
'''), [True, True] + [False] * 7 + ['local_input'])

    def test_frontend_profile_is_readonly_and_does_not_mutate_input_fixture(self):
        capabilities = {'KeyboardEnabled': True, 'MouseEnabled': True}
        self.assertEqual(self.execute(self.host(capabilities), '''
local input=game:GetService("UserInputService")
return input.KeyboardEnabled,input.MouseEnabled,input.TouchEnabled,
    not pcall(function()input.KeyboardEnabled=false end),input.KeyboardEnabled
'''), [True, True, False, True, True])
        self.assertEqual(capabilities, {'KeyboardEnabled': True, 'MouseEnabled': True})

    def test_host_changes_emit_callbacks_once_and_survive_requests(self):
        host = self.host()
        self.execute(host, '''
getgenv().calls=0;getgenv().changed=0;getgenv().observed=false
local input=game:GetService("UserInputService")
input:GetPropertyChangedSignal("TouchEnabled"):Connect(function()
    calls+=1;observed=input.TouchEnabled
end)
input.Changed:Connect(function(key)if key=="TouchEnabled"then changed+=1 end end)
''')
        self.assertTrue(self.update(host, {'TouchEnabled': True})['ok'])
        self.assertEqual(self.execute(host, 'return calls,changed,observed'), [1, 1, True])
        self.assertTrue(self.update(host, {'TouchEnabled': True, 'MouseEnabled': True})['ok'])
        self.assertEqual(self.execute(host, 'return calls,changed,game:GetService("UserInputService").MouseEnabled'), [1, 1, True])
        self.assertTrue(self.update(host, {'TouchEnabled': False})['ok'])
        self.assertEqual(self.execute(host, 'return calls,changed,observed'), [2, 2, False])

    def test_invalid_host_patch_does_not_partially_change_profile(self):
        host = self.host({'TouchEnabled': True})
        for patch in [{'TouchEnabled': False, 'KeyboardEnabled': 1},
                      {'TouchEnabled': False, 'UnknownDevice': True}, [], None]:
            with self.subTest(patch=patch):
                self.assertFalse(self.update(host, patch)['ok'])
                self.assertEqual(self.execute(host, 'return game:GetService("UserInputService").TouchEnabled'), [True])

    def test_replicated_properties_cannot_override_device_state_or_emit_input_changes(self):
        host = self.host(received=True)
        self.execute(host, '''getgenv().changes=0
game:GetService("UserInputService"):GetPropertyChangedSignal("TouchEnabled"):Connect(function()changes+=1 end)''')
        r = host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
            'version': 1, 'delta': True, 'actions': [{'sequence': 8, 'kind': 'set',
                'id': 'fixture-input', 'fields': {'TouchEnabled': False}}]}})
        self.assertTrue(r['ok'])
        self.assertEqual(self.execute(host, 'return game:GetService("UserInputService").TouchEnabled,changes'), [False, 0])

    def test_adapter_copies_and_validates_the_initial_frontend_profile(self):
        world, _ = fixture()
        caps = {'MouseEnabled': True}
        runtime = PersistentClientScriptRuntime(lambda: world, 123, input_capabilities=caps)
        self.addCleanup(runtime.close)
        caps['MouseEnabled'] = False
        runtime._ensure()
        self.assertEqual(self.execute(runtime.host, 'return game:GetService("UserInputService").MouseEnabled'), [True])
        for bad in [[], 1, {'MouseEnabled': 1}, {'MadeUp': False}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PersistentClientScriptRuntime(lambda: world, 123, input_capabilities=bad)


if __name__ == '__main__':
    unittest.main()
