-- Bounded own-character jump exercise. Results describe local prediction only.
local player = game:GetService("Players").LocalPlayer
local character = player and player.Character
local humanoid = character and character:FindFirstChildOfClass("Humanoid")
if not humanoid then return "jump_smoke", false, "character_unavailable" end

local function currentState()
    local ok, state = pcall(function() return humanoid:GetState() end)
    return ok and state.Name or nil
end

for _ = 1, 120 do
    if currentState() == "Running" then break end
    task.wait(0.1)
end
if currentState() ~= "Running" then
    return "jump_smoke", false, "ground_contact_unavailable"
end

local jumping, falling, landed = 0, 0, 0
local connection = humanoid.StateChanged:Connect(function(_, state)
    if state == Enum.HumanoidStateType.Jumping then jumping += 1 end
    if state == Enum.HumanoidStateType.Freefall then falling += 1 end
    if state == Enum.HumanoidStateType.Landed then landed += 1 end
end)

-- Give the observer time to find the avatar after the test is announced.
task.wait(8)
for attempt = 1, 2 do
    humanoid.Jump = true
    for _ = 1, 100 do
        task.wait(0.05)
        if landed >= attempt and currentState() == "Running" then break end
    end
    if landed < attempt then break end
    if attempt < 2 then task.wait(2) end
end
connection:Disconnect()
return "jump_smoke", jumping >= 2 and falling >= 2 and landed >= 2,
    jumping, falling, landed
