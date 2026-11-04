"""Client preference state and native scheduler aliases through the real VM."""
import unittest

import test_script_execution as script_tests


class ClientSettingsTests(unittest.TestCase):
    host = script_tests.ScriptExecutionTests.host
    execute = script_tests.ScriptExecutionTests.execute
    completed = script_tests.ScriptExecutionTests.completed

    def run_source(self, host, source):
        result = self.completed(host, self.execute(host, source))
        self.assertTrue(result['ok'], result)
        return result

    def test_settings_identity_state_and_notifications(self):
        host = self.host()
        self.run_source(host, '''
local model=UserSettings()
local settings=model:GetService("UserGameSettings")
assert(model==UserSettings() and settings.Parent==model)
assert(model.GameSettings==settings and model:FindFirstChild("GameSettings")==settings)
assert(model:FindService("UserGameSettings")==settings)
assert(model:FindFirstChildOfClass("UserGameSettings")==settings)
assert(model:FindService("UnknownSettings")==nil)
assert(not pcall(function()model:GetService("UnknownSettings")end))
assert(not pcall(function()model:IsUserFeatureEnabled("UnknownFeature")end))
assert(client.propertyOrigin(settings,"CameraYInverted")=="local_settings")
local changes=0
settings:GetPropertyChangedSignal("CameraYInverted"):Connect(function()changes+=1 end)
assert(settings:GetCameraYInvertValue()==1)
settings.CameraYInverted=true
assert(settings:GetCameraYInvertValue()==-1)
settings.CameraYInverted=true
task.wait();assert(changes==1)
settings.CameraYInverted=false
task.wait();assert(changes==2 and settings:GetCameraYInvertValue()==1)
assert(not settings.IsUsingCameraYInverted)
local visible=0
settings:GetPropertyChangedSignal("IsUsingCameraYInverted"):Connect(function()visible+=1 end)
settings:SetCameraYInvertVisible();settings:SetCameraYInvertVisible()
settings:SetGamepadCameraSensitivityVisible()
task.wait();assert(visible==1)
assert(settings.IsUsingCameraYInverted and settings.IsUsingGamepadCameraSensitivity)
return true
''')
        result = self.run_source(host, 'return UserSettings():GetService("UserGameSettings").IsUsingCameraYInverted')
        self.assertEqual(result['values'], [True])
        fresh = self.run_source(self.host(), 'return UserSettings():GetService("UserGameSettings").IsUsingCameraYInverted')
        self.assertEqual(fresh['values'], [False])

    def test_settings_types_and_receiver_validation(self):
        self.run_source(self.host(), '''
local s=UserSettings():GetService("UserGameSettings")
s.RotationType=Enum.RotationType.CameraRelative
assert(s.RotationType==Enum.RotationType.CameraRelative)
s.ComputerMovementMode=Enum.ComputerMovementMode.ClickToMove
assert(s.ComputerMovementMode==Enum.ComputerMovementMode.ClickToMove)
assert(not pcall(function()s.RotationType=Enum.ControlMode.Classic end))
assert(not pcall(function()s.CameraYInverted=1 end))
assert(not pcall(function()s.MouseSensitivity="fast" end))
assert(not pcall(function()s.IsUsingCameraYInverted=true end))
local p=Instance.new("Part")
assert(not pcall(s.GetCameraYInvertValue,p))
assert(not pcall(s.SetCameraYInvertVisible,p))
assert(not pcall(function()return p.IsUsingCameraYInverted end))
return true
''')

    def test_legacy_scheduler_aliases_execute_real_callbacks(self):
        self.run_source(self.host(), '''
assert(Wait==wait and Spawn==spawn and Delay==delay and ElapsedTime==elapsedTime)
local called={}
Spawn(function()table.insert(called,"spawn")end)
Delay(.01,function()table.insert(called,"delay")end)
local before=ElapsedTime()
local duration=Wait(.03)
assert(type(duration)=="number" and duration>=.03 and ElapsedTime()>=before)
assert(table.find(called,"spawn") and table.find(called,"delay"))
return true
''')

    def test_runtime_globals_use_current_vm_state(self):
        result=self.run_source(self.host(),'''
assert(stats==Stats and stats()==game:GetService("Stats"))
assert(stats()==stats())
local before=collectgarbage("count")
local retained=table.create(100000,true)
local after=collectgarbage("count")
assert(after>before+500 and #retained==100000)
assert(math.abs(after-gcinfo())<2)
assert(not pcall(collectgarbage,"collect"))
assert(not pcall(collectgarbage,"stop"))
assert(not pcall(collectgarbage))
setthreadidentity(3);printidentity();printidentity("Probe identity")
setthreadidentity(8)
return true
''')
        self.assertIn('Current identity is 3',result['output'])
        self.assertIn('Probe identity 3',result['output'])
