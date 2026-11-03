-- Focus the browser's input area. Hold right mouse and drag to turn;
-- wheel moves the camera forward/back. This updates the actual local Camera
-- with CFrame smoothing; it does not render a 3D view.
-- Stop and restore the original pose: shared.mouseCameraControls.stop()
local actions = game:GetService("ContextActionService")
local run = game:GetService("RunService")
local tween = game:GetService("TweenService")
local camera = assert(workspace.CurrentCamera, "CurrentCamera is not ready")
if shared.mouseCameraControls and type(shared.mouseCameraControls.stop) == "function" then
    shared.mouseCameraControls.stop()
end
local original = camera.CFrame
local position = original.Position
local facing = original.LookVector
local yaw = math.atan2(-facing.X, -facing.Z)
local pitch = math.asin(math.clamp(facing.Y, -1, 1))
local target = original
local current = original
local velocity = 0
local looking = false
local stopped = false
local prefix = "BrowserMouseCamera_" .. game:GetService("HttpService"):GenerateGUID(false)
local names = {prefix .. "look", prefix .. "motion", prefix .. "wheel"}
local function updateTarget()
    target = CFrame.new(position) * CFrame.fromOrientation(pitch, yaw, 0)
end
local controls = {target = function() return target end}
function controls.stop()
    if stopped then return end
    stopped = true
    looking = false
    run:UnbindFromRenderStep(prefix)
    for _, name in ipairs(names) do actions:UnbindAction(name) end
    camera.CFrame = original
end
local ok, message = pcall(function()
    actions:BindAction(names[1], function(_, state)
        if not stopped then looking = state == Enum.UserInputState.Begin end
        return Enum.ContextActionResult.Sink
    end, false, Enum.UserInputType.MouseButton2)
    actions:BindAction(names[2], function(_, state, input)
        if stopped or not looking then return Enum.ContextActionResult.Pass end
        if state == Enum.UserInputState.Change then
            yaw -= input.Delta.X * .003
            pitch = math.clamp(pitch - input.Delta.Y * .003, -math.rad(80), math.rad(80))
            updateTarget()
        end
        return Enum.ContextActionResult.Sink
    end, false, Enum.UserInputType.MouseMovement)
    actions:BindAction(names[3], function(_, state, input)
        if not stopped and state == Enum.UserInputState.Change then
            position += target.LookVector * input.Position.Z * 2
            updateTarget()
        end
        return Enum.ContextActionResult.Sink
    end, false, Enum.UserInputType.MouseWheel)
    run:BindToRenderStep(prefix, Enum.RenderPriority.Camera.Value, function(dt)
        current, velocity = tween:SmoothDamp(current, target, velocity, .08, math.huge, dt)
        camera.CFrame = current
    end)
end)
if not ok then controls.stop(); error(message, 0) end
shared.mouseCameraControls = controls
return "Mouse camera ready: right-drag to turn, wheel to move. Stop: shared.mouseCameraControls.stop()"
