"""Native GUID generation through both source hosts; synthetic world only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_runtime import HOST, RUST_HOST
from persistent_runtime import PersistentLuauHost

SNAPSHOT = json.loads((ROOT / 'runtime/tests/world_snapshot.json').read_text())


@unittest.skipUnless(HOST.is_file() and RUST_HOST.is_file(), 'build both native hosts first')
class GuidTests(unittest.TestCase):
    def execute(self, host, source):
        with tempfile.TemporaryDirectory(prefix='guid-test-') as folder:
            path = Path(folder)
            (path / 'source.luau').write_text(source)
            (path / 'world.json').write_text(json.dumps(SNAPSHOT))
            result = subprocess.run([str(host), '--source', str(path / 'source.luau'),
                                     '--world-snapshot', str(path / 'world.json'),
                                     '--timeout-ms', '2000'], capture_output=True, timeout=6)
        response = json.loads(result.stdout)
        self.assertTrue(response['ok'], response)
        self.assertEqual(result.returncode, 0, result.stderr)
        return response

    def assert_guid(self, text, braces):
        self.assertEqual(len(text), 38 if braces else 36)
        if braces:
            self.assertTrue(text.startswith('{') and text.endswith('}'))
            text = text[1:-1]
        parsed = uuid.UUID(text)
        self.assertEqual(parsed.version, 4)
        self.assertEqual(parsed.variant, uuid.RFC_4122)
        self.assertEqual(str(parsed), text)

    def test_default_explicit_and_nil_braces_match_both_hosts(self):
        for host in (HOST, RUST_HOST):
            with self.subTest(host=host.name):
                result = self.execute(host, '''
local h=game:GetService("HttpService")
assert(game:FindService("HttpService")==h)
return h:GenerateGUID(),h:GenerateGUID(true),h:GenerateGUID(false),h:GenerateGUID(nil)
''')
                for value, wrapped in zip(result['values'], (True, True, False, True)):
                    self.assert_guid(value, wrapped)

    def test_generation_is_unique_and_independent_of_luau_random_state(self):
        result = self.execute(RUST_HOST, '''
local h=game:GetService("HttpService")
math.randomseed(1729)
local before,after=math.random(),math.random()
math.randomseed(1729)
assert(math.random()==before)
local ids={}
for i=1,1024 do ids[i]=h:GenerateGUID(false) end
assert(math.random()==after,"GUID consumed math.random state")
return h:JSONEncode(ids)
''')
        values = json.loads(result['values'][0])
        self.assertEqual(len(values), 1024)
        self.assertEqual(len(set(values)), 1024)
        for value in values:
            self.assert_guid(value, False)

    def test_measured_studio_coercions_and_invalid_receivers(self):
        for host in (HOST, RUST_HOST):
            with self.subTest(host=host.name):
                result = self.execute(host, '''
local h=game:GetService("HttpService")
local function wrapped(value)return h:GenerateGUID(value):sub(1,1)=="{" end
assert(not wrapped(0) and wrapped(1) and wrapped(-1) and wrapped(2))
assert(wrapped(math.huge) and wrapped(0/0))
assert(wrapped("true") and wrapped("TRUE") and wrapped("TrUe"))
assert(not wrapped("false") and not wrapped("FALSE"))
for _,value in {"yes","","1","0"," true ","true\\0",{},workspace} do
    assert(not pcall(function()h:GenerateGUID(value)end))
end
assert(not pcall(function()h:GenerateGUID(function()end)end))
local copied=h.GenerateGUID
assert(not pcall(copied,workspace,false))
assert(not pcall(copied,{},false))
assert(#copied(h,false)==36)
assert(#h:GenerateGUID(false,"ignored")==36)
return true
''')
                self.assertEqual(result['values'], [True])

    def test_persistent_service_and_json_interoperate_across_commands(self):
        host = PersistentLuauHost(SNAPSHOT)
        self.addCleanup(host.close)
        first = host.call({'op': 'execute', 'source': '''
getgenv().http=game:GetService("HttpService")
getgenv().previousGuid=http:GenerateGUID(false)
return previousGuid
'''})
        self.assertTrue(first['ok'], first)
        self.assert_guid(first['values'][0], False)
        second = host.call({'op': 'execute', 'source': '''
assert(http==game:GetService("HttpService"))
local id=http:GenerateGUID(false)
assert(id~=previousGuid)
assert(http:JSONDecode(http:JSONEncode({id=id})).id==id)
return id
'''})
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['mutations'], [])
        self.assert_guid(second['values'][0], False)

    def test_demo_runs_and_cleans_up_its_objects(self):
        result = self.execute(RUST_HOST, (ROOT / 'runtime/scripts/guid_demo.luau').read_text())
        self.assertIn('PASS: GenerateGUID', result['output'])
        self.assert_guid(result['values'][0], False)
        self.assertTrue(any(effect['kind'] == 'destroy' for effect in result['mutations']))


if __name__ == '__main__':
    unittest.main()
