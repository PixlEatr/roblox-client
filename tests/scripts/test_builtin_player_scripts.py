"""Pinned client modules, normal execution, and game template precedence."""
import unittest

from builtin_player_scripts import player_scripts
import test_script_execution as script_tests
from test_script_execution import snapshot, add_script


class BuiltinPlayerScriptTests(unittest.TestCase):
    host = script_tests.ScriptExecutionTests.host
    execute = script_tests.ScriptExecutionTests.execute
    completed = script_tests.ScriptExecutionTests.completed

    def test_compiled_debug_source_uses_current_instance_name(self):
        host=self.host()
        result=self.execute(host,'''
local module=Instance.new("ModuleScript")
module.Name="RenamedModule"
client.registerScriptSource(module,"return function() return 7 end")
local fn=require(module)
local compiled=getscriptclosure(module)
return debug.info(fn,"s"),debug.info(compiled,"s")
''')
        self.assertEqual(result['values'],['RenamedModule','RenamedModule'])

    def test_pinned_source_tree_runs_and_survives_catalog_refresh(self):
        data = snapshot()
        data['builtin_player_scripts'] = player_scripts()
        host = self.host(data)
        result = self.completed(host, self.execute(host, '''
local scripts=game.Players.LocalPlayer.PlayerScripts
assert(scripts.PlayerScriptsLoader:IsA("LocalScript"))
assert(scripts.RbxCharacterSounds:IsA("LocalScript"))
assert(scripts.PlayerModule:IsA("ModuleScript"))
local module=scripts.PlayerModule.CommonUtils.ConnectionUtil
local util=require(module)
local manager=util.new()
local event=Instance.new("BindableEvent")
local count=0
manager:trackConnection("probe",event.Event:Connect(function()count+=1 end))
event:Fire();task.wait();assert(count==1)
manager:disconnect("probe")
event:Fire();task.wait();assert(count==1)
getgenv().pinnedModule=module
getgenv().pinnedBytes=getscriptbytecode(module)
return not isexecutorclosure(util.new)
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True])
        update=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':1,'actions':[], 'script_sources':{}}})
        self.assertTrue(update['ok'], update)
        self.assertEqual(self.execute(host, 'return getscriptbytecode(pinnedModule)==pinnedBytes')['values'], [True])

    def test_game_template_precedes_builtin_root_and_initial_replication(self):
        data = snapshot()
        data['initial_replication_complete'] = False
        data['builtin_player_scripts'] = player_scripts()
        data['nodes'].extend([
            {'id':'StarterPlayer','class_name':'StarterPlayer','name':'StarterPlayer','parent':'game','properties':{}},
            {'id':'Templates','class_name':'StarterPlayerScripts','name':'StarterPlayerScripts','parent':'StarterPlayer','properties':{}}])
        data['services']['StarterPlayer'] = 'StarterPlayer'
        add_script(data, 'PlayerModule', 'return {custom=true}', parent='Templates')
        host = self.host(data)
        self.assertEqual(self.execute(host, 'return game.Players.LocalPlayer:FindFirstChildOfClass("PlayerScripts")==nil')['values'], [True])
        update=host.call({'op':'update','delta':{'version':1,'delta':True,'sequence':1,'actions':[], 'initial_replication_complete':True}})
        self.assertTrue(update['ok'], update)
        result = self.completed(host, self.execute(host, '''
local scripts=game.Players.LocalPlayer:WaitForChild("PlayerScripts")
local module=scripts:WaitForChild("PlayerModule")
assert(require(module).custom==true)
assert(#module:GetChildren()==0)
return true
'''))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['values'], [True])
