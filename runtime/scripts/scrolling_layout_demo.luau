-- Requires a local display and PlayerGui. Exercises scripted scrolling only.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "ScrollingLayoutDemo"
local ok, result = pcall(function()
    local window = Instance.new("ScrollingFrame", screen)
    window.Size = UDim2.fromOffset(300, 200)
    window.CanvasSize = UDim2.fromOffset(0, 0)
    window.AutomaticCanvasSize = Enum.AutomaticSize.Y
    window.ScrollingDirection = Enum.ScrollingDirection.Y
    window.ScrollBarThickness = 10
    local layout = Instance.new("UIListLayout", window)
    layout.SortOrder = Enum.SortOrder.LayoutOrder
    layout.Padding = UDim.new(0, 2)
    for index = 1, 10 do
        local entry = Instance.new("TextButton", window)
        entry.Name = "Entry" .. index
        entry.Text = "Entry " .. index
        entry.LayoutOrder = index
        entry.Size = UDim2.fromOffset(280, 40)
    end
    assert(window.AbsoluteCanvasSize.Y == 418)
    assert(window.AbsoluteWindowSize == Vector2.new(290, 200))
    window.CanvasPosition = Vector2.new(0, 50)
    assert(window.Entry2.AbsolutePosition.Y - window.AbsolutePosition.Y == -8)
    assert(layout.AbsoluteContentSize == Vector2.new(280, 418))
    window.Entry10:Destroy()
    assert(window.AbsoluteCanvasSize.Y == 376)
    assert(window.CanvasSize == UDim2.fromOffset(0, 0))
    return layout.AbsoluteContentSize
end)
screen:Destroy()
assert(ok, result)
return result
