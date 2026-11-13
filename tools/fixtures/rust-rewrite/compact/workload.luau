local workspace = game:GetService("Workspace")
local count = 0
for _, instance in workspace:GetChildren() do
    if string.sub(instance.Name, 1, 8) == "Passive_" then
        assert(instance:IsA("BasePart"), "passive fixture node must be a BasePart")
        count += 1
    end
end
assert(count == 64, "snapshot passive part count mismatch")
local probe = assert(workspace:FindFirstChild("MemoryProbe"), "memory probe missing")
assert(probe:IsA("BasePart"), "memory probe must be a BasePart")
probe.Transparency = 0.25
task.wait(3.000)
return count, probe.Transparency
