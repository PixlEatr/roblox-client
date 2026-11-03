-- A second submitted script can see objects created by the first command.
local folder = assert(workspace:FindFirstChild("__ClientLuauExample"), "Run local_objects.luau first")
local count = 0
for _, child in folder:GetChildren() do
    assert(child:IsA("BasePart") and child.Anchored == true)
    count += 1
end
print("Persistent local parts:", count)
return count
