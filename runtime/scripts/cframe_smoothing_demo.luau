-- CFrame velocity is an opaque payload: carry it between calls unchanged.
local service=game:GetService("TweenService")
local part=Instance.new("Part")
part.Anchored=true
part.CFrame=CFrame.identity
part.Parent=workspace
local changes=0
local connection=part:GetPropertyChangedSignal("CFrame"):Connect(function()changes+=1 end)
local target=CFrame.new(8,3,-4)*CFrame.Angles(.4,1.5,-.2)
local velocity=0
for _=1,120 do
    part.CFrame,velocity=service:SmoothDamp(part.CFrame,target,velocity,.18,30,1/60)
    task.wait(.005)
end
local positionError=(part.Position-target.Position).Magnitude
local angleError=part.CFrame:AngleBetween(target)
assert(positionError<.001 and angleError<.001,"CFrame smoothing did not converge")
connection:Disconnect()
part:Destroy()
return "CFrame smoothed",changes,positionError,angleError
