"""Behavioral checks for Unveil-derived bindings and pending-job diagnostics."""
import io
import os
from pathlib import Path
import time
import unittest
from unittest import mock
import persistent_runtime
import terminal_client
import test_local_compat as compat
import test_cframe_transforms as transforms

class UnveilPortTests(unittest.TestCase):
    run_script=compat.LocalCompatibilityTests.run_script

    def host(self):
        binary=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        patch=mock.patch.object(persistent_runtime,'RUST_HOST',binary);patch.start();self.addCleanup(patch.stop)
        host=persistent_runtime.PersistentLuauHost(transforms.SNAPSHOT);self.addCleanup(host.close);return host

    def test_native_debug_info_traceback_and_registry_survive_extensions(self):
        self.run_script("""
assert(iscclosure(debug.info) and iscclosure(debug.traceback))
local function probe(a,b,...) return a,b end
local n,vararg=debug.info(probe,"a")
assert(n==2 and vararg==true and debug.info(probe,"f")==probe)
assert(debug.getinfo(probe).func==probe)
local co=coroutine.create(function() coroutine.yield();return 4 end)
assert(coroutine.resume(co))
assert(type(debug.info(co,1,"s"))=="string")
assert(debug.traceback(co,"trace marker"):find("trace marker",1,true))
assert(getreg==getregistry and getreg==debug.getregistry)
local reg=getreg();assert(reg==getreg() and type(reg._LOADED)=="table")
""")

    def test_legacy_aliases_share_state_methods_and_signals(self):
        self.run_script("""
local parent=Instance.new("Folder");parent.Name="AliasParent"
local child=Instance.new("Folder",parent);child.Name="Child"
assert(child.className==child.ClassName and child:isA("Instance"))
assert(parent:getChildren()[1]==child and parent:children()[1]==child)
assert(parent:findFirstChild("Child")==child and parent:waitForChild("Child")==child)
assert(child:isDescendantOf(parent) and child:getFullName()==child:GetFullName())
assert(parent.childAdded==parent.ChildAdded)
assert(child:GetPropertyChangedSignal("archivable")==child:GetPropertyChangedSignal("Archivable"))
child.archivable=false;assert(child.Archivable==false and child:clone()==nil)
assert(not pcall(function()child.className="Part"end))
child:destroy();assert(#parent:children()==0)
""")

    def test_actor_lookup_and_legacy_remove_preserve_objects_and_listeners(self):
        self.run_script("""
local outer=Instance.new("Actor");local inner=Instance.new("Actor",outer)
local folder=Instance.new("Folder",inner);local child=Instance.new("Folder",folder)
assert(child:GetActor()==inner and inner:GetActor()==inner and outer:GetActor()==outer)
child:SetAttribute("value",42)
local changed=0;local connection=child.Changed:Connect(function()changed+=1 end)
folder:remove();assert(folder.Parent==nil and child.Parent==nil)
assert(child:GetActor()==nil and child:GetAttribute("value")==42 and connection.Connected)
child.Parent=folder;folder.Parent=outer;child.Name="Reused"
task.wait(.01);assert(changed>0 and child:GetActor()==outer)
""")

    def test_wait_for_child_reports_actual_target_and_completes(self):
        host=self.host()
        r=host.call({'op':'execute','source':'return workspace:waitForChild("Arriving").Name','timeout_ms':50})
        self.assertEqual(r['state'],'running')
        d=next(d for d in r['job_diagnostics'] if d['job_id']==r['job_id'])
        self.assertEqual(d['scheduler_state'],'waiting');self.assertEqual(d['wait']['kind'],'child')
        self.assertEqual(d['wait']['name'],'Arriving');self.assertFalse(d['wait']['has_timeout'])
        self.assertTrue(any('persistent-source' in f['source'] for f in d['frames']))
        arrival=host.call({'op':'execute','source':'local p=Instance.new("Folder",workspace);p.Name="Arriving"','timeout_ms':100})
        completed=arrival.get('completed_jobs',[])
        for _ in range(10):
            if any(c['job_id']==r['job_id'] for c in completed):break
            completed+=host.call({'op':'poll','timeout_ms':20}).get('completed_jobs',[])
        self.assertEqual(next(c for c in completed if c['job_id']==r['job_id'])['values'],['Arriving'])

    def test_player_mouse_identity_and_distance_use_current_character(self):
        self.run_script('''
local player=game:GetService("Players").LocalPlayer
local mouse=player:GetMouse()
assert(mouse==player:GetMouse() and mouse.ClassName=="PlayerMouse" and mouse:IsA("Mouse"))
assert(not pcall(Instance.new,"PlayerMouse"))
local other=Instance.new("Folder");assert(not pcall(player.GetMouse,other))
local character=Instance.new("Model");local root=Instance.new("Part",character)
root.Name="HumanoidRootPart";root.Position=Vector3.new(1,2,3);player.Character=character
assert(player:DistanceFromCharacter(Vector3.new(4,6,3))==5)
root.Position=Vector3.new(4,6,3);assert(player:DistanceFromCharacter(root.Position)==0)
player.Character=nil;assert(player:DistanceFromCharacter(Vector3.zero)==0)
''')

    def test_signal_sleep_and_adapter_status_are_distinguished(self):
        host=self.host()
        first=host.call({'op':'execute','source':'getgenv().blocked=Instance.new("BindableEvent");return blocked.Event:Wait()','timeout_ms':10})
        second=host.call({'op':'execute','source':'task.wait(60)','timeout_ms':10})
        by_id={d['job_id']:d for d in second['job_diagnostics']}
        self.assertEqual(by_id[first['job_id']]['wait']['kind'],'signal')
        self.assertEqual(by_id[second['job_id']]['scheduler_state'],'sleeping')
        adapter=persistent_runtime.PersistentClientScriptRuntime(lambda:None,123)
        adapter._record(first);adapter._record(second)
        result=adapter.status(first['job_id'],advance=False)
        self.assertEqual(result['diagnostic'],by_id[first['job_id']])
        output=io.StringIO()
        with mock.patch.object(terminal_client,'send_request',return_value=result),mock.patch.object(terminal_client.sys,'stdout',output):
            terminal_client.show_job_status(first['job_id'])
        self.assertIn('Waiting for signal',output.getvalue())

    def test_closed_waiting_root_finishes_with_explicit_failure(self):
        host=self.host()
        first=host.call({'op':'execute','source':'getgenv().rootToClose=coroutine.running();Instance.new("BindableEvent").Event:Wait()','timeout_ms':10})
        self.assertEqual(first['state'],'running')
        result=host.call({'op':'execute','source':'assert(coroutine.close(rootToClose));return "closed"','timeout_ms':50})
        done=next((j for j in result['completed_jobs'] if j['job_id']==first['job_id']),None)
        self.assertIsNotNone(done,result)
        self.assertFalse(done['ok']);self.assertEqual(done['state'],'failed')
        self.assertIn('outside the scheduler',done['error'])
        self.assertEqual(result['active_jobs'],0)

if __name__=='__main__':unittest.main()
