local run=game:GetService("RunService")
local camera=workspace.CurrentCamera
local original=camera.CFrame
local position,elapsed,frames=0,0,0
run:BindToRenderStep("DemoInput",Enum.RenderPriority.Input.Value,function(dt)
    assert(dt>0);elapsed+=dt;position+=dt*2
end)
run:BindToRenderStep("DemoCamera",Enum.RenderPriority.Camera.Value,function(dt)
    camera.CFrame=CFrame.lookAt(Vector3.new(position,3,10),Vector3.new(position,3,0))
end)
run:BindToRenderStep("DemoObserve",Enum.RenderPriority.Last.Value,function(dt)
    assert(math.abs(camera.CFrame.X-position)<1e-5)
    assert(camera.CFrame.LookVector:FuzzyEq(Vector3.new(0,0,-1)))
    frames+=1
end)
while frames<5 do run.PreRender:Wait()end
for _,name in ipairs({"DemoInput","DemoCamera","DemoObserve"})do run:UnbindFromRenderStep(name)end
camera.CFrame=original
return "Render-step camera updated",frames,elapsed
