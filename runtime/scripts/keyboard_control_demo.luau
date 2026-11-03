-- Requires a host keyboard backend (KeyboardEnabled=true).
-- Press W through the host input bridge to move a real local part for 4 frames.
local input = game:GetService("UserInputService")
local run = game:GetService("RunService")
assert(input.KeyboardEnabled, "Attach a keyboard backend before running this demo")
local part = Instance.new("Part")
part.Name = "KeyboardControlDemo"
part.Anchored = true
part.CFrame = CFrame.new(0, 5, 0)
part.Parent = workspace
local origin = part.Position
local held = false
local started = input.InputBegan:Connect(function(event, processed)
    if not processed and event.KeyCode == Enum.KeyCode.W then held = true end
end)
local ended = input.InputEnded:Connect(function(event)
    if event.KeyCode == Enum.KeyCode.W then held = false end
end)
local binding = "KeyboardControlDemo_" .. game:GetService("HttpService"):GenerateGUID(false)
local frames = 0
run:BindToRenderStep(binding, Enum.RenderPriority.Input.Value, function(dt)
    if held then
        part.CFrame = part.CFrame + Vector3.new(0, 0, -16 * dt)
        frames += 1
    end
end)
local ok, message = pcall(function()
    local deadline = time() + 2
    while frames < 4 do
        assert(time() < deadline, "No W input received before the demo timeout")
        run.PreRender:Wait()
    end
end)
run:UnbindFromRenderStep(binding)
started:Disconnect()
ended:Disconnect()
local distance = (part.Position - origin).Magnitude
part:Destroy()
if not ok then error(message, 0) end
return "Keyboard moved a local part", frames, distance
