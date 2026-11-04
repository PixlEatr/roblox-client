local ok = pcall(function()
    return string.rep("x", 40 * 1024 * 1024)
end)
return ok
