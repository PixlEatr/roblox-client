local colors = {}
for index = 0, 127 do
    colors[index + 1] = BrickColor.palette(index)
end

local matched = BrickColor.new(0.502, 0.734, 0.859)
local gold = BrickColor.palette(70)
local randomColor = BrickColor.random()

assert(matched.Name == "Pastel Blue")
assert(gold.Number == 333)
assert(BrickColor.new("unknown color") == BrickColor.Gray())
assert(typeof(randomColor) == "BrickColor")

return #colors, matched.Name, gold.Name
