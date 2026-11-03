"""Opiumware filesystem aliases over the bounded local workspace."""
import os
from pathlib import Path
import unittest
from unittest import mock

import test_cframe_transforms as transforms
import test_local_compat as compat
import persistent_runtime


class FilesystemCompatibilityTests(unittest.TestCase):
    run_script = compat.LocalCompatibilityTests.run_script

    def test_library_functions_are_the_global_functions(self):
        self.run_script('''
assert(runfile==dofile)
assert(type(fs)=="table" and type(filesystem)=="table")
local names={"dofile","loadfile","delfolder","delfile","isfile","isfolder",
             "makefolder","appendfile","listfiles","writefile","readfile"}
for _,name in names do
    assert(fs[name]==getgenv()[name],name)
    assert(filesystem[name]==getgenv()[name],name)
    assert(fs[name]==filesystem[name],name)
end
''')

    def test_aliases_preserve_binary_paths_and_quota_behavior(self):
        self.run_script('''
local payload=string.char(0,255).."hello"
fs.makefolder("aliases/nested")
filesystem.writefile("aliases/nested/data.bin",payload)
fs.appendfile("aliases/nested/data.bin",string.char(1,0))
assert(filesystem.readfile("aliases\\nested/./data.bin")==payload..string.char(1,0))
assert(fs.isfile("aliases/nested/data.bin") and fs.isfolder("aliases/nested"))
assert(not fs.isfile("aliases/nested") and not fs.isfolder("aliases/nested/data.bin"))
assert(#fs.listfiles("aliases")==1 and fs.listfiles("aliases")[1]=="aliases/nested")
assert(filesystem.listfiles("aliases/nested")[1]=="aliases/nested/data.bin")
for _,path in {"../escape","aliases/../../escape","/absolute","C:\\escape"} do
    assert(not pcall(fs.writefile,path,"no"))
end
local before=filesystem.readfile("aliases/nested/data.bin")
assert(not pcall(fs.writefile,"aliases/nested/data.bin",string.rep("x",128*1024+1)))
assert(filesystem.readfile("aliases/nested/data.bin")==before)
fs.delfile("aliases/nested/data.bin")
assert(not filesystem.isfile("aliases/nested/data.bin"))
filesystem.delfolder("aliases")
assert(not fs.isfolder("aliases/nested"))
''')

    def test_runfile_forwards_arguments_and_reports_compile_errors(self):
        self.run_script('''
fs.writefile("run.luau","local value,label=...; getgenv().runCount=(getgenv().runCount or 0)+value; return value,label")
local value,label=runfile("run.luau",7,"ok")
assert(value==7 and label=="ok" and getgenv().runCount==7)
value,label=filesystem.dofile("run.luau",5,"again")
assert(value==5 and label=="again" and getgenv().runCount==12)
fs.writefile("broken.luau","local =")
local fn,err=fs.loadfile("broken.luau")
assert(fn==nil and type(err)=="string")
assert(not pcall(runfile,"broken.luau"))
assert(not pcall(filesystem.runfile,"run.luau"))
''')

    def test_aliases_and_workspace_persist_within_the_vm(self):
        path = Path(os.environ.get('CFRAME_PERSISTENT_HOST', str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime, 'RUST_HOST', path):
            host = persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT)
        self.addCleanup(host.close)
        first = host.call({'op': 'execute', 'source': '''
fs.makefolder("persistent")
filesystem.writefile("persistent/value.bin",string.char(0,255).."saved")
fs.writefile("persistent/read.luau","return filesystem.readfile(...)")'''})
        self.assertTrue(first['ok'], first)
        second = host.call({'op': 'execute', 'source': '''
assert(fs.readfile("persistent/value.bin")==string.char(0,255).."saved")
assert(runfile("persistent/read.luau","persistent/value.bin")==string.char(0,255).."saved")
assert(fs.writefile==writefile and filesystem.dofile==dofile)'''})
        self.assertTrue(second['ok'], second)


if __name__ == '__main__':
    unittest.main()
