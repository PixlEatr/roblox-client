-- Ordinary user-authored source, executed by the custom client's Luau VM.
local player = game:GetService("Players").LocalPlayer
assert(player, "LocalPlayer has not replicated")
local character = assert(player.Character, "Character has not replicated")
local root = assert(character:FindFirstChild("HumanoidRootPart"), "Root has not replicated")
local parts, anchored = 0, 0
for _, instance in character:GetDescendants() do
    if instance:IsA("BasePart") then
        parts += 1
        local ok, value = pcall(function() return instance.Anchored end)
        if ok and value then anchored += 1 end
    end
end
print("Root Anchored:", root.Anchored)
print("Value origin:", client.propertyOrigin(root, "Anchored"))
print("Character parts:", parts, "known anchored parts:", anchored)
return root.Anchored, client.propertyOrigin(root, "Anchored"), parts, anchored
