-- Scripted geometry demo. Requires PlayerGui and a configured display surface.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "WrappedListDemo"
local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(340, 200)
    local layout = Instance.new("UIListLayout", panel)
    layout.FillDirection = Enum.FillDirection.Horizontal
    layout.SortOrder = Enum.SortOrder.LayoutOrder
    layout.HorizontalAlignment = Enum.HorizontalAlignment.Center
    layout.Wraps = true
    layout.Padding = UDim.new(0, 8)
    layout.ItemLineAlignment = Enum.ItemLineAlignment.Stretch
    for index = 1, 3 do
        local button = Instance.new("TextButton", panel)
        button.Name = "Action" .. index
        button.Text = "Action " .. index
        button.LayoutOrder = index
        button.Size = UDim2.fromOffset(160, index == 1 and 16 or 32)
    end
    assert(layout.AbsoluteContentSize == Vector2.new(328, 72))
    assert(panel.Action1.AbsoluteSize == Vector2.new(160, 32))
    assert(panel.Action3.AbsolutePosition - panel.AbsolutePosition == Vector2.new(90, 40))
    panel.Size = UDim2.fromOffset(500, 200)
    assert(layout.AbsoluteContentSize == Vector2.new(496, 32))
    assert(panel.Action3.AbsolutePosition - panel.AbsolutePosition == Vector2.new(338, 0))
    assert(panel.Action1.Size.Y.Offset == 16)
    return layout.AbsoluteContentSize
end)
screen:Destroy()
assert(ok, result)
return result
