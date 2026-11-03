local player = assert(game:GetService("Players").LocalPlayer, "Player not loaded")
local target = player:FindFirstChildOfClass("PlayerGui") or player.Character
assert(target, "Wait for your character to load")

local folder = Instance.new("Folder", target)
folder.Name = "ModuleLoadingDemo"

local module = Instance.new("ModuleScript", folder)
module.Name = "Config"
client.registerScriptSource(module, [[
    print("Module initialized:", script.Name)
    return {answer = 42, greet = function() return "hello from a ModuleScript" end}
]])

local runner = Instance.new("LocalScript", folder)
runner.Name = "AutomaticRunner"
client.registerScriptSource(runner, [[
    local first = require(script.Parent.Config)
    local second = require(script.Parent.Config)
    assert(first == second, "Module cache did not preserve identity")
    print(first.greet())
    print("Answer:", first.answer)
    print("PASS: automatic LocalScript, require, and shared module cache")
    script.Parent:Destroy()
]])

print("Installed the demo. AutomaticRunner will start without another command.")
