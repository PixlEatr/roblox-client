"""Script provenance comes from real execution ownership, not function globals."""
import unittest

import test_script_execution as script_tests
from test_script_execution import snapshot, add_script


class CallingScriptTests(unittest.TestCase):
    host=script_tests.ScriptExecutionTests.host
    execute=script_tests.ScriptExecutionTests.execute
    completed=script_tests.ScriptExecutionTests.completed

    def run_source(self, host, source):
        result=self.completed(host,self.execute(host,source,timeout_ms=100))
        self.assertTrue(result['ok'],result)
        return result

    def test_executor_has_no_script_even_if_environment_claims_one(self):
        result=self.run_source(self.host(),'''
assert(getcallingscript==getcaller and getcallingscript()==nil and checkcaller())
script=Instance.new("LocalScript")
assert(getcallingscript()==nil)
local fn=loadstring("return getcallingscript(),checkcaller()")
setfenv(fn,setmetatable({script=script},{__index=getfenv()}))
local value,ours=fn();assert(value==nil and ours)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_real_script_owner_survives_hook_wrappers_and_suspension(self):
        data=snapshot()
        add_script(data,'Actor','''
shared.started=getcallingscript()==script and not checkcaller()
task.wait(.03)
shared.nested=newcclosure(function()return getcallingscript()==script end)()
local probe=game.Name
shared.hook=function()return game.Name end
shared.ready=true
''',kind='LocalScript',parent='gui')
        host=self.host(data)
        result=self.run_source(host,'''
local old;local observed
old=hookmetamethod(game,"__index",function(self,key)
 local caller=getcallingscript()
 if caller then observed=tostring(caller) end
 return old(self,key)
end)
for _=1,100 do
 if shared.ready then break end
 task.wait(.005)
end
-- Calling a script's closure on the executor thread keeps this thread's owner.
shared.hook();assert(getcallingscript()==nil)
hookmetamethod(game,"__index",old)
return shared.started,shared.nested,observed
''')
        self.assertEqual(result['values'],[True,True,'Actor'])

    def test_module_and_created_coroutines_retain_creation_owner_after_require_returns(self):
        data=snapshot();add_script(data,'Leaf','''
local actual=script
return {initial=getcallingscript(),
 co=coroutine.create(function()task.wait(.01);shared.coroutineOwner=getcallingscript();shared.coroutineOurs=checkcaller() end),
 scheduled=coroutine.create(function()shared.scheduledOwner=getcallingscript() end),
 wrap=coroutine.wrap(function()return getcallingscript(),checkcaller() end),
 fn=function()return getcallingscript(),checkcaller() end}
''')
        add_script(data,'RootModule','local leaf=require(script.Parent.Leaf);return {self=getcallingscript(),leaf=leaf}')
        result=self.run_source(self.host(data),'''
local m=require(game:GetService("ReplicatedStorage").RootModule)
assert(m.self.Name=="RootModule" and m.leaf.initial.Name=="Leaf")
local origin,ours=m.leaf.wrap();assert(origin==m.leaf.initial and not ours)
local noScript,isExecutor=m.leaf.fn();assert(noScript==nil and isExecutor)
task.spawn(m.leaf.scheduled)
local done
task.spawn(function()
 local ok=coroutine.resume(m.leaf.co);assert(ok)
 while coroutine.status(m.leaf.co)~="dead" do task.wait(.005) end
 done=true
end)
for _=1,100 do if done then break end;task.wait(.005) end
assert(done)
assert(shared.coroutineOwner==m.leaf.initial and not shared.coroutineOurs)
assert(shared.scheduledOwner==m.leaf.initial)
return true
''')
        self.assertEqual(result['values'],[True])

    def test_signal_and_deferred_callbacks_use_registering_script_owner(self):
        data=snapshot();add_script(data,'Listeners','''
shared.event=Instance.new("BindableEvent")
shared.event.Event:Connect(function()
 shared.signalOwner=getcallingscript()
 task.defer(function()task.wait(.01);shared.deferredOwner=getcallingscript() end)
end)
return script
''')
        result=self.run_source(self.host(data),'''
local origin=require(game:GetService("ReplicatedStorage").Listeners)
shared.event:Fire()
for _=1,100 do if shared.deferredOwner then break end;task.wait(.005) end
return shared.signalOwner==origin,shared.deferredOwner==origin,getcallingscript()==nil
''')
        self.assertEqual(result['values'],[True,True,True])


if __name__=='__main__':unittest.main()
