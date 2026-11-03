-- Runs after the custom runtime binds the local HttpService JSON methods.
local http = game:GetService("HttpService")

assert(http:JSONEncode({}) == "[]")
assert(http:JSONEncode({"a", 7, true}) == '["a",7,true]')
assert(http:JSONEncode({answer = 42, label = "hello"}) == '{"answer":42,"label":"hello"}')
assert(http:JSONEncode(nil) == "null")

local decoded = http:JSONDecode('{"ok":true,"n":12,"items":["x",null,3]}')
assert(decoded.ok == true and decoded.n == 12)
assert(decoded.items[1] == "x" and decoded.items[2] == nil and decoded.items[3] == 3)
assert(http:JSONDecode("null") == nil)
local nulKey = http:JSONDecode('{"a\\u0000b":7}')
assert(nulKey["a\0b"]==7 and nulKey.a==nil)

local largeText = string.rep("z", 1200 * 1024)
local largeEncoded = http:JSONEncode({metadata = largeText})
assert(#largeEncoded > 1024 * 1024 and #largeEncoded < 16 * 1024 * 1024)
assert(http:JSONDecode(largeEncoded).metadata == largeText)

local function fails(callback)
    local ok = pcall(callback)
    assert(not ok)
end

fails(function() http:JSONDecode("{") end)
local tooDeep = string.rep("[", 129) .. string.rep("]", 129)
fails(function() http:JSONDecode(tooDeep) end)
fails(function() http:JSONEncode({[1] = "sparse", [3] = "array"}) end)
fails(function() http:JSONEncode({[true] = "not a JSON key"}) end)
fails(function() http:JSONEncode({value = 0 / 0}) end)
local cycle = {}
cycle.self = cycle
fails(function() http:JSONEncode(cycle) end)

return "JSON binding fixture passed"
