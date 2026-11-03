-- Fresh controls now carry native Size and SliceCenter defaults.
local service=game:GetService("TweenService")
local frame=Instance.new("Frame")
local image=Instance.new("ImageLabel",frame)
assert(frame.Size==UDim2.new() and image.SliceCenter==Rect.new())
local sizeChanges,rectChanges=0,0
frame:GetPropertyChangedSignal("Size"):Connect(function()sizeChanges+=1 end)
image:GetPropertyChangedSignal("SliceCenter"):Connect(function()rectChanges+=1 end)
local a=service:Create(frame,TweenInfo.new(.12,Enum.EasingStyle.Quad),{
    Size=UDim2.fromOffset(240,90),Visible=false,
})
local goal=Rect.new(8,8,32,32)
local b=service:Create(image,TweenInfo.new(.18,Enum.EasingStyle.Linear),{
    Size=UDim2.fromOffset(64,64),SliceCenter=goal,SliceScale=2,
})
a:Play();b:Play()
if a.PlaybackState~=Enum.PlaybackState.Completed then a.Completed:Wait()end
if b.PlaybackState~=Enum.PlaybackState.Completed then b.Completed:Wait()end
assert(frame.Size==UDim2.fromOffset(240,90) and frame.Visible==false)
assert(image.Size==UDim2.fromOffset(64,64) and image.SliceCenter==goal and image.SliceScale==2)
assert(sizeChanges>1 and rectChanges>1)
a:Destroy();b:Destroy();frame:Destroy()
return "GUI tween completed",sizeChanges,rectChanges
