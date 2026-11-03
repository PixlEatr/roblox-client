local TweenService = game:GetService("TweenService")
local part = Instance.new("Part")
part.Name = "LocalTweenDemo"
part.Anchored = true
part.Parent = workspace

local tween = TweenService:Create(part,
    TweenInfo.new(.25, Enum.EasingStyle.Quad, Enum.EasingDirection.Out, 0, true), {
        CFrame = CFrame.new(4, 3, 0) * CFrame.Angles(0, math.pi / 2, 0),
        Color = Color3.new(.2, .6, 1),
        Transparency = .4,
    })
local changes = 0
part:GetPropertyChangedSignal("CFrame"):Connect(function()
    changes += 1
end)
tween:Play()
local state = tween.Completed:Wait()
assert(state == Enum.PlaybackState.Completed)
assert(changes > 1, "Tween did not update its target over time")
part:Destroy()
tween:Destroy()
return state.Name, changes
