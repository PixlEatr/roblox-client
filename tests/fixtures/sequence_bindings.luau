local a = NumberSequenceKeypoint.new(0, 2, 0.25)
local b = NumberSequenceKeypoint.new(0.5, 4)
local c = NumberSequenceKeypoint.new(1, 3, 0.5)
assert(typeof(a) == "NumberSequenceKeypoint")
assert(a.Time == 0 and a.Value == 2 and a.Envelope == 0.25)
assert(a == NumberSequenceKeypoint.new(0, 2, 0.25))

local sequence = NumberSequence.new({ a, b, c })
assert(typeof(sequence) == "NumberSequence")
assert(#sequence.Keypoints == 3)
assert(sequence.Keypoints[2] == b)
assert(sequence == NumberSequence.new({ a, b, c }))
assert(sequence ~= NumberSequence.new(2))

local constant = NumberSequence.new(5)
assert(#constant.Keypoints == 2)
assert(constant.Keypoints[1].Time == 0 and constant.Keypoints[2].Time == 1)
assert(constant.Keypoints[1].Value == 5)
local endpoints = NumberSequence.new(5, 0.5)
assert(endpoints.Keypoints[1].Value == 5 and endpoints.Keypoints[2].Value == 0.5)
assert(endpoints.Keypoints[1].Envelope == 0 and endpoints.Keypoints[2].Envelope == 0)
local repeated = NumberSequence.new({
    NumberSequenceKeypoint.new(0, 1),
    NumberSequenceKeypoint.new(0.5, 2),
    NumberSequenceKeypoint.new(0.5, 3),
    NumberSequenceKeypoint.new(1, 4),
})
assert(#repeated.Keypoints == 4)

assert(not pcall(function() NumberSequenceKeypoint.new(-0.1, 2) end))
assert(not pcall(function() NumberSequenceKeypoint.new(1.1, 2) end))
assert(not pcall(function() NumberSequenceKeypoint.new(0, 2, -1) end))
assert(not pcall(function() NumberSequenceKeypoint.new(0/0, 2) end))
assert(not pcall(function() NumberSequence.new({ b, a, c }) end))
assert(not pcall(function() NumberSequence.new({ b, c }) end))
assert(not pcall(function() NumberSequence.new({ a, b }) end))
assert(not pcall(function() NumberSequence.new({ a, b, c, extra = b }) end))
assert(not pcall(function() sequence.Keypoints = {} end))
assert(not pcall(function() a.Value = 8 end))

local red = Color3.new(1, 0, 0)
local blue = Color3.new(0, 0, 1)
local ca = ColorSequenceKeypoint.new(0, red)
local cb = ColorSequenceKeypoint.new(1, blue)
local colors = ColorSequence.new({ ca, cb })
assert(typeof(ca) == "ColorSequenceKeypoint")
assert(typeof(colors) == "ColorSequence")
assert(colors.Keypoints[1].Value == red and colors.Keypoints[2].Value == blue)
assert(colors == ColorSequence.new({ ca, cb }))
local solid = ColorSequence.new(red)
local gradient = ColorSequence.new(red, blue)
assert(solid.Keypoints[1].Value == red and solid.Keypoints[2].Value == red)
assert(gradient.Keypoints[1].Value == red and gradient.Keypoints[2].Value == blue)
local repeatedColors = ColorSequence.new({
    ColorSequenceKeypoint.new(0, red),
    ColorSequenceKeypoint.new(0.5, red),
    ColorSequenceKeypoint.new(0.5, blue),
    ColorSequenceKeypoint.new(1, blue),
})
assert(#repeatedColors.Keypoints == 4)
assert(not pcall(function() ColorSequenceKeypoint.new(1.1, red) end))
assert(not pcall(function() ColorSequence.new({ cb, ca }) end))
assert(not pcall(function() ColorSequence.new({ ca, ca }) end))

return "NumberSequence fixtures passed"
