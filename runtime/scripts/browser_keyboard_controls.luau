-- Run once, then focus the browser's Keyboard controls area.
-- WASD/arrows move in world axes; Space jumps. This installs local bindings.
-- Stop with shared.keyboardControls.stop(); running this again replaces them.
local actions = game:GetService("ContextActionService")
local run = game:GetService("RunService")
local player = assert(game:GetService("Players").LocalPlayer, "LocalPlayer is not ready")
if shared.keyboardControls and type(shared.keyboardControls.stop) == "function" then
    shared.keyboardControls.stop()
end
local prefix = "BrowserKeyboard_" .. game:GetService("HttpService"):GenerateGUID(false)
local names = {}
local pressed = {forward = {}, backward = {}, left = {}, right = {}}
local stopped = false
local function humanoid()
    local character = player.Character
    return character and character:FindFirstChildOfClass("Humanoid")
end
local function direction()
    local x = (next(pressed.right) and 1 or 0) - (next(pressed.left) and 1 or 0)
    local z = (next(pressed.backward) and 1 or 0) - (next(pressed.forward) and 1 or 0)
    local value = Vector3.new(x, 0, z)
    return value.Magnitude > 1 and value.Unit or value
end
local function bindDirection(name, action)
    local fullName = prefix .. name
    table.insert(names, fullName)
    actions:BindAction(fullName, function(_, state, input)
        if stopped then return Enum.ContextActionResult.Sink end
        if state == Enum.UserInputState.Begin then
            pressed[name][input] = true
        elseif state == Enum.UserInputState.End then
            pressed[name][input] = nil
        elseif state == Enum.UserInputState.Cancel then
            table.clear(pressed[name])
        end
        return Enum.ContextActionResult.Sink
    end, false, action)
end
local controls = {direction = direction}
function controls.stop()
    if stopped then return end
    stopped = true
    run:UnbindFromRenderStep(prefix)
    for _, name in ipairs(names) do actions:UnbindAction(name) end
    for _, keys in pairs(pressed) do table.clear(keys) end
    local target = humanoid()
    if target then target:Move(Vector3.zero, false); target.Jump = false end
end
local ok, message = pcall(function()
    bindDirection("forward", Enum.PlayerActions.CharacterForward)
    bindDirection("backward", Enum.PlayerActions.CharacterBackward)
    bindDirection("left", Enum.PlayerActions.CharacterLeft)
    bindDirection("right", Enum.PlayerActions.CharacterRight)
    local jump = prefix .. "jump"
    table.insert(names, jump)
    actions:BindAction(jump, function(_, state)
        local target = humanoid()
        if target and not stopped then target.Jump = state == Enum.UserInputState.Begin end
        return Enum.ContextActionResult.Sink
    end, false, Enum.PlayerActions.CharacterJump)
    run:BindToRenderStep(prefix, Enum.RenderPriority.Character.Value, function()
        local target = humanoid()
        if target then target:Move(direction(), false) end
    end)
end)
if not ok then controls.stop(); error(message, 0) end
shared.keyboardControls = controls
return "Keyboard controls ready: WASD/arrows + Space. Stop: shared.keyboardControls.stop()"
