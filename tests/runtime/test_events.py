"""Source scheduling and lifecycle conformance against independent Studio fixtures."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from local_runtime import RUST_HOST, run_source
from persistent_runtime import PersistentLuauHost
from script_runtime import ClientScriptRuntime
from test_world_snapshot import fixture

SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust source host first')
class EventsAndLifecycleTests(unittest.TestCase):
    def run_luau(self, source, timeout=1000):
        return run_source(source, world_snapshot=SNAPSHOT, timeout_ms=timeout, memory_mb=64)

    def test_independent_studio_lifecycle_reference(self):
        script = ROOT / 'tests/fixtures/instance_lifecycle.luau'
        reference = json.loads(script.with_name('instance_lifecycle_reference.json').read_text())
        result = self.run_luau(script.read_text())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [reference['expected']])

    def test_independent_studio_event_and_task_reference(self):
        script = ROOT / 'tests/fixtures/instance_events.luau'
        reference = json.loads(script.with_name('instance_events_reference.json').read_text())
        result = self.run_luau(script.read_text())
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [reference['expected']])

    def test_instance_handle_targets_and_clone_remapping(self):
        result = self.run_luau('''
local f=Instance.new("Folder")
local p=Instance.new("Part",f)
f:SetAttribute("Self",f)
f:SetAttribute("Child",InstanceHandle.new(p))
local c=f:Clone()
local original=f:GetAttribute("Self")
assert(typeof(original)=="InstanceHandle")
assert(original:Get()==f and original:Wait(.001)==f)
assert(c:GetAttribute("Self"):Get()==c)
assert(c:GetAttribute("Child"):Get()==c:GetChildren()[1])
assert(InstanceHandle.new(f)==InstanceHandle.new(f))
assert(InstanceHandle.new():Get()==nil)
assert(InstanceHandle.new():Wait(.001)==nil)
assert(not pcall(function()original:Wait(0)end))
f:Destroy()
return original:Get()==f,original:Get().Parent==nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True])

    def test_callback_errors_are_isolated_and_other_callbacks_continue(self):
        result = self.run_luau('''
local f=Instance.new("Folder")
local state={other=false}
f.ChildAdded:Connect(function(child)
 child.Name="changed before callback error"
 error("background fixture failure")
end)
f.ChildAdded:Connect(function()state.other=true end)
Instance.new("Folder",f)
task.wait(.005)
state.changed=f:GetChildren()[1].Name=="changed before callback error"
return state
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [{'other': True, 'changed': True}])
        self.assertEqual(len(result['task_errors']), 1)
        self.assertIn('background fixture failure', result['task_errors'][0])

    def test_disconnect_cancels_callback_already_scheduled_in_same_event(self):
        result = self.run_luau('''
local f=Instance.new("Folder")
local second
local calls=0
f.ChildAdded:Connect(function()second:Disconnect()end)
second=f.ChildAdded:Connect(function()calls+=1 end)
Instance.new("Folder",f)
task.wait(.005)
return calls,second.Connected
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [0, False])

    def test_tasks_drain_after_main_returns_and_keep_returned_table_alive(self):
        result = self.run_luau('''
local state={done=false}
task.defer(function()task.wait(.002);state.done=true end)
return state
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [{'done': True}])

    def test_bindable_event_copies_tables_preserves_functions_and_trailing_nils(self):
        result = self.run_luau('''
local event=Instance.new("BindableEvent")
local original={inner={value=1}}
local fn=function()return 17 end
local state={called=false}
event.Event:Once(function(...)
 local value,empty,callback,last=...
 assert(select("#",...)==4)
 assert(value~=original and value.inner~=original.inner and value.inner.value==1 and empty==nil and last==nil)
 assert(callback==fn and callback()==17)
 state.called=true
end)
event:Fire(original,nil,fn,nil)
original.inner.value=2
task.wait(.005)
task.delay(.002,function()event:Fire(original,nil,fn,nil)end)
local value,empty,callback,last=event.Event:Wait()
local cyclic={};cyclic.self=cyclic
local cycleAccepted=pcall(function()event:Fire(cyclic)end)
return state.called,value~=original and value.inner.value==2,empty==nil,callback==fn,last==nil,cycleAccepted
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True, True, True, False])

    def test_signal_wait_preserves_instance_and_nil_arguments(self):
        result = self.run_luau('''
local parent=Instance.new("Folder")
local child=Instance.new("Folder",parent)
task.delay(.002,function()child:Destroy()end)
local actual,newParent=child.AncestryChanged:Wait()
return actual==child,newParent==nil,child.Parent==nil
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True])

    def test_bindable_variant_copy_matches_measured_table_and_buffer_rules(self):
        result = self.run_luau('''
local event=Instance.new("BindableEvent")
local input=buffer.create(1);buffer.writeu8(input,0,1)
local out={}
event.Event:Once(function(b,array,dict,proxy,thread)
 out.bufferCopied=b~=input and buffer.readu8(b,0)==1
 out.mixedArray=array[1]=="one" and array.other==nil and array[3]==nil
 out.numericDictionary=dict["2"]=="two" and dict[2.5]==nil
 out.unsupportedBecameNil=proxy==nil and thread==nil
end)
event:Fire(input,{[1]="one",[3]="three",other="discarded"},{[2.5]="two"},newproxy(true),coroutine.create(function()end))
buffer.writeu8(input,0,2)
task.wait(.005)
local invalidKey=pcall(function()event:Fire({[true]=1})end)
return out.bufferCopied,out.mixedArray,out.numericDictionary,out.unsupportedBecameNil,invalidKey
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True, True, True, True, False])

    def test_cancelled_waiter_never_resumes(self):
        result = self.run_luau('''
local f=Instance.new("Folder")
local resumed=false
local t=task.spawn(function()f.ChildAdded:Wait();resumed=true end)
task.cancel(t)
Instance.new("Folder",f)
task.wait(.005)
return resumed
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [False])

    def test_manual_resume_matches_measured_still_armed_signal_waiter(self):
        # Studio 0.745 keeps the first signal waiter armed after task.spawn
        # manually resumes its coroutine. Its later fire resumes the thread's
        # second wait with the old signal's arguments. Preserve this observed
        # engine behavior rather than assuming wait registrations are revoked.
        result = self.run_luau('''
local a=Instance.new("BindableEvent")
local b=Instance.new("BindableEvent")
local out={}
local t=task.spawn(function()
 out.first=a.Event:Wait()
 out.second=b.Event:Wait()
 out.done=true
end)
task.spawn(t,"manual")
a:Fire("stale")
task.wait(.005)
out.finishedBeforeSecond=out.done==true
b:Fire("expected")
return out.first,out.second,out.done,out.finishedBeforeSecond
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], ['manual', 'stale', True, True])

    def test_background_infinite_loop_and_unresolved_wait_are_bounded(self):
        for source in [
            'task.defer(function()pcall(function()while true do end end)end);return true',
            'Instance.new("Folder").ChildAdded:Wait()',
        ]:
            result = self.run_luau(source, timeout=25)
            self.assertFalse(result['ok'], result)
            self.assertTrue(result['timed_out'], result)

    def test_valuebase_changed_passes_value_and_ignores_name(self):
        result = self.run_luau('''
local v=Instance.new("StringValue")
local values={}
local names=0
v.Changed:Connect(function(value)table.insert(values,value)end)
v:GetPropertyChangedSignal("Name"):Connect(function()names+=1 end)
v.Name="Other";v.Value="changed"
task.wait(.005)
return #values,values[1],names
''')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [1, 'changed', 1])

    def test_destroyed_references_and_cloned_attributes_persist_across_commands(self):
        world, _ = fixture()
        count = len(world.instances)
        runtime = ClientScriptRuntime(lambda: world, 123)
        first = runtime.execute('''
local f=Instance.new("Folder",workspace);f.Name="Lifecycle"
local p=Instance.new("Part",f);p.Name="Original"
p:SetAttribute("State","old")
local ref=Instance.new("ObjectValue",f);ref.Name="Reference";ref.Value=p
local copy=p:Clone();copy.Name="Copied";copy.Parent=f
copy:SetAttribute("State","new")
p:Destroy()
return p.Parent==nil,ref.Value==p
''')
        self.assertTrue(first['ok'], first)
        second = runtime.execute('''
local f=workspace:FindFirstChild("Lifecycle")
local p=f:FindFirstChild("Reference").Value
local copy=f:FindFirstChild("Copied")
local reparent=pcall(function()p.Parent=f end)
assert(not reparent)
assert(p:GetAttribute("State")=="old" and copy:GetAttribute("State")=="new")
f:ClearAllChildren()
local child=Instance.new("Folder",f)
return p.Parent==nil,#f:GetChildren(),child.Parent==f
''')
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [True, 1, True])
        self.assertEqual(len(world.instances), count)

    def test_partial_received_attributes_remain_unknown_after_local_write(self):
        world, key = fixture()
        world.creation[key]['complete'] = False
        runtime = ClientScriptRuntime(lambda: world, 123)
        first = runtime.execute('''
local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
p:SetAttribute("Known",7)
return p:GetAttribute("Known")
''')
        self.assertTrue(first['ok'], first)
        second = runtime.execute('''
local p=game:GetService("Players").LocalPlayer.Character:FindFirstChild("HumanoidRootPart")
local all=pcall(function()p:GetAttributes()end)
local missing=pcall(function()p:GetAttribute("Unknown")end)
return p:GetAttribute("Known"),all,missing
''')
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [7, False, False])

    def test_unresolved_instance_handle_wait_resumes_after_world_update(self):
        import time
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(node for node in snapshot['nodes'] if node['name'] == 'HumanoidRootPart')
        root['attributes'] = {
            'Future': {'$instance_handle': 'future:1'},
            'Missing': {'$instance_handle': 'future:missing'},
        }
        root['attributes_known'] = True
        host = PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        first = host.call({'op': 'execute', 'source': '''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
getgenv().root=root
getgenv().future=root:GetAttribute("Future")
getgenv().missing=root:GetAttribute("Missing")
getgenv().resolved="pending"
getgenv().expired="pending"
task.spawn(function()resolved=future:Wait(1)end)
task.spawn(function()expired=missing:Wait(.001)end)
return future:Get()==nil,missing:Get()==nil,resolved
'''})
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['values'], [True, True, 'pending'])
        sequence = snapshot['sequence'] + 1
        update = host.call({'op': 'update', 'timeout_ms': 20, 'delta': {
            'version': 1, 'delta': True, 'sequence': sequence,
            'actions': [{'sequence': sequence, 'kind': 'create', 'id': 'future:1', 'node': {
                'id': 'future:1', 'class_name': 'Folder', 'name': 'Resolved',
                'parent': root['id'], 'properties': {}, 'origins': {},
            }}],
        }})
        self.assertTrue(update['ok'], update)
        self.assertTrue(host.call({'op': 'poll', 'timeout_ms': 20})['ok'])
        second = host.call({'op': 'execute', 'source':
            'return resolved.Name,resolved.Parent==root,expired'})
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'][:2], ['Resolved', True])
        # The missing handle has a 1 ms timeout. IPC and scheduling may already
        # have consumed it; either state is valid before the explicit sleep.
        self.assertIn(second['values'][2], ('pending', None))
        time.sleep(.003)
        self.assertTrue(host.call({'op': 'poll', 'timeout_ms': 20})['ok'])
        third = host.call({'op': 'execute', 'source': 'return expired'})
        self.assertTrue(third['ok'], third)
        self.assertEqual(third['values'], [None])


if __name__ == '__main__':
    unittest.main()
