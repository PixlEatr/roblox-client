-- Source control for the separate local prediction. No movement is sent.
assert(client.persistent, "use persistent source mode")
local player = game:GetService("Players").LocalPlayer
local character = assert(player.Character, "character has not arrived")
local humanoid = assert(character:FindFirstChildOfClass("Humanoid"))
humanoid:Move(Vector3.xAxis, false)
task.wait(0.25)
humanoid:Move(Vector3.zero, false)
return "local prediction requested; inspect local_movement in the source result"
