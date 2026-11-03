-- Local authored motion: carry the returned velocity into the next step.
local service=game:GetService("TweenService")
local part=Instance.new("Part")
part.Anchored=true
part.Position=Vector3.zero
part.Parent=workspace
local changes=0
local connection=part:GetPropertyChangedSignal("Position"):Connect(function()changes+=1 end)
local target=Vector3.new(8,3,-4)
local velocity=Vector3.zero
for _=1,90 do
    local position
    position,velocity=service:SmoothDamp(part.Position,target,velocity,.15,30,1/60)
    part.Position=position
    task.wait(.005)
end
local error=(part.Position-target).Magnitude
assert(part.Position:FuzzyEq(target,.001),"smoothing did not converge")
assert(CFrame.new(part.Position):FuzzyEq(CFrame.new(target),.001))
connection:Disconnect()
part:Destroy()
return "Smoothed",changes,error
