-- Offline preview: python tests/preview.py --clipping
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"))
screen.Name="ClippingDemo";screen.IgnoreGuiInset=true
local function frame(name,parent,x,y,w,h,color)
    local f=Instance.new("Frame",parent);f.Name=name
    f.Position=UDim2.fromOffset(x,y);f.Size=UDim2.fromOffset(w,h)
    f.BackgroundColor3=color;return f
end
local panel=frame("ClipPanel",screen,40,40,220,160,Color3.fromRGB(35,49,63))
panel.ClipsDescendants=true
local button=Instance.new("TextButton",panel);button.Name="PartialButton"
button.Position=UDim2.fromOffset(170,50);button.Size=UDim2.fromOffset(140,50)
button.BackgroundColor3=Color3.fromRGB(70,185,140);button.Text="Click";button.TextSize=16
button.Activated:Connect(function()button.Text="Clicked" end)
local inner=frame("InnerClip",panel,20,100,100,40,Color3.fromRGB(70,80,95));inner.ClipsDescendants=true
frame("NestedOverflow",inner,70,10,90,70,Color3.fromRGB(235,165,60))
local scroll=Instance.new("ScrollingFrame",screen);scroll.Name="ScrollClip"
scroll.Position=UDim2.fromOffset(300,40);scroll.Size=UDim2.fromOffset(220,160)
scroll.BackgroundColor3=Color3.fromRGB(35,49,63)
scroll.CanvasSize=UDim2.fromOffset(220,400);scroll.CanvasPosition=Vector2.new(0,55)
local grid=Instance.new("UIGridLayout",scroll)
grid.CellSize=UDim2.fromOffset(90,60);grid.CellPadding=UDim2.fromOffset(10,10)
for i=1,10 do frame("Cell"..i,scroll,0,0,90,60,Color3.fromRGB(55+i*10,100,160)) end
local hidden=Instance.new("TextButton",panel);hidden.Name="HiddenButton"
hidden.Size=UDim2.fromOffset(100,40);hidden.Position=UDim2.fromOffset(0,220);hidden.Text="Hidden"
return "Clipping fixture ready"
