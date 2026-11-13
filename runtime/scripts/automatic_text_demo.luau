-- Local GUI demo for the Rust host with installed Roblox font assets.
local core = game:GetService("CoreGui")
local previous = core:FindFirstChild("AutomaticTextDemo")
if previous then previous:Destroy() end

local screen = Instance.new("ScreenGui", core)
screen.Name = "AutomaticTextDemo"
screen.IgnoreGuiInset = true

local panel = Instance.new("Frame", screen)
panel.Position = UDim2.fromOffset(24, 24)
panel.Size = UDim2.fromOffset(320, 0)
panel.AutomaticSize = Enum.AutomaticSize.Y
panel.BackgroundColor3 = Color3.fromRGB(24, 26, 30)
Instance.new("UICorner", panel).CornerRadius = UDim.new(0, 12)

local padding = Instance.new("UIPadding", panel)
padding.PaddingLeft = UDim.new(0, 18)
padding.PaddingRight = UDim.new(0, 18)
padding.PaddingTop = UDim.new(0, 16)
padding.PaddingBottom = UDim.new(0, 16)
local list = Instance.new("UIListLayout", panel)
list.SortOrder = Enum.SortOrder.LayoutOrder
list.Padding = UDim.new(0, 12)

local function text(content, order)
    local label = Instance.new("TextLabel", panel)
    label.Name = "Text" .. order
    label.LayoutOrder = order
    label.Size = UDim2.new(1, 0, 0, 0)
    label.AutomaticSize = Enum.AutomaticSize.Y
    label.BackgroundTransparency = 1
    label.Font = Enum.Font.SourceSans
    label.TextSize = 20
    label.TextColor3 = Color3.fromRGB(235, 237, 240)
    label.TextXAlignment = Enum.TextXAlignment.Left
    label.TextWrapped = true
    label.Text = content
    return label
end

text("automatic text sizing", 1).TextSize = 28
text("This panel grows with its labels. Longer text wraps, and padding stays outside the measured text.", 2)
local rich = text('A <font size="26">larger</font> word changes the line height.', 3)
rich.RichText = true

local button = Instance.new("TextButton", panel)
button.Name = "Close"
button.LayoutOrder = 4
button.Size = UDim2.fromOffset(0, 0)
button.AutomaticSize = Enum.AutomaticSize.XY
button.Font = Enum.Font.SourceSans
button.TextSize = 20
button.Text = "close demo"
button.TextColor3 = Color3.fromRGB(235, 237, 240)
button.BackgroundColor3 = Color3.fromRGB(55, 61, 72)
local buttonPadding = Instance.new("UIPadding", button)
buttonPadding.PaddingLeft = UDim.new(0, 12)
buttonPadding.PaddingRight = UDim.new(0, 12)
buttonPadding.PaddingTop = UDim.new(0, 6)
buttonPadding.PaddingBottom = UDim.new(0, 6)
Instance.new("UICorner", button).CornerRadius = UDim.new(0, 6)
button.Activated:Connect(function() screen:Destroy() end)

return panel.AbsoluteSize
