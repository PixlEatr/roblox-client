-- Ordinary code runs inside this headless client's own Luau VM.
getgenv().exampleMultiplier = 3
shared.exampleName = "headless source helpers"

local fn, compileError = loadstring([[
    local values = ...
    local total = 0
    for _, value in values do
        total += value * exampleMultiplier
    end
    return total
]], "=user-authored example")
assert(fn, compileError)
assert(islclosure(fn) and iscclosure(loadstring))

local message = string.char(0, 255) .. "binary source data"
local encoded = crypt.base64.encode(message)
assert(base64_decode(encoded) == message)

local total = fn({1, 2, 3, 4})
print(shared.exampleName, total)
return total, crypt.base64.decode(encoded) == message
