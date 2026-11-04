-- Run with a host that calls install_color3_bindings before source execution.
local function near(a, b, epsilon)
    return math.abs(a - b) <= (epsilon or 1e-6)
end

local red = Color3.new(1, 0, 0)
assert(typeof(red) == "Color3")
assert(red.R == 1 and red.G == 0 and red.B == 0)
assert(tostring(red) == "1, 0, 0")
assert(red == Color3.fromRGB(255, 0, 0))
assert(red ~= Color3.new(1, 0, 0.001))
assert(Color3.new() == Color3.new(0, 0, 0))
assert(Color3.new(2, -1, 0.5).R == 2)
local scaled = Color3.fromRGB(510, -255, 128)
assert(scaled.R == 2 and scaled.G == -1 and near(scaled.B, 128 / 255))

local orange = Color3.fromHex("#Fa0")
assert(orange == Color3.fromHex("ffaa00"))
assert(orange:ToHex() == "ffaa00")
local roundTrip = Color3.fromHex(orange:ToHex())
assert(roundTrip == orange)

local green = Color3.fromHSV(1 / 3, 1, 1)
assert(near(green.R, 0) and near(green.G, 1) and near(green.B, 0))
local h, s, v = green:ToHSV()
assert(near(h, 1 / 3) and near(s, 1) and near(v, 1))
local redHue, redSat, redValue = red:ToHSV()
assert(redHue == 1 and redSat == 1 and redValue == 1)

local middle = Color3.new(0, 0, 0):Lerp(Color3.new(1, 1, 1), 0.5)
assert(middle == Color3.new(0.5, 0.5, 0.5))
assert(middle:ToHex() == "808080")

assert(not pcall(Color3.fromHex, "#gg0000"))
assert(not pcall(Color3.fromHex, "abcd"))
assert(not pcall(Color3.new, 0 / 0, 0, 0))
assert(not pcall(function() red.R = 0 end))

return true
