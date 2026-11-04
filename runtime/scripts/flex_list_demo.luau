-- Fixed labels around a flexible control. Calculates local scripting geometry.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui", playerGui)
screen.Name = "FlexListDemo"
local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(360, 48)
    local layout = Instance.new("UIListLayout", panel)
    layout.FillDirection = Enum.FillDirection.Horizontal
    layout.VerticalFlex = Enum.UIFlexAlignment.Fill
    layout.SortOrder = Enum.SortOrder.LayoutOrder
    layout.Padding = UDim.new(0, 10)
    local label = Instance.new("TextLabel", panel)
    label.Size = UDim2.fromOffset(80, 20)
    label.Text = "Volume"
    local control = Instance.new("Frame", panel)
    control.Size = UDim2.fromOffset(100, 20)
    control.LayoutOrder = 1
    local value = Instance.new("TextLabel", panel)
    value.Size = UDim2.fromOffset(40, 20)
    value.LayoutOrder = 2
    value.Text = "50%"
    local flex = Instance.new("UIFlexItem", control)
    flex.FlexMode = Enum.UIFlexMode.Fill
    local bounds = Instance.new("UISizeConstraint", control)
    bounds.MinSize = Vector2.new(60, 0)
    bounds.MaxSize = Vector2.new(400, math.huge)
    assert(control.AbsoluteSize == Vector2.new(220, 48))
    assert(label.AbsoluteSize == Vector2.new(80, 48))
    panel.Size = UDim2.fromOffset(220, 48)
    assert(control.AbsoluteSize == Vector2.new(80, 48))
    panel.Size = UDim2.fromOffset(900, 48)
    assert(control.AbsoluteSize == Vector2.new(400, 48))
    assert(control.Size.X.Offset == 100)
    assert(layout.AbsoluteContentSize == Vector2.new(540, 48))
    return layout.AbsoluteContentSize
end)
screen:Destroy()
assert(ok, result)
return result
