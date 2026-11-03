-- Run in the terminal client after restarting it. All objects stay local.
local random = Random.new(42)
local choices = {"north", "south", "east", "west"}
random:Shuffle(choices)
local direction = random:NextUnitVector()

local region = Region3.new(Vector3.new(-1, 2, 3), Vector3.new(9, 10, 11))
local aligned = region:ExpandToGrid(4)
local material = PhysicalProperties.new(1, 0.5, 0.25, 1, 1)
local part = Instance.new("Part")
part.CustomPhysicalProperties = material
local copied = part:Clone()
assert(copied.CustomPhysicalProperties == material)

local encoded = game:GetService("HttpService"):UrlEncode("hello world")
print("Direction:", direction)
print("Order:", table.concat(choices, ", "))
print("Grid region:", aligned.CFrame.Position, aligned.Size)
print("Encoded:", encoded)
part:Destroy()
copied:Destroy()
return encoded, aligned, material
