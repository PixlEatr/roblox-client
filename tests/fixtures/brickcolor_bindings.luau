local white = BrickColor.new("White")
assert(white == BrickColor.White())
assert(white.Number == 1 and white.Name == "White")
assert(typeof(white.Color) == "Color3")

local red = BrickColor.new(21)
assert(red.Name == "Bright red")
assert(math.abs(red.r - 196 / 255) < 1e-9)
assert(BrickColor.new(red.Color) == red)
assert(BrickColor.new("Bright blue").Number == 23)

assert(BrickColor.new(999) == BrickColor.Gray())
assert(BrickColor.new("not in local palette") == BrickColor.Gray())
assert(BrickColor.palette(1).Number == 301)
assert(typeof(BrickColor.random()) == "BrickColor")
return white.Name, red.Number, red.Name
