-- Run in the owned headless VM after install_closure_bindings.
local function make_counter()
    local count = 0
    return function(value)
        count += value
        return count, "counter"
    end
end

local original = make_counter()
local clone = clonefunction(original)
assert(original ~= clone)
assert(islclosure(original) and islclosure(clone))
assert(iscclosure(clonefunction))
assert(clone(2) == 2)
assert(original(3) == 5) -- Cloned Luau closures share captured upvalues.
assert(clone(4) == 9)
assert(clonefunc(original) ~= original)

local custom_env = { marker = "kept" }
local environment_function = function()
    return marker
end
setfenv(environment_function, custom_env)
local environment_clone = clonefunction(environment_function)
assert(getfenv(environment_clone) == custom_env)
assert(environment_clone() == "kept")

local implementation = function(a, b, ...)
    return a + b, ...
end
local wrapped = newcclosure(implementation)
assert(wrapped ~= implementation)
assert(iscclosure(wrapped) and isnewcclosure(wrapped))
assert(not isnewcclosure(loadstring))
local first, second, third = wrapped(2, 5, "tail")
assert(first == 7 and second == "tail" and third == nil)
assert(newcclosure(loadstring) == loadstring)
assert(newcclosure(loadstring, "renamed") == loadstring)

local ok, message = pcall(newcclosure(function()
    error("wrapped failure")
end))
assert(not ok and string.find(message, "wrapped failure", 1, true))

local yielding = newcclosure(function(value)
    local resumed = coroutine.yield("paused", value)
    return "resumed", resumed
end)
local thread = coroutine.create(function()
    return yielding(17)
end)
local resumed, state, value = coroutine.resume(thread)
assert(resumed and state == "paused" and value == 17)
local finished, result, sent = coroutine.resume(thread, "continue")
assert(finished and result == "resumed" and sent == "continue")

local yielding_error = newcclosure(function()
    coroutine.yield("before failure")
    error("failure after yield")
end)
local error_thread = coroutine.create(yielding_error)
local yielded, marker = coroutine.resume(error_thread)
assert(yielded and marker == "before failure")
local failed, yielded_message = coroutine.resume(error_thread)
assert(not failed and string.find(yielded_message, "failure after yield", 1, true))

assert(not pcall(clonefunction, 42))
assert(not pcall(clonefunction, loadstring)) -- C closures lack public clone API.
assert(not pcall(newcclosure, 42))

local captured = "before"
local captured_nil = nil
local function captures()
    return captured, captured_nil
end
assert(getupvalue(captures, 1) == "before")
captured = "after"
assert(getupvalue(captures, 1) == "after")
assert(getupvalue(captures, 2) == nil)
assert(getupvalue(captures, 0) == nil)
assert(getupvalue(captures, -1) == nil)
assert(getupvalue(captures, 3) == nil)
assert(getupvalue(clonefunction(captures), 1) == "after")
assert(getupvalue(wrapped, 1) == implementation)
assert(not pcall(getupvalue, 42, 1))
assert(not pcall(getupvalue, captures))
return true
