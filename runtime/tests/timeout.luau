while true do
    local ok = pcall(function()
        while true do end
    end)
    if not ok then
        -- Catching the inner error must not make the outer loop immortal.
    end
end
