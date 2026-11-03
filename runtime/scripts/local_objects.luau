-- A normal Luau program: functions, loops, native instances, and local writes.
local function buildParts(count)
    local folder = Instance.new("Folder", workspace)
    folder.Name = "__ClientLuauExample"
    local total = 0
    for index = 1, count do
        local part = Instance.new("Part", folder)
        part.Name = "ExamplePart" .. index
        part.Anchored = true
        total += index
    end
    return folder, total
end

local folder, total = buildParts(5)
assert(#folder:GetChildren() == 5)
print("Created local parts:", #folder:GetChildren(), "sum:", total)
return #folder:GetChildren(), total
