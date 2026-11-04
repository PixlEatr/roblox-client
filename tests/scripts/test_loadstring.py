"""Source-loader behavior, independent of a test suite's bytecode-version gate."""
import unittest

import test_script_execution as script_tests


class LoadstringTests(unittest.TestCase):
    host = script_tests.ScriptExecutionTests.host
    execute = script_tests.ScriptExecutionTests.execute
    completed = script_tests.ScriptExecutionTests.completed

    def test_chunk_names_default_to_source_and_preserve_explicit_names(self):
        result = self.execute(self.host(), '''
local source = "return 42"
for _,fn in {assert(loadstring(source)), assert(loadstring(source,nil))} do
    assert(debug.getinfo(fn).source==source, "default chunk name must be the source")
    assert(debug.info(fn,"s"):find(source,1,true))
    assert(fn()==42)
end
assert(debug.info(assert(loadstring(source,"=named chunk")),"s")=="named chunk")
assert(debug.info(assert(loadstring(source,"@named.lua")),"s")=="named.lua")
local fn,err=loadstring("local =")
assert(fn==nil and type(err)=="string" and err:find("local =",1,true))
local named,namedError=loadstring("local =","=broken chunk")
assert(named==nil and namedError:find("broken chunk",1,true))
return true
''')
        self.assertEqual(result['values'], [True])

    def test_real_bytecode_is_rejected_without_rejecting_source(self):
        result = self.execute(self.host(), '''
local module=Instance.new("ModuleScript")
module.Source="return 42"
local bytecode=getscriptbytecode(module)
assert(type(bytecode)=="string" and #bytecode>0)
local binary,err=loadstring(bytecode,"=compiled input")
assert(binary==nil and type(err)=="string")
local source=assert(loadstring(module.Source))
assert(source()==42)
assert(assert(loadstring("return ... + 1"))(1)==2)
local bad,message=loadstring("f")
assert(bad==nil and type(message)=="string")
module:Destroy()
return true
''')
        self.assertEqual(result['values'], [True])
