"""BindableFunction communication through the real persistent native VM."""
import copy
import time
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class BindableFunctionTests(unittest.TestCase):
    def host(self, snapshot=None):
        host=PersistentLuauHost(copy.deepcopy(snapshot or SNAPSHOT))
        self.addCleanup(host.close)
        return host

    def call(self, host, source):
        result=host.call({'op':'execute','source':source,'timeout_ms':100})
        self.assertEqual(result.get('task_errors',[]),[])
        return result

    def completed(self, host, initial, *later):
        job=initial['job_id']
        for result in (initial,)+later:
            for done in result.get('completed_jobs',[]):
                if done['job_id']==job:return done
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=host.call({'op':'poll','timeout_ms':50})
            self.assertEqual(result.get('task_errors',[]),[])
            for done in result.get('completed_jobs',[]):
                if done['job_id']==job:return done
            time.sleep(.005)
        self.fail('BindableFunction caller did not finish')

    def run_source(self, host, source):
        initial=self.call(host,source)
        self.assertTrue(initial['ok'],initial.get('error'))
        result=self.completed(host,initial)
        self.assertTrue(result['ok'],result.get('error'))
        return result['values']

    def test_arguments_multiple_results_and_trailing_nils(self):
        values=self.run_source(self.host(),'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function(...)
 local args=table.pack(...)
 return args.n,args[1]+args[3],nil,args[4],nil
end
local result=table.pack(f:Invoke(10,nil,20,"done",nil))
return result.n,table.unpack(result,1,result.n)
''')
        self.assertEqual(values,[5,5,30,None,'done',None])

    def test_no_callback_waits_until_assigned_and_zero_returns_stay_zero(self):
        host=self.host()
        first=self.call(host,'f=Instance.new("BindableFunction");return select("#",f:Invoke(17,nil))')
        self.assertEqual(first['state'],'running')
        for _ in range(3):
            result=host.call({'op':'poll','timeout_ms':20})
            self.assertFalse(any(x['job_id']==first['job_id'] for x in result['completed_jobs']))
        second=self.call(host,'f.OnInvoke=function(...)assert(select("#",...)==2)end')
        self.assertEqual(self.completed(host,first,second)['values'],[0])

    def test_yielding_nested_invocations_return_to_the_correct_callers(self):
        values=self.run_source(self.host(),'''
local inner=Instance.new("BindableFunction")
local outer=Instance.new("BindableFunction")
inner.OnInvoke=function(n)task.wait(.02);return n*3,nil end
outer.OnInvoke=function(n)local a,b=inner:Invoke(n);return a+1,b end
local a=task.spawn(function()shared.left=outer:Invoke(2)end)
local b=task.spawn(function()shared.right=outer:Invoke(5)end)
while shared.left==nil or shared.right==nil do task.wait(.005)end
return shared.left,shared.right
''')
        self.assertEqual(values,[7,16])

    def test_callback_replacement_does_not_change_inflight_invocations(self):
        values=self.run_source(self.host(),'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function()task.wait(.02);return "old"end
task.spawn(function()shared.old=f:Invoke()end)
f.OnInvoke=function()return "new"end
local current=f:Invoke()
while shared.old==nil do task.wait(.005)end
return shared.old,current
''')
        self.assertEqual(values,['old','new'])

    def test_table_arguments_and_results_are_copied_but_instance_identity_survives(self):
        values=self.run_source(self.host(),'''
local f=Instance.new("BindableFunction")
local object=Instance.new("Folder")
local original=setmetatable({nested={value=3}}, {__index={secret=true}})
local returned={nested={value=8}}
f.OnInvoke=function(arg,instance,enum,callback)
 assert(arg~=original and arg.nested~=original.nested and getmetatable(arg)==nil)
 arg.nested.value=99
 assert(instance==object and enum==Enum.AnimationPriority.Core)
 return returned,instance,callback(4)
end
local result,instance,value=f:Invoke(original,object,Enum.AnimationPriority.Core,function(x)return x+1 end)
result.nested.value=20
return original.nested.value,returned.nested.value,result~=returned,instance==object,value
''')
        self.assertEqual(values,[3,8,True,True,5])

    def test_callback_error_is_catchable_without_duplicate_background_error(self):
        host=self.host()
        values=self.run_source(host,'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function()task.wait(.01);error("expected callback failure")end
local ok,message=pcall(function()return f:Invoke()end)
return ok,string.find(message,"expected callback failure",1,true)~=nil
''')
        self.assertEqual(values,[False,True])
        self.assertEqual(host.call({'op':'poll','timeout_ms':50})['task_errors'],[])

    def test_cyclic_arguments_and_return_values_are_rejected(self):
        values=self.run_source(self.host(),'''
local f=Instance.new("BindableFunction")
local cycle={};cycle.self=cycle
f.OnInvoke=function()return cycle end
local argumentOk=pcall(function()f:Invoke(cycle)end)
local returnOk=pcall(function()f:Invoke()end)
return argumentOk,returnOk
''')
        self.assertEqual(values,[False,False])

    def test_nil_clears_callback_and_members_are_validated(self):
        host=self.host()
        values=self.run_source(host,'''
f=Instance.new("BindableFunction")
f.OnInvoke=function()return true end
f.OnInvoke=nil
return pcall(function()local x=f.OnInvoke end),
       pcall(function()f.OnInvoke=42 end),
       pcall(function()f.Invoke(workspace)end)
''')
        self.assertEqual(values[:3],[False,False,False])
        pending=self.call(host,'return f:Invoke()')
        self.assertEqual(pending['state'],'running')
        second=self.call(host,'f.OnInvoke=function()return 123 end')
        self.assertEqual(self.completed(host,pending,second)['values'],[123])

    def test_destroy_releases_waiting_caller_with_an_error(self):
        host=self.host()
        first=self.call(host,'f=Instance.new("BindableFunction");return pcall(function()f:Invoke()end)')
        second=self.call(host,'f:Destroy()')
        values=self.completed(host,first,second)['values']
        self.assertFalse(values[0]);self.assertIn('destroyed',values[1])

    def test_cancelled_caller_does_not_leak_retained_callback_tasks(self):
        host=self.host()
        values=self.run_source(host,'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function()task.wait(.01);return 1 end
for _=1,100 do
 local thread=task.spawn(function()f:Invoke()end)
 task.cancel(thread)
end
task.wait(.03)
return true
''')
        self.assertEqual(values,[True])
        result=host.call({'op':'poll','timeout_ms':100})
        self.assertEqual(result['pending_tasks'],0)

    def test_completed_callbacks_are_released_beyond_scheduler_capacity(self):
        host=self.host()
        values=self.run_source(host,'''
local f=Instance.new("BindableFunction")
f.OnInvoke=function(n)return n+1 end
local result=0
for _=1,2100 do result=f:Invoke(result)end
return result
''')
        self.assertEqual(values,[2100])
        self.assertEqual(host.call({'op':'poll','timeout_ms':50})['pending_tasks'],0)

    def test_disabled_callback_owner_cancels_inflight_call_and_clears_binding(self):
        from test_script_execution import snapshot,add_script
        data=snapshot()
        add_script(data,'Provider','''
shared.endpoint=Instance.new("BindableFunction")
shared.endpoint.OnInvoke=function()task.wait(10);return 1 end
''',kind='LocalScript',parent='gui')
        host=self.host(data)
        first=self.call(host,'return pcall(function()shared.endpoint:Invoke()end)')
        self.assertEqual(first['state'],'running')
        second=self.call(host,'game:GetService("Players").LocalPlayer.PlayerGui.Provider.Disabled=true')
        self.assertFalse(self.completed(host,first,second)['values'][0])
        third=self.call(host,'return shared.endpoint:Invoke()')
        self.assertEqual(third['state'],'running')
        fourth=self.call(host,'shared.endpoint.OnInvoke=function()return 77 end')
        self.assertEqual(self.completed(host,third,fourth)['values'],[77])


if __name__=='__main__':unittest.main()
