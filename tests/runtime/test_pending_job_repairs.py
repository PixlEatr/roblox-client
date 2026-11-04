"""Local reproduction of missing received properties and queued-job progress."""
import copy
import os
from pathlib import Path
import unittest
import time
from unittest import mock

import persistent_runtime
import test_cframe_transforms as transforms
import test_local_compat as compat


class PendingJobRepairsTests(unittest.TestCase):
    run_script = compat.LocalCompatibilityTests.run_script

    def test_core_gui_exists_before_get_service_and_owns_local_gui_children(self):
        self.run_script('''
local core=game.CoreGui
assert(core.ClassName=="CoreGui" and core.Parent==game)
assert(core==game:FindFirstChild("CoreGui"))
assert(core==game:FindService("CoreGui") and core==game:GetService("CoreGui"))
local screen=Instance.new("ScreenGui");screen.Name="AuthoredUI";screen.Parent=core
assert(core.AuthoredUI==screen and screen:IsDescendantOf(game))
screen:Destroy();assert(core:FindFirstChild("AuthoredUI")==nil)
''')

    def test_existing_core_gui_identity_is_preserved(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        game_id='captured-game'
        snapshot['nodes'].append({'id':game_id,'name':'Game','class_name':'DataModel',
                                 'parent':None,'properties':{}})
        snapshot['nodes'].append({'id':'captured-core','name':'CoreGui','class_name':'CoreGui',
                                 'parent':game_id,'properties':{'Archivable':False}})
        snapshot['services']['CoreGui']='captured-core'
        self.run_script('''
assert(game.CoreGui==game:GetService("CoreGui") and not game.CoreGui.Archivable)
local count=0
for _,child in game:GetChildren()do if child.ClassName=="CoreGui" then count+=1 end end
assert(count==1)
''',snapshot)

    def test_test_service_identity_and_message_output(self):
        results=self.run_script('''
local service=game:GetService("TestService")
assert(service==game:GetService("TestService") and service==game:FindService("TestService"))
assert(service.ClassName=="TestService" and service.Parent==game)
assert(not pcall(service.Message,workspace,"wrong receiver"))
assert(not pcall(service.Message,service,{}))
assert(not pcall(service.Message,service,"bad source",42))
service:Message("TEST_SERVICE_REAL_OUTPUT",nil,0)
assert(not pcall(function()return service.TestCount end))
''')
        for result in results:self.assertIn('TestService: TEST_SERVICE_REAL_OUTPUT',result['output'])

    def host(self, snapshot):
        binary = Path(os.environ.get('CFRAME_PERSISTENT_HOST', str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime, 'RUST_HOST', binary):
            host = persistent_runtime.PersistentLuauHost(snapshot)
        self.addCleanup(host.close)
        return host

    def test_known_missing_properties_accept_typed_local_writes(self):
        snapshot = copy.deepcopy(transforms.SNAPSHOT)
        snapshot['nodes'].append({'id':'emitter','class_name':'ParticleEmitter',
                                  'name':'Emitter','parent':'scope:root-1','properties':{}})
        self.run_script('''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local e=p.Emitter
assert(not pcall(function()return e.Enabled end))
assert(not pcall(function()e.Enabled="yes"end))
assert(not pcall(function()e.NotAProperty=1 end))
assert(not pcall(function()p.Mass=2 end))
local changes=0;e:GetPropertyChangedSignal("Enabled"):Connect(function()changes+=1 end)
e.Enabled=false;e.Rate=12;p.CanCollide=false
assert(e.Enabled==false and e.Rate==12 and p.CanCollide==false)
assert(client.propertyOrigin(e,"Enabled")=="local")
task.wait(.01);assert(changes==1)
''', snapshot)

    def test_received_color_alias_updates_and_overrides_local_color(self):
        snapshot = copy.deepcopy(transforms.SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id']=='scope:root-1')
        root['properties']['Color3uint8']={'$color3':[1,0,0]}
        host=self.host(snapshot)
        first=host.call({'op':'execute','source': '''
getgenv().colorPart=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(colorPart.Color==Color3.new(1,0,0))
assert(client.propertyOrigin(colorPart,"Color")=="derived_received")
colorPart.Color=Color3.new(0,0,1)
getgenv().colorChanges=0
colorPart:GetPropertyChangedSignal("Color"):Connect(function()colorChanges+=1 end)
return true
''','timeout_ms':100})
        self.assertTrue(first['ok'],first)
        sequence=int(snapshot.get('sequence',0))+1
        updated=host.call({'op':'update','timeout_ms':100,'delta':{
            'version':1,'delta':True,'sequence':sequence,'actions':[{
                'sequence':sequence,'kind':'set','id':root['id'],
                'fields':{'Color3uint8':{'$color3':[0,1,0]}}}]}})
        self.assertTrue(updated['ok'],updated)
        result=host.call({'op':'execute','source': '''
assert(colorPart.Color==Color3.new(0,1,0))
assert(colorChanges==1)
assert(client.propertyOrigin(colorPart,"Color")=="derived_received")
return true
''','timeout_ms':100})
        self.assertTrue(result['ok'],result)

    def test_trail_and_particle_color_sequences_are_typed_local_properties(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        for classname in ('Trail','ParticleEmitter'):
            snapshot['nodes'].append({'id':classname,'name':classname,'class_name':classname,
                                      'parent':'scope:root-1','properties':{}})
        self.run_script('''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
for _,name in {"Trail","ParticleEmitter"} do
    local effect=p[name]
    assert(not pcall(function()return effect.Color end))
    assert(not pcall(function()effect.Color=Color3.new(1,0,0) end))
    local changes=0
    effect:GetPropertyChangedSignal("Color"):Connect(function()changes+=1 end)
    effect.Color=ColorSequence.new(Color3.new(1,0,0),Color3.new(0,0,1))
    assert(typeof(effect.Color)=="ColorSequence")
    assert(effect.Color.Keypoints[1].Value==Color3.new(1,0,0))
    assert(effect.Color.Keypoints[2].Value==Color3.new(0,0,1))
    assert(client.propertyOrigin(effect,"Color")=="local")
    effect.Transparency=NumberSequence.new(.25,.75)
    assert(effect.Transparency.Keypoints[1].Value==.25)
    assert(not pcall(function()effect.Transparency=.5 end))
    if name=="Trail" then
        effect.WidthScale=NumberSequence.new(1)
        assert(effect.WidthScale.Keypoints[1].Value==1)
    else
        for _,key in {"Size","Squash"} do
            effect[key]=NumberSequence.new(2)
            assert(effect[key].Keypoints[1].Value==2)
        end
        for _,key in {"FlipbookFramerate","Lifetime","RotSpeed","Rotation","Speed"} do
            effect[key]=NumberRange.new(1,3)
            assert(effect[key].Min==1 and effect[key].Max==3)
            assert(not pcall(function()effect[key]=1 end))
        end
        effect.SpreadAngle=Vector2.new(15,30)
        assert(effect.SpreadAngle==Vector2.new(15,30))
    end
    task.wait(.01);assert(changes==1)
end
''',snapshot)

    def test_non_yielding_job_reports_measured_resume_progress(self):
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(copy.deepcopy(transforms.SNAPSHOT))
        first=host.call({'op':'execute','source':'local n=0;while true do n+=1 end','timeout_ms':5})
        self.assertEqual(first['state'],'running',first)
        a=next(d for d in first['job_diagnostics'] if d['job_id']==first['job_id'])
        second=host.call({'op':'poll','timeout_ms':10})
        b=next(d for d in second['job_diagnostics'] if d['job_id']==first['job_id'])
        self.assertGreater(b['resume_count'],a['resume_count'])
        self.assertGreater(b['execution_ms'],a['execution_ms'])
        self.assertGreater(b['slice_interruptions'],a['slice_interruptions'])
        self.assertEqual(b['yield_count'],0)

    def test_wait_separates_authored_poses_into_distinct_transactions(self):
        host=self.host(copy.deepcopy(transforms.SNAPSHOT))
        result=host.call({'op':'execute','timeout_ms':100,'source': '''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
for i=1,3 do
    p.CFrame=CFrame.new(i,10,0)
    task.wait(.001)
end
return true
'''})
        frames=[]
        for _ in range(8):
            poses=[m['value']['$cframe']['position'][0] for m in result.get('mutations',[])
                   if m.get('id')=='scope:root-1' and m.get('property')=='CFrame']
            self.assertLessEqual(len(poses),1,result)
            frames.extend(poses)
            if not result.get('active_jobs'):break
            time.sleep(.003)
            result=host.call({'op':'poll','timeout_ms':100})
        self.assertEqual(frames,[1,2,3])
        self.assertEqual(result.get('active_jobs'),0,result)

    def test_world_housekeeping_does_not_starve_a_ready_job(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        snapshot['nodes'] += [
            {'id':f'background-{i}','name':f'Background{i}','class_name':'LocalScript',
             'parent':'scope:character-1','properties':{}} for i in range(150)]
        snapshot['nodes'] += [
            {'id':f'folder-{i}','name':'Folder','class_name':'Folder',
             'parent':'scope:workspace','properties':{}} for i in range(2000)]
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(snapshot)
        first=host.call({'op':'execute','timeout_ms':1,
                         'source':'local n=0;for i=1,10000000 do n+=i end;return n'})
        job=first['job_id']
        before=next(d for d in first['job_diagnostics'] if d['job_id']==job)
        for _ in range(8):
            result=host.call({'op':'poll','timeout_ms':1})
            done=next((d for d in result['completed_jobs'] if d['job_id']==job),None)
            if done:
                self.assertTrue(done['ok'],done)
                return
        after=next(d for d in result['job_diagnostics'] if d['job_id']==job)
        self.assertGreater(after['resume_count'],before['resume_count'])
        self.assertGreater(after['execution_ms'],before['execution_ms'])

    def test_repeating_timer_and_expensive_world_pump_do_not_starve_ready_work(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        snapshot['nodes'] += [
            {'id':f'folder-{i}','name':'Folder','class_name':'Folder',
             'parent':'scope:workspace','properties':{}} for i in range(3000)]
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):host=self.host(snapshot)
        setup=host.call({'op':'execute','timeout_ms':1000,'source': '''
for i=1,50 do task.spawn(function()workspace:WaitForChild("absent"..i)end)end
task.spawn(function()while true do task.wait(.001)end end)
return true
'''})
        self.assertTrue(setup['ok'],setup)
        first=host.call({'op':'execute','timeout_ms':1,
                         'source':'local sum=0;for j=1,10000000 do sum+=j end;return sum'})
        job=first['job_id']
        before=next(d for d in first['job_diagnostics'] if d['job_id']==job)
        for _ in range(8):
            time.sleep(.003)
            result=host.call({'op':'poll','timeout_ms':1})
            done=next((d for d in result['completed_jobs'] if d['job_id']==job),None)
            if done:
                self.assertTrue(done['ok'],done);return
        after=next(d for d in result['job_diagnostics'] if d['job_id']==job)
        self.assertGreater(after['resume_count'],before['resume_count'])
        self.assertGreater(after['execution_ms'],before['execution_ms'])

    def test_sleeping_authored_job_gets_a_turn_among_cpu_bound_callbacks(self):
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(copy.deepcopy(transforms.SNAPSHOT))
        setup=host.call({'op':'execute','timeout_ms':20,'source': '''
for i=1,16 do task.defer(function()while true do end end)end
return true
'''})
        self.assertTrue(setup['ok'],setup)
        first=host.call({'op':'execute','timeout_ms':1,'source':'task.wait(.001);return 42'})
        job=first['job_id'];done=None
        for _ in range(30):
            time.sleep(.002)
            result=host.call({'op':'poll','timeout_ms':10})
            done=next((d for d in result['completed_jobs'] if d['job_id']==job),None)
            if done:break
        self.assertIsNotNone(done,'CPU-bound callbacks starved a ready authored job')
        self.assertTrue(done['ok'],done);self.assertEqual(done['values'],[42])

    def test_headless_heartbeat_waits_for_dispatch_capacity(self):
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(copy.deepcopy(transforms.SNAPSHOT))
        setup=host.call({'op':'execute','timeout_ms':100,'source': '''
local heartbeat=game:GetService("RunService").Heartbeat
for i=1,160 do heartbeat:Connect(function()while true do end end)end
return true
'''})
        self.assertTrue(setup['ok'],setup)
        for _ in range(15):
            time.sleep(.02)
            result=host.call({'op':'poll','timeout_ms':10})
            self.assertLessEqual(result.get('pending_event_kinds',{}).get('Heartbeat',0),160,
                                 'headless frames outpaced callback dispatch')

    def test_received_events_progress_with_full_runnable_callback_batch(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        snapshot['nodes'].append({'id':'received-event','name':'Reply',
            'class_name':'RemoteEvent','parent':'scope:workspace','properties':{}})
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(snapshot)
        setup=host.call({'op':'execute','timeout_ms':10,'source': '''
getgenv().received={}
getgenv().onceCount=0
workspace.Reply.OnClientEvent:Connect(function(value)
    table.insert(received,value)
end)
workspace.Reply.OnClientEvent:Once(function()onceCount+=1 end)
local cancelled=workspace.Reply.OnClientEvent:Connect(function()error("cancelled handler ran")end)
cancelled:Disconnect()
for i=1,128 do task.defer(function()while true do end end)end
return true
'''})
        self.assertTrue(setup['ok'],setup)
        sequence=int(snapshot.get('sequence',0))
        result=host.call({'op':'update','timeout_ms':10,'delta':{
            'version':1,'delta':True,'sequence':sequence+2,'actions':[
                {'sequence':sequence+i,'kind':'event','id':'received-event',
                 'name':'OnClientEvent','arguments':[i]} for i in (1,2)]}})
        for _ in range(50):
            result=host.call({'op':'poll','timeout_ms':10})
        self.assertEqual(result.get('pending_event_kinds',{}).get('OnClientEvent',0),0,
                         'received event admission starved behind CPU-bound callbacks')
        result=host.call({'op':'execute','timeout_ms':10,'source':
            'return #received,received[1],received[2],onceCount'})
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['values'],[2,1,2,1])

    def test_authored_progress_is_not_diluted_by_a_full_callback_batch(self):
        with mock.patch.dict(os.environ,{'RCR_LUAU_LARGE_TEST':'1'}):
            host=self.host(copy.deepcopy(transforms.SNAPSHOT))
        host.call({'op':'execute','timeout_ms':10,'source': '''
for i=1,128 do task.defer(function()while true do end end)end
return true
'''})
        first=host.call({'op':'execute','timeout_ms':1,'source':'local x=0;while true do x+=1 end'})
        job=first['job_id']
        before=next(d for d in first['job_diagnostics'] if d['job_id']==job)
        for _ in range(4):
            result=host.call({'op':'poll','timeout_ms':10})
        after=next(d for d in result['job_diagnostics'] if d['job_id']==job)
        self.assertGreater(after['resume_count'],before['resume_count'])
        self.assertGreater(after['execution_ms'],before['execution_ms'])


    def test_missing_color_is_not_fabricated_and_explicit_color_is_preserved(self):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in snapshot['nodes'] if n['id']=='scope:root-1')
        self.run_script('''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(not pcall(function()return p.Color end))
''',snapshot)
        root['properties']['Color3uint8']={'$color3':[1,0,0]}
        root['properties']['Color']={'$color3':[0,0,1]}
        self.run_script('''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
assert(p.Color==Color3.new(0,0,1))
''',snapshot)


if __name__=='__main__':unittest.main()
