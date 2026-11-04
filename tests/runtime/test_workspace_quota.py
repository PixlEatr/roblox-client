"""Bounded virtual workspace configuration and binary-safe readback tests."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import local_runtime
import persistent_runtime

if os.environ.get("LUAU_TEST_HOST"):
    local_runtime.RUST_HOST = Path(os.environ["LUAU_TEST_HOST"])
    if not local_runtime.RUST_HOST.is_file():
        raise RuntimeError("LUAU_TEST_HOST must point to a completed build")
    persistent_runtime.RUST_HOST = local_runtime.RUST_HOST

SNAPSHOT = json.loads((ROOT / "tests/fixtures/world_snapshot.json").read_text())


@unittest.skipUnless(local_runtime.RUST_HOST.is_file(), "build the persistent Luau host first")
class WorkspaceQuotaTests(unittest.TestCase):
    def setUp(self):
        self.host = persistent_runtime.PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self, source, timeout_ms=1000):
        result = self.host.call({"op": "execute", "source": source, "timeout_ms": timeout_ms})
        if result.get("completed"):
            return result
        job_id = result.get("job_id")
        deadline = time.monotonic() + 3
        while type(job_id) is int and time.monotonic() < deadline:
            poll = self.host.call({"op": "poll", "timeout_ms": 100})
            for completed in poll.get("completed_jobs", []):
                if completed.get("job_id") == job_id:
                    return completed
        return result

    def test_defaults_stay_small_and_failed_overwrite_is_atomic(self):
        result = self.execute('''
writefile("keep.bin", "old")
local ok = pcall(function() writefile("keep.bin", string.rep("x", 128*1024+1)) end)
return isfile("keep.bin"), readfile("keep.bin"), ok,
       (pcall(function() writefile("large.bin", string.rep("y", 128*1024+1)) end))
''')
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["values"], [True, "old", False, False])

    def test_opt_in_quota_and_chunk_read_round_trip_binary_data(self):
        result = self.execute('''
local fileLimit,totalLimit=setworkspacequota(2*1024*1024,4*1024*1024)
local payload=string.rep("A",524288)..string.char(0,255)..string.rep("Z",100000)
makefolder("exports")
writefile("exports/model.rbxlx",payload)
assert(getfileinfo("exports/model.rbxlx")==#payload)
local chunks={};local offset=0
while offset<#payload do
    local chunk=readfilechunk("exports/model.rbxlx",offset,65536)
    assert(#chunk>0 and #chunk<=65536)
    chunks[#chunks+1]=chunk;offset+=#chunk
end
local copied=table.concat(chunks)
local eof=readfilechunk("exports/model.rbxlx",#payload,65536)
local empty=readfilechunk("exports/model.rbxlx",0,0)
return fileLimit,totalLimit,#copied,copied==payload,
       string.byte(copied,524289),string.byte(copied,524290),eof,empty
''')
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["values"], [2*1024*1024, 4*1024*1024,
                                             624290, True, 0, 255, "", ""])

    def test_quota_bounds_and_shrinking_below_current_usage_leave_state_intact(self):
        result = self.execute('''
setworkspacequota(2*1024*1024,4*1024*1024)
writefile("a",string.rep("a",1258292))
local smallFile=pcall(function()setworkspacequota(1100000,4*1024*1024)end)
local smallTotal=pcall(function()setworkspacequota(2*1024*1024,1200000)end)
local tooBig=pcall(function()setworkspacequota(65*1024*1024,128*1024*1024)end)
local nonInteger=pcall(function()setworkspacequota(2*1024*1024+.5,4*1024*1024)end)
local invalidRead=pcall(function()readfilechunk("a",0,65537)end)
local pastEof=pcall(function()readfilechunk("a",getfileinfo("a")+1,1)end)
writefile("b",string.rep("b",1500000))
return smallFile,smallTotal,tooBig,nonInteger,invalidRead,pastEof,
       getfileinfo("a"),getfileinfo("b"),readfilechunk("a",0,3)
''')
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["values"], [False, False, False, False, False, False,
                                             1258292, 1500000, "aaa"])

    def test_chunk_arguments_are_strict_and_failed_reads_do_not_change_file(self):
        result = self.execute('''
writefile("value", "0123456789")
local negative=pcall(function()readfilechunk("value",-1,2)end)
local fractional=pcall(function()readfilechunk("value",1.5,2)end)
local coerced=pcall(function()readfilechunk("value","0",2)end)
local missing=pcall(function()readfilechunk("missing",0,1)end)
local after=readfilechunk("value",3,4)
return negative,fractional,coerced,missing,after,readfile("value")
''')
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["values"], [False, False, False, False, "3456", "0123456789"])


if __name__ == "__main__":
    unittest.main()
