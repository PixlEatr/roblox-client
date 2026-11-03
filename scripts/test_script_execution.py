"""Actual persistent Luau module loading and automatic script lifecycle tests."""
import copy
import json
from pathlib import Path
import time
import unittest

from local_runtime import RUST_HOST
from persistent_runtime import PersistentLuauHost, PersistentClientScriptRuntime
from test_world_snapshot import fixture

ROOT=Path(__file__).resolve().parents[1]


def snapshot():
    result=json.loads((ROOT/'runtime/tests/world_snapshot.json').read_text())
    result['nodes'].append({'id':'game','class_name':'DataModel','name':'Game','parent':None,'properties':{}})
    for name in ('ReplicatedStorage','ReplicatedFirst'):
        result['nodes'].append({'id':name,'class_name':name,'name':name,'parent':None,'properties':{}})
        result['services'][name]=name
    result['nodes'].append({'id':'gui','class_name':'PlayerGui','name':'PlayerGui','parent':result['local_player'],'properties':{}})
    result['script_sources']={}
    return result


def add_script(data,name,source,*,kind='ModuleScript',parent='ReplicatedStorage',disabled=False):
    properties={'Archivable':True}
    if kind!='ModuleScript':properties.update({'Disabled':disabled,'Enabled':not disabled})
    data['nodes'].append({'id':name,'class_name':kind,'name':name,'parent':parent,'properties':properties})
    if source is not None:data['script_sources'][name]={'source':source}


@unittest.skipUnless(RUST_HOST.is_file(),'build the Rust host')
class ScriptExecutionTests(unittest.TestCase):
    def host(self,data=None):
        host=PersistentLuauHost(data or snapshot());self.addCleanup(host.close);return host

    def execute(self,host,source,**kwargs):
        result=host.call({'op':'execute','source':source,**kwargs})
        self.assertTrue(result['ok'],result)
        return result

    def completed(self,host,result,*,timeout=10):
        """Poll a yielded command to completion with a load-tolerant bound."""
        if result.get('state')!='running':
            return result
        deadline=time.monotonic()+timeout
        while result.get('state')=='running' and time.monotonic()<deadline:
            batch=host.call({'op':'poll'})
            for item in batch.get('completed_jobs',[]):
                if item['job_id']==result['job_id']:return item
            time.sleep(.005)
        self.fail(f"job {result.get('job_id')} did not complete within {timeout}s")

    def test_require_caches_identity_across_commands_and_isolates_globals(self):
        data=snapshot();add_script(data,'Config','''
shared.runs=(shared.runs or 0)+1
privateGlobal=42
return {name=script.Name, answer=42}
''')
        host=self.host(data)
        first=self.execute(host,'getgenv().first=require(game:GetService("ReplicatedStorage").Config);first.answer=99;return first.name,privateGlobal==nil,shared.runs')
        self.assertEqual(first['values'],['Config',True,1])
        second=self.execute(host,'local again=require(game:GetService("ReplicatedStorage").Config);return again==first,again.answer,shared.runs')
        self.assertEqual(second['values'],[True,99,1])

    def test_nested_requires_and_module_script_identity(self):
        data=snapshot();add_script(data,'Leaf','return {value=17, name=script.Name}')
        add_script(data,'Main','local leaf=require(script.Parent.Leaf);return {value=leaf.value+1,name=script.Name,leaf=leaf.name}')
        result=self.execute(self.host(data),'local m=require(game:GetService("ReplicatedStorage").Main);return m.value,m.name,m.leaf')
        self.assertEqual(result['values'],[18,'Main','Leaf'])

    def test_yielding_module_is_shared_by_concurrent_callers(self):
        data=snapshot();add_script(data,'Slow','shared.loads=(shared.loads or 0)+1;task.wait(.02);return {value=42}')
        host=self.host(data)
        result=self.execute(host,'''local module=game:GetService("ReplicatedStorage").Slow
task.spawn(function()shared.first=require(module)end)
task.spawn(function()shared.second=require(module)end)
for _=1,200 do
    if shared.first~=nil and shared.second~=nil then break end
    task.wait(.01)
end
assert(shared.first~=nil and shared.second~=nil,"module callers did not complete")
return shared.first==shared.second,shared.first.value,shared.loads''')
        result=self.completed(host,result)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['values'],[True,42,1])

    def test_errors_are_cached_and_bad_return_counts_rejected(self):
        data=snapshot();add_script(data,'Broken','shared.errors=(shared.errors or 0)+1;error("module failed")')
        add_script(data,'NoReturn','local x=1');add_script(data,'TooMany','return 1,2')
        host=self.host(data)
        result=self.execute(host,'''local r=game:GetService("ReplicatedStorage")
local a,e=pcall(require,r.Broken);local b,f=pcall(require,r.Broken)
return a,b,e==f,shared.errors,pcall(require,r.NoReturn),pcall(require,r.TooMany)''')
        self.assertEqual(result['values'][:5],[False,False,True,1,False])
        self.assertFalse(result['values'][5])

    def test_false_and_nil_are_single_cached_return_values(self):
        data=snapshot();add_script(data,'FalseValue','shared.falseRuns=(shared.falseRuns or 0)+1;return false')
        add_script(data,'NilValue','return nil')
        result=self.execute(self.host(data),'local r=game:GetService("ReplicatedStorage");return require(r.FalseValue),require(r.FalseValue),shared.falseRuns,require(r.NilValue)')
        self.assertEqual(result['values'],[False,False,1,None])

    def test_local_script_automatically_runs_in_player_gui_only_once(self):
        data=snapshot();add_script(data,'Auto','shared.started=(shared.started or 0)+1;shared.module=require(game:GetService("ReplicatedStorage").Config).value;shared.scriptName=script.Name',kind='LocalScript',parent='gui')
        add_script(data,'Config','return {value=42}')
        host=self.host(data)
        # Startup runs on the background scheduler and may cross an init slice
        # while requiring the module. Assert its result after it is ready.
        result=self.execute(host,'''for _=1,200 do
    if shared.module~=nil and shared.scriptName~=nil then break end
    task.wait(.005)
end
return shared.started,shared.module,shared.scriptName''')
        result=self.completed(host,result)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['values'],[1,42,'Auto'])
        for _ in range(3):host.call({'op':'poll'})
        self.assertEqual(self.execute(host,'return shared.started')['values'],[1])

    def test_server_scripts_and_locals_in_storage_do_not_run(self):
        data=snapshot();add_script(data,'Server','shared.wrong=true',kind='Script',parent='scope:workspace')
        add_script(data,'Stored','shared.wrong=true',kind='LocalScript')
        add_script(data,'First','shared.first=true',kind='LocalScript',parent='ReplicatedFirst')
        result=self.execute(self.host(data),'return shared.wrong==nil,shared.first')
        self.assertEqual(result['values'],[True,True])

    def test_disabled_script_restarts_and_cancels_children_and_connections(self):
        data=snapshot();add_script(data,'Lifecycle','''
shared.started=(shared.started or 0)+1
workspace.ChildAdded:Connect(function()shared.events=(shared.events or 0)+1 end)
shared.gate=Instance.new("BindableEvent")
task.spawn(function()shared.gate.Event:Wait();shared.delayed=true end)
task.wait(10)
shared.afterWait=true
''',kind='LocalScript',parent='gui')
        host=self.host(data)
        self.execute(host,'game:GetService("Players").LocalPlayer.PlayerGui.Lifecycle.Disabled=true')
        self.execute(host,'shared.gate:Fire();local f=Instance.new("Folder",workspace);f:Destroy();task.wait(.01)')
        for _ in range(15):host.call({'op':'poll'});time.sleep(.005)
        result=self.execute(host,'return shared.started,shared.delayed==nil,shared.events==nil,shared.afterWait==nil')
        self.assertEqual(result['values'],[1,True,True,True])
        self.execute(host,'local s=game:GetService("Players").LocalPlayer.PlayerGui.Lifecycle;s.Enabled=true;assert(not s.Disabled)')
        self.assertEqual(self.execute(host,'return shared.started')['values'],[2])

    def test_missing_code_is_visible_and_can_arrive_later(self):
        data=snapshot();add_script(data,'Late',None,kind='LocalScript',parent='gui')
        host=self.host(data)
        result=self.execute(host,'return client.scriptStatus().blocked')
        self.assertEqual(result['values'],[1])
        response=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':1,'actions':[],
                         'script_sources':{'Late':{'source':'shared.lateLoaded=true'}}}})
        self.assertTrue(response['ok'],response)
        loaded=self.completed(host,self.execute(host,'while shared.lateLoaded==nil do task.wait()end;return shared.lateLoaded'))
        self.assertEqual(loaded['values'],[True])

    def test_local_registration_and_require_validation(self):
        host=self.host()
        result=self.execute(host,'''local module=Instance.new("ModuleScript")
client.registerScriptSource(module,"return {answer=42}")
local value=require(module)
return value.answer,pcall(require,123),pcall(require,"path"),pcall(require,workspace)''')
        self.assertEqual(result['values'][:4],[42,False,False,False])

    def test_cycles_leave_foreground_commands_responsive(self):
        data=snapshot();add_script(data,'A','return require(script.Parent.B)');add_script(data,'B','return require(script.Parent.A)')
        host=self.host(data)
        first=self.execute(host,'return require(game:GetService("ReplicatedStorage").A)')
        self.assertEqual(first['state'],'running')
        self.assertEqual(self.execute(host,'return 42')['values'],[42])

    def test_live_adapter_initializes_and_starts_received_source_without_console_submit(self):
        world,_=fixture();world.schema['classes'].append({'name':'LocalScript','properties':[]})
        world.instances[(1,20)]={'class':len(world.schema['classes'])-1,'parent':(1,3),
                                'properties':{'Name':b'ReceivedAuto','Source':b'shared.receivedAuto=script.Name'}}
        runtime=PersistentClientScriptRuntime(lambda:world,123);self.addCleanup(runtime.close)
        result=runtime.poll(force=True)
        self.assertIsNotNone(result)
        self.assertEqual(runtime.execute('return shared.receivedAuto')['values'],[None])
        world.tags.append(13)
        runtime.poll(force=True)
        self.assertEqual(runtime.execute('return shared.receivedAuto')['values'],['ReceivedAuto'])

    def test_require_waits_for_verification_and_resumes_after_catalog_update(self):
        data=snapshot();add_script(data,'Pending',None)
        data['script_sources']['Pending']={'reason':'verification_pending'}
        host=self.host(data)
        result=self.execute(host,'return require(game:GetService("ReplicatedStorage").Pending).answer')
        self.assertEqual(result['state'],'running')
        update=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':0,'actions':[],
                           'script_sources':{'Pending':{'source':'return {answer=42}'}}}})
        completed=next((item for item in update.get('completed_jobs', [])
                        if item['job_id']==result['job_id']),None)
        if completed is None:
            completed=self.completed(host,result)
        self.assertTrue(completed['ok'],completed)
        self.assertEqual(completed['values'],[42])

    def test_cloned_modules_keep_code_and_have_distinct_cache_entries(self):
        result=self.execute(self.host(),'''local module=Instance.new("ModuleScript")
client.registerScriptSource(module,"shared.cloneLoads=(shared.cloneLoads or 0)+1;return {}")
local first=require(module)
local cloned=module:Clone()
local second=require(cloned)
return first~=second,require(cloned)==second,shared.cloneLoads''')
        self.assertEqual(result['values'],[True,True,2])

    def test_starter_player_templates_clone_into_real_player_scripts(self):
        data=snapshot()
        data['nodes'].extend([
            {'id':'starter','class_name':'StarterPlayer','name':'StarterPlayer','parent':None,'properties':{}},
            {'id':'starter-scripts','class_name':'StarterPlayerScripts','name':'StarterPlayerScripts','parent':'starter','properties':{}},
        ])
        data['services']['StarterPlayer']='starter'
        add_script(data,'Library','return {answer=42}',parent='starter-scripts')
        add_script(data,'Boot','shared.templateRuns=(shared.templateRuns or 0)+1;shared.templateValue=require(script.Parent.Library).answer',kind='LocalScript',parent='starter-scripts')
        host=self.host(data)
        result=self.completed(host,self.execute(host,'local ps=game:GetService("Players").LocalPlayer.PlayerScripts;while shared.templateValue==nil do task.wait()end;return ps.ClassName,shared.templateRuns,shared.templateValue,ps.Boot~=game:GetService("StarterPlayer").StarterPlayerScripts.Boot'))
        self.assertEqual(result['values'],['PlayerScripts',1,42,True])
        self.assertEqual(self.execute(host,'return shared.templateRuns')['values'],[1])

    def test_large_starter_gui_clones_without_exporting_every_node(self):
        data=snapshot()
        data['services']['StarterGui']='starter-gui'
        data['nodes'].extend([
            {'id':'starter-gui','class_name':'StarterGui','name':'StarterGui',
             'parent':None,'properties':{}},
            {'id':'ui-template','class_name':'Folder','name':'LargeUI',
             'parent':'starter-gui','properties':{'Archivable':True}},
        ])
        for index in range(90):
            data['nodes'].append({'id':f'widget:{index}','class_name':'Folder',
                'name':f'Widget{index}','parent':'ui-template',
                'properties':{'Archivable':True,'Payload':'x'*2048}})
        host=self.host(data)
        self.assertEqual(host.initial_result['script_runtime']['bootstrap_errors'],0)
        clones=[row for row in host.initial_result['mutations']
                if row['kind']=='template_clone']
        self.assertEqual(len(clones),1)
        self.assertEqual(clones[0]['nodes'],91)
        result=self.execute(host,'''local ui=game:GetService("Players").LocalPlayer.PlayerGui.LargeUI
return #ui:GetChildren(),#ui.Widget0.Payload''')
        self.assertEqual(result['values'],[90,2048])

    def test_large_starter_template_keeps_output_bounded_without_losing_nodes(self):
        data=snapshot()
        data['nodes'].extend([
            {'id':'starter','class_name':'StarterPlayer','name':'StarterPlayer','parent':None,'properties':{}},
            {'id':'starter-scripts','class_name':'StarterPlayerScripts','name':'StarterPlayerScripts','parent':'starter','properties':{}},
        ])
        data['services']['StarterPlayer']='starter'
        data['nodes'].append({'id':'scope:large-template-gate','class_name':'BindableEvent',
                              'name':'LargeTemplateGate','parent':'scope:workspace','properties':{}})
        # The internal template exceeds the 128 KiB mutation-report budget.
        # Its complete local hierarchy must survive the compact template report.
        for i in range(160):
            data['nodes'].append({'id':f'template-{i}','class_name':'StringValue',
                'name':f'Value{i}','parent':'starter-scripts',
                'properties':{'Archivable':True,'Value':'x'*1024}})
        add_script(data,'Boot','shared.largeTemplateRan=true;workspace.LargeTemplateGate:Fire()',
                   kind='LocalScript',parent='starter-scripts')
        host=self.host(data)
        result=self.execute(host,'''local ps=game:GetService("Players").LocalPlayer.PlayerScripts
if not shared.largeTemplateRan then workspace.LargeTemplateGate.Event:Wait() end
return #ps:GetChildren(),#ps.Value159.Value,shared.largeTemplateRan''')
        result=self.completed(host,result)
        self.assertEqual(result['values'],[161,1024,True])
        self.assertLess(len(json.dumps(result).encode()),128*1024)

    def test_client_run_context_and_destroyed_script_cleanup(self):
        data=snapshot();add_script(data,'ClientScript','shared.clientContext=true',kind='Script')
        data['nodes'][-1]['properties']['RunContext']=2
        data['nodes'].append({'id':'scope:cleanup-gate','class_name':'BindableEvent',
                              'name':'CleanupGate','parent':'scope:workspace','properties':{}})
        add_script(data,'RemoveMe','shared.removeStarted=true;workspace.CleanupGate.Event:Wait();shared.shouldNotRun=true',
                   kind='LocalScript',parent='gui')
        host=self.host(data)
        self.assertEqual(self.execute(host,'return shared.clientContext,shared.removeStarted')['values'],[True,True])
        host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':1,
                  'actions':[{'sequence':1,'kind':'remove','id':'RemoveMe'}]}})
        self.assertEqual(self.execute(host,'workspace.CleanupGate:Fire();return shared.shouldNotRun==nil')['values'],[True])

    def test_modules_can_return_functions(self):
        data=snapshot();add_script(data,'Function','return function(x)return x+1 end')
        result=self.execute(self.host(data),'local fn=require(game:GetService("ReplicatedStorage").Function);return type(fn),fn(41)')
        self.assertEqual(result['values'],['function',42])


if __name__=='__main__':unittest.main()
