-- The host must supply keyboard input (KeyboardEnabled=true).
-- W or Up drives the existing Humanoid movement bridge for four frames.
local actions = game:GetService("ContextActionService")
local run = game:GetService("RunService")
local player = game:GetService("Players").LocalPlayer
local humanoid = assert(player.Character, "Character is not loaded"):FindFirstChildOfClass("Humanoid")
assert(humanoid, "Character has no Humanoid")
local name = "ContextMovement_" .. game:GetService("HttpService"):GenerateGUID(false)
local direction = Vector3.zero
local frames = 0
local began = false
local function handleAction(_, state)
    if state == Enum.UserInputState.Begin then
        direction = Vector3.new(0, 0, -1)
        began = true
    elseif state == Enum.UserInputState.End or state == Enum.UserInputState.Cancel then
        direction = Vector3.zero
    end
    return Enum.ContextActionResult.Sink
end
actions:BindActionAtPriority(name, handleAction, false,
    Enum.ContextActionPriority.Medium.Value, Enum.PlayerActions.CharacterForward)
run:BindToRenderStep(name, Enum.RenderPriority.Character.Value, function()
    humanoid:Move(direction, false)
    if direction.Magnitude > 0 then frames += 1 end
end)
local ok, message = pcall(function()
    local deadline = time() + 2
    while frames < 4 do
        assert(time() < deadline, "No forward input received before the demo timeout")
        run.PreRender:Wait()
    end
end)
run:UnbindFromRenderStep(name)
actions:UnbindAction(name)
humanoid:Move(Vector3.zero, false)
if not ok then error(message, 0) end
return "Context action drove Humanoid movement", frames, began
