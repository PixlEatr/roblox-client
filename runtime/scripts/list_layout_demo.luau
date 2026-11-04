-- Requires a PlayerGui and a local display surface. Cleans up its own instances.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "ListLayoutDemo"
local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(300, 200)
    local layout = Instance.new("UIListLayout", panel)
    layout.FillDirection = Enum.FillDirection.Vertical
    layout.SortOrder = Enum.SortOrder.LayoutOrder
    layout.Padding = UDim.new(0, 8)
    layout.HorizontalAlignment = Enum.HorizontalAlignment.Center
    layout.VerticalAlignment = Enum.VerticalAlignment.Top
    for index = 1, 3 do
        local button = Instance.new("TextButton", panel)
        button.Name = "Action" .. index
        button.Text = "Action " .. index
        button.Size = UDim2.fromOffset(160, 32)
        button.LayoutOrder = index
    end
    assert(layout.AbsoluteContentSize == Vector2.new(160, 112))
    assert(panel.Action2.AbsolutePosition - panel.AbsolutePosition == Vector2.new(70, 40))
    panel.Action1.Visible = false
    assert(layout.AbsoluteContentSize == Vector2.new(160, 72))
    assert(panel.Action2.AbsolutePosition - panel.AbsolutePosition == Vector2.new(70, 0))
    return layout.AbsoluteContentSize
end)
screen:Destroy()
assert(ok, result)
return result
