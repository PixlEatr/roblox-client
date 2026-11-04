-- Requires a local display surface and a PlayerGui in the world snapshot.
local player = game:GetService("Players").LocalPlayer
local playerGui = assert(player:FindFirstChildOfClass("PlayerGui"), "PlayerGui required")
local screen = Instance.new("ScreenGui")
screen.Name = "AspectLayoutDemo"
screen.Parent = playerGui

local ok, result = pcall(function()
    local panel = Instance.new("Frame", screen)
    panel.Size = UDim2.fromOffset(400, 300)
    local tile = Instance.new("Frame", panel)
    tile.Size = UDim2.fromOffset(0, 100)
    tile.Position = UDim2.fromScale(.5, .5)
    tile.AnchorPoint = Vector2.new(.5, .5)
    local aspect = Instance.new("UIAspectRatioConstraint", tile)
    aspect.AspectType = Enum.AspectType.ScaleWithParentSize
    aspect.DominantAxis = Enum.DominantAxis.Height
    aspect.AspectRatio = 2
    assert(tile.AbsoluteSize == Vector2.new(200, 100))

    tile.Size = UDim2.fromOffset(0, 500)
    assert(tile.AbsoluteSize == Vector2.new(400, 200))
    panel.Size = UDim2.fromOffset(160, 300)
    assert(tile.AbsoluteSize == Vector2.new(160, 80))
    return tile.AbsoluteSize
end)
screen:Destroy()
assert(ok, result)
return result
