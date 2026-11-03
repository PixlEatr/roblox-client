-- These objects stay local to this client's script model.
local folder = Instance.new("Folder", workspace)
folder.Name = "__HeadlessTypeExamples"

local text = Instance.new("StringValue", folder)
text.Name = "Message"
text.Value = "created from Luau"

local count = Instance.new("IntValue", folder)
count.Name = "Counter"
count.Value = 7

local part = Instance.new("MeshPart", folder)
part.Name = "LocalMeshPart"
part.Anchored = true
part.Position = Vector3.new(0, 10, 0)
assert(part:IsA("BasePart"))
assert(part.CFrame.Position == part.Position)

local reference = Instance.new("ObjectValue", folder)
reference.Name = "Target"
reference.Value = part
assert(reference.Value == part)

local humanoid = Instance.new("Humanoid", folder)
assert(humanoid.Health == 100)
assert(humanoid.WalkSpeed == 16)

print("Created local objects:", #folder:GetChildren())
print("Message:", text.Value, "count:", count.Value)
print("This Humanoid has no physics simulation yet.")
return #folder:GetChildren(), reference.Value == part
