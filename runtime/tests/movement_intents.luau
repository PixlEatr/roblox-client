-- Exercised by a host fixture that installs the local MovementIntent binding.
local players = game:GetService("Players")
local player = players.LocalPlayer
local character = assert(player.Character)
local humanoid = assert(character:FindFirstChildOfClass("Humanoid"))

-- The omitted flag uses the documented relativeToCamera=false default.
humanoid:Move(Vector3.new(1, 0, 0))
humanoid:Move(Vector3.new(0, 0, 1), true)
humanoid:MoveTo(Vector3.new(12, 3, -5))

-- Method lookup is dynamic: a copied method must resolve its call-time self.
local otherHumanoid = Instance.new("Humanoid", character)
local copiedMove = humanoid.Move
copiedMove(otherHumanoid, Vector3.new(-1, 0, 0))

assert(not pcall(function() humanoid:Move(Vector3.zero, "camera") end))
assert(not pcall(function() humanoid:MoveTo(Vector3.new(0/0, 0, 0)) end))
return "four local movement intents queued"
