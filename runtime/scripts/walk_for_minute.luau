local player = game:GetService("Players").LocalPlayer
local character = assert(player.Character, "Character is not available")
local humanoid = character:WaitForChild("Humanoid")
local directions = {
    Vector3.new(1, 0, 0),
    Vector3.new(0, 0, 1),
    Vector3.new(-1, 0, 0),
    Vector3.new(0, 0, -1),
}

-- Use absolute deadlines so late task resumption does not accumulate into
-- a longer test. Skip missed turns rather than replaying them in a burst.
local started = elapsedTime()
local deadline = started + 60
local lastSegment = -1
while elapsedTime() < deadline do
    assert(player.Character == character and humanoid.Parent ~= nil,
        "Character changed during the walk")
    local now = elapsedTime()
    local segment = math.floor(now - started)
    if segment ~= lastSegment then
        humanoid:Move(directions[segment % #directions + 1], false)
        lastSegment = segment
    end
    task.wait(math.max(0, math.min(started + segment + 1, deadline) - now))
end

humanoid:Move(Vector3.new(0, 0, 0), false)
return "walk_completed"
