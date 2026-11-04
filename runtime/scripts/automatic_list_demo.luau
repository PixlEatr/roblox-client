-- A menu that grows around its list and a nested wrapping section.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "AutomaticListDemo"
local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(200, 0)
    panel.AutomaticSize = Enum.AutomaticSize.Y
    local padding = Instance.new("UIPadding", panel)
    padding.PaddingLeft = UDim.new(0, 10)
    padding.PaddingRight = UDim.new(0, 10)
    padding.PaddingTop = UDim.new(0, 10)
    padding.PaddingBottom = UDim.new(0, 10)
    local list = Instance.new("UIListLayout", panel)
    list.SortOrder = Enum.SortOrder.LayoutOrder
    list.Padding = UDim.new(0, 6)
    for index = 1, 3 do
        local button = Instance.new("TextButton", panel)
        button.Name = "Action" .. index
        button.Text = "Action " .. index
        button.LayoutOrder = index
        button.Size = UDim2.new(1, 0, 0, 32)
    end
    assert(panel.AbsoluteSize == Vector2.new(200, 128))
    local section = Instance.new("Frame", panel)
    section.Size = UDim2.new(1, 0, 0, 0)
    section.AutomaticSize = Enum.AutomaticSize.Y
    section.LayoutOrder = 4
    local tiles = Instance.new("UIListLayout", section)
    tiles.FillDirection = Enum.FillDirection.Horizontal
    tiles.Wraps = true
    tiles.Padding = UDim.new(0, 4)
    for index = 1, 3 do
        local tile = Instance.new("Frame", section)
        tile.Size = UDim2.fromOffset(80, 20)
    end
    assert(section.AbsoluteSize.Y == 44)
    assert(panel.AbsoluteSize.Y == 178)
    panel.Action2.Visible = false
    assert(panel.AbsoluteSize.Y == 140)
    panel.Size = UDim2.fromOffset(120, 0)
    assert(section.AbsoluteSize.Y == 68)
    assert(panel.AbsoluteSize == Vector2.new(120, 164))
    assert(panel.Size.Y.Offset == 0 and section.Size.Y.Offset == 0)
    return panel.AbsoluteSize
end)
screen:Destroy()
assert(ok, result)
return result
