"""Process-boundary integration checks, using only synthetic local source."""
import unittest
import subprocess
import tempfile
from pathlib import Path

from local_runtime import HOST, run_source, run_bytecode

MODEL = {'mode': 'offline', 'job_id': 'offline-fixture-job',
         'is_studio': False, 'services': ['Workspace', 'Players', 'RunService']}


@unittest.skipUnless(HOST.is_file(), 'build the optional local Luau host first')
class LocalRuntimeTests(unittest.TestCase):
    @unittest.skipUnless((HOST.parent / 'luau-source-to-bytecode').is_file(),
                         'build the trusted fixture compiler first')
    def test_compiled_authored_fixture_matches_source_with_engine_model(self):
        source = '''local a,b=...; return a+b,game.JobId,UserSettings().Name,
game:GetService("RunService"):IsStudio()'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture.luau'
            out = Path(folder) / 'fixture.luac'
            path.write_text(source)
            subprocess.run([str(HOST.parent / 'luau-source-to-bytecode'), str(path), str(out)],
                           capture_output=True, check=True, timeout=5)
            compiled = out.read_bytes()
            result = run_bytecode(compiled, [2, 5], model_context=MODEL)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [7, 'offline-fixture-job', 'GlobalBasicSettings', False])
        self.assertEqual(result, run_source(source, [2, 5], model_context=MODEL))
        job = '00000000-0000-0000-0000-000000000001'
        session_result = run_bytecode(compiled, [2, 5], session_job_id=job)
        self.assertTrue(session_result['ok'], session_result)
        self.assertEqual(session_result['values'], [7, job, 'GlobalBasicSettings', False])

    def test_bytecode_interface_rejects_roblox_envelope_as_standard_input(self):
        result = run_bytecode(b'RSB1' + bytes(24))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['values'], [])

    def test_numeric_arguments_and_nil_returns_survive_process_boundary(self):
        result = run_source('local a,b=...; return a+b,nil,true,"hello"', [2, 5])
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [7, None, True, 'hello'])

    def test_fresh_vm_and_sandbox_surface(self):
        self.assertTrue(run_source('privateGlobal=42; return privateGlobal')['ok'])
        result = run_source('return privateGlobal,io,require,os.exit,game,UserSettings')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [None] * 6)

    def test_caught_infinite_loop_terminates_without_success(self):
        result = run_source('pcall(function() while true do end end); return "escaped"',
                            timeout_ms=25)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['values'], [])

    def test_vm_provides_metamethods_proxy_and_protected_call_varargs(self):
        result = run_source('''
local proxy = newproxy(true)
getmetatable(proxy).__namecall = function(_, value) return value * 2 end
local box = setmetatable({value=3}, {
    __add=function(a,b) return a.value+b end,
})
local ok, value = xpcall(function(a,b) return a+b end, tostring, 4, 9)
return proxy:Double(6), box+5, ok, value, bit32.bor(0x10,3)
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [12, 8, True, 13, 19])

    def test_local_model_has_canonical_identity_and_parent_references(self):
        result = run_source('''
local w = game:GetService("Workspace")
local s = UserSettings()
local keys = {[w] = 123, [s] = 456}
return game.JobId, keys[game:GetService("Workspace")], keys[UserSettings()],
    rawequal(w.Parent, game), w:IsA("Instance"), game:IsA("ServiceProvider"),
    tostring(s), game:GetService("RunService"):IsStudio()
''', model_context=MODEL)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['offline-fixture-job', 123, 456,
                                          True, True, True, 'GlobalBasicSettings', False])

    def test_foreign_userdata_cannot_be_used_as_an_instance_receiver(self):
        result = run_source('''
local w = game:GetService("Workspace")
local r = game:GetService("RunService")
local foreign = Random.new(1)
local a = pcall(w.IsA, foreign, "Instance")
local b = pcall(game.GetService, newproxy(true), "Workspace")
local c = pcall(r.IsStudio, foreign)
return a,b,c
''', model_context=MODEL)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False, False, False])

    def test_model_configuration_is_explicit_and_bad_services_raise(self):
        result = run_source('''
local ok = pcall(function() return game:GetService("MissingService") end)
local writable = pcall(function() game.JobId="changed" end)
return game:FindService("MissingService"), ok, writable
''', model_context=MODEL)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [None, False, False])


if __name__ == '__main__':
    unittest.main()
