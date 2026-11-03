local http = game:GetService("HttpService")
local id = http:GenerateGUID(false)
local wrapped = http:GenerateGUID()

assert(#id == 36 and id:sub(15, 15) == "4", "Invalid UUID v4")
assert(#wrapped == 38 and wrapped:sub(1, 1) == "{", "Missing braces")

local part = Instance.new("Part")
part:SetAttribute("UniqueId", id)
assert(part:GetAttribute("UniqueId") == id)
part:Destroy()

print("Unique ID:", id)
print("With braces:", wrapped)
print("PASS: GenerateGUID and local attribute storage")
return id
