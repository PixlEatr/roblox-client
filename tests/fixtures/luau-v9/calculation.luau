local a, b = ...
local values = {marker = "owned-v9", enabled = true}
values.sum = a + b
if a < b then
    values.sum += 1
end
local function calculate(value)
    return bit32.bxor(value, 0x1234)
end
return calculate(values.sum), values.marker
