local white = BrickColor.new("White")
assert(white == BrickColor.White())
assert(white.Number == 1 and white.Name == "White")
assert(typeof(white.Color) == "Color3")

local red = BrickColor.new(21)
assert(red.Name == "Bright red")
assert(math.abs(red.r - 196 / 255) < 1e-9)
assert(BrickColor.new(red.Color) == red)
assert(BrickColor.new("Bright blue").Number == 23)

assert(not pcall(function() return BrickColor.new(999) end))
assert(not pcall(function() return BrickColor.new("not in local palette") end))
assert(not pcall(function() return BrickColor.palette(1) end))
assert(not pcall(function() return BrickColor.random() end))
return white.Name, red.Number, red.Name
