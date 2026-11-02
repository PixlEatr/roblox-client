local players = game:GetService("Players")
local player = players.LocalPlayer
local character = player.Character
local root = character:FindFirstChild("HumanoidRootPart")
local humanoid = character:FindFirstChildOfClass("Humanoid")
assert(client.snapshot and client.sequence == 7)
assert(workspace == game:GetService("Workspace"))
assert(player == players.LocalPlayer and player.UserId == 123)
assert(character:IsA("Model") and root:IsA("BasePart"))
assert(root.Position.X == 1 and root.Position.Y == 2 and root.Position.Z == 3)
assert(root.CFrame.Position.X == 1)
assert(humanoid.Health == 100 and humanoid.WalkSpeed == 16)
assert(root.Parent == character and character.Parent == workspace)
assert(client.propertyOrigin(root, "Anchored") == "received")
assert(client.propertyOrigin(root, "CanCollide") == "unavailable")
root.Anchored = false
root.Parent = workspace
assert(root.Anchored == false and root.Parent == workspace)
assert(client.propertyOrigin(root, "Anchored") == "local")
print("world local simulation", player.Name)
warn("mutation is local only")
return true, player, root, root.CFrame
