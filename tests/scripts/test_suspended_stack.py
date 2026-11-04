"""Local coroutine stack reads must preserve suspension and original results."""
import copy
import unittest

from persistent_runtime import PersistentLuauHost
from test_attribute_read_diagnostics import SNAPSHOT


class SuspendedStackTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result)
        self.assertTrue(result.get('completed'), result)
        return result['values']

    def test_reads_suspended_locals_without_resuming_or_replacing_them(self):
        values = self.execute('''
local co=coroutine.create(function(number,text)
 coroutine.yield("parked")
 return number,text
end)
assert(coroutine.resume(co,37,"suspended-marker"))
local foundNumber,foundText=false,false
for level=0,5 do
 local ok,slots=pcall(debug.getstack,co,level)
 if ok then
  for index,value in slots do
   if value==37 then foundNumber=true;assert(debug.getstack(co,level,index)==37)end
   if value=="suspended-marker" then foundText=true end
  end
 end
end
assert(coroutine.status(co)=="suspended")
local ok,number,text=coroutine.resume(co)
return foundNumber,foundText,ok,number,text
''')
        self.assertEqual(values, [True, True, True, 37, 'suspended-marker'])

    def test_invalid_targets_levels_and_slots_do_not_damage_vm(self):
        values = self.execute('''
local fresh=coroutine.create(function()end)
assert(not pcall(debug.getstack,fresh,0))
assert(not pcall(debug.getstack,coroutine.running(),0))
assert(coroutine.resume(fresh))
assert(not pcall(debug.getstack,fresh,0))
local co=coroutine.create(function()local marker=17;coroutine.yield();return marker end)
assert(coroutine.resume(co))
assert(not pcall(debug.getstack,co,-1))
assert(not pcall(debug.getstack,co,100000))
for level=0,5 do
 if pcall(debug.getstack,co,level)then
  assert(not pcall(debug.getstack,co,level,0))
  assert(not pcall(debug.getstack,co,level,100000))
 end
end
return coroutine.resume(co)
''')
        self.assertEqual(values, [True, 17])

    def test_numeric_current_thread_form_remains_compatible(self):
        values = self.execute('''
local function read(marker)
 local slots=debug.getstack(1)
 local found=false
 for index,value in slots do
  if value==marker then found=true;assert(debug.getstack(1,index)==marker)end
 end
 return found,marker
end
return read("current-frame-marker")
''')
        self.assertEqual(values, [True, 'current-frame-marker'])


if __name__ == '__main__':
    unittest.main()
