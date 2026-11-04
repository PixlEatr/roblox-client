local root = Instance.new("Folder", workspace)
root.Name = "QueryExample"

local group = Instance.new("Model", root)
group.Name = "Enemies"

local target = Instance.new("Part", group)
target.Name = "Target"
target:AddTag("Enemy")
target:SetAttribute("Health", 100)

local matches = root:QueryDescendants("Model > BasePart.Enemy[$Health = 100]")
assert(#matches == 1 and matches[1] == target)
assert(#root:QueryDescendants("Model:has(> .Enemy)") == 1)

target:SetAttribute("Health", 0)
assert(#root:QueryDescendants(".Enemy:not([$Health = 0])") == 0)

root:Destroy()
return "Instance queries completed", #matches
