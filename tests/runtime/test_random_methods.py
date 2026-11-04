"""Authored Luau checks for the additional standalone Random methods."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HOSTS = [ROOT / "runtime/build/luau-host",
                 ROOT / "rust-client/target/release/headless-luau"]
HOSTS = ([Path(path) for path in os.environ["RANDOM_METHOD_TEST_HOSTS"].split(os.pathsep)]
         if "RANDOM_METHOD_TEST_HOSTS" in os.environ else DEFAULT_HOSTS)
SNAPSHOT = ROOT / "tests/fixtures/world_snapshot.json"


@unittest.skipUnless(all(host.is_file() for host in HOSTS), "build both native Luau hosts first")
class RandomMethodTests(unittest.TestCase):
    def execute(self, host, source, *, world=False):
        with tempfile.TemporaryDirectory(prefix="random-method-test-", dir="/tmp") as folder:
            path = Path(folder) / "source.luau"
            path.write_text(source)
            command = [str(host), "--source", str(path), "--timeout-ms", "2000"]
            if world:
                command.extend(["--world-snapshot", str(SNAPSHOT)])
            process = subprocess.run(command, capture_output=True, timeout=6)
        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        response = json.loads(process.stdout)
        self.assertTrue(response["ok"], response)
        self.assertFalse(response.get("task_errors"), response)
        return response["values"]

    def test_next_unit_vector_is_reproducible_uniform_and_uses_two_draws(self):
        source = '''
local a,b=Random.new(123),Random.new(123)
local v=a:NextUnitVector()
assert(typeof(v)=="Vector3")
assert(math.abs(math.sqrt(v.X*v.X+v.Y*v.Y+v.Z*v.Z)-1)<2e-6)
assert(v.X>=-1 and v.X<=1 and v.Y>=-1 and v.Y<=1 and v.Z>=-1 and v.Z<=1)
local same=b:NextUnitVector()
assert(v==same)
assert(a:NextNumber()==b:NextNumber())
local x,y,z,xx,yy,zz=0,0,0,0,0,0
local random=Random.new(8675309)
for _=1,4096 do
    local d=random:NextUnitVector()
    assert(math.abs(math.sqrt(d.X*d.X+d.Y*d.Y+d.Z*d.Z)-1)<2e-6)
    x+=d.X;y+=d.Y;z+=d.Z
    xx+=d.X*d.X;yy+=d.Y*d.Y;zz+=d.Z*d.Z
end
assert(math.abs(x/4096)<0.08 and math.abs(y/4096)<0.08 and math.abs(z/4096)<0.08)
assert(math.abs(xx/4096-1/3)<0.04 and math.abs(yy/4096-1/3)<0.04 and math.abs(zz/4096-1/3)<0.04)
local vectorState,numberState=Random.new(2468),Random.new(2468)
vectorState:NextUnitVector()
numberState:NextNumber()
assert(vectorState:NextNumber()==numberState:NextNumber(),"NextUnitVector must consume two uint32 draws, like one NextNumber")
return true
'''
        for host in HOSTS:
            with self.subTest(host=host.name):
                # World-source install configures Roblox's typeof for native
                # Vector3; the standalone compiler host leaves native vectors
                # with ordinary Luau vector identity.
                self.assertEqual(self.execute(host, source, world=True), [True])

    def test_shuffle_is_deterministic_in_place_and_uses_fisher_yates_draw_count(self):
        source = '''
local a,b=Random.new(42),Random.new(42)
local first={"a","b","c","d","e","f"}
local second={"a","b","c","d","e","f"}
setmetatable(first,{__pairs=function()error("__pairs invoked")end,
                    __index=function()error("__index invoked")end,
                    __newindex=function()error("__newindex invoked")end})
a:Shuffle(first);b:Shuffle(second)
local seen={}
for i=1,6 do assert(first[i]==second[i]);seen[first[i]]=true end
for _,value in {"a","b","c","d","e","f"} do assert(seen[value]) end
local copy=a:Clone()
local expected=Random.new(42)
for i=6,2,-1 do expected:NextInteger(1,i) end
assert(copy:NextNumber()==expected:NextNumber())
return first[1]~=nil,seen.a and seen.f,true
'''
        for host in HOSTS:
            with self.subTest(host=host.name):
                self.assertEqual(self.execute(host, source), [True, True, True])

    def test_empty_single_hash_only_and_readonly_tables(self):
        source = '''
local random=Random.new(71)
local probe=random:Clone()
random:Shuffle({})
local one={"only",answer=42}
random:Shuffle(one)
assert(one[1]=="only" and one.answer==42)
local hash={answer=42}
random:Shuffle(hash)
assert(hash.answer==42)
local frozen=table.freeze({1,2,3})
local frozenProbe=random:Clone()
assert(not pcall(function()random:Shuffle(frozen)end))
assert(random:NextNumber()==frozenProbe:NextNumber())
return true
'''
        for host in HOSTS:
            with self.subTest(host=host.name):
                self.assertEqual(self.execute(host, source), [True])

    def test_sparse_tables_and_bad_calls_fail_without_consuming_random_state(self):
        source = '''
local random=Random.new(991)
local probe=random:Clone()
local sparse={[1]="a",[3]="c"}
assert(not pcall(function()random:Shuffle(sparse)end))
assert(random:NextNumber()==probe:NextNumber())
assert(not pcall(function()random:Shuffle({1},"extra")end))
assert(not pcall(function()random:Shuffle("not a table")end))
assert(not pcall(function()Random.new(1):NextUnitVector(1)end))
assert(not pcall(function()Random.NextUnitVector({})end))
return true
'''
        for host in HOSTS:
            with self.subTest(host=host.name):
                self.assertEqual(self.execute(host, source), [True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
