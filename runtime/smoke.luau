local a, b = ...
local mt = { __add = function(x, y) return x.n + y end }
local box = setmetatable({n = a}, mt)
local random = Random.new(1234)
local ok, value = xpcall(function() return bit32.band(box + b, 0xff) end, tostring)
return ok, value, random:NextInteger(1, 1000), string.format("sum=%d", a + b)
