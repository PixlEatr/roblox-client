-- An automatically sized inventory grid. This checks local scripting geometry.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "GridLayoutDemo"
local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(220, 0)
    panel.AutomaticSize = Enum.AutomaticSize.Y
    local grid = Instance.new("UIGridLayout", panel)
    grid.CellSize = UDim2.fromOffset(50, 40)
    grid.CellPadding = UDim2.fromOffset(5, 5)
    for index = 1, 7 do
        local tile = Instance.new("TextButton", panel)
        tile.Name = "Slot" .. index
        tile.Text = tostring(index)
        tile.LayoutOrder = index
    end
    grid:ApplyLayout()
    assert(grid.AbsoluteCellCount == Vector2.new(4, 2))
    assert(panel.AbsoluteSize.Y == 85)
    panel.Size = UDim2.fromOffset(160, 0)
    assert(grid.AbsoluteCellCount == Vector2.new(3, 3))
    assert(panel.AbsoluteSize.Y == 130)
    grid.StartCorner = Enum.StartCorner.BottomRight
    assert(panel.Slot1.AbsolutePosition - panel.AbsolutePosition == Vector2.new(110, 90))
    assert(grid.AbsoluteCellSize == Vector2.new(50, 40))
    return grid.AbsoluteCellCount
end)
screen:Destroy()
assert(ok, result)
return result
