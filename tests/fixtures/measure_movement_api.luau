-- Run only in a disposable, empty Studio test place from the command bar.
-- All test instances remain under an unparented Model and are destroyed even
-- if a measurement raises. This measures API calls, not a live character.
local HttpService = game:GetService("HttpService")
local created = {}
local report = {
    fixture = "temporary_unparented_humanoid_movement",
    engineOnly = true,
    noWorkspaceParenting = true,
    noPlayerManipulation = true,
    noNetwork = true,
    measurements = {},
}

local function make(className, parent)
    local instance = Instance.new(className)
    table.insert(created, instance)
    if parent then
        instance.Parent = parent
    end
    return instance
end

local function safeCall(callback)
    local ok, value = pcall(callback)
    if not ok then
        return { ok = false, error = tostring(value) }
    end
    return { ok = true, value = value }
end

local function vector(value)
    return { value.X, value.Y, value.Z }
end

local function capture(name, callback)
    report.measurements[name] = safeCall(callback)
end

local ok, failure = xpcall(function()
    local model = make("Model")
    local humanoid = make("Humanoid", model)
    local root = make("Part", model)
    root.Name = "TemporaryMovementRoot"
    root.Anchored = true
    root.Position = Vector3.new(0, 4, 0)
    report.fixtureInstances = {
        modelParentIsNil = model.Parent == nil,
        humanoidParentIsTemporaryModel = humanoid.Parent == model,
        rootParentIsTemporaryModel = root.Parent == model,
        rootIsAnchored = root.Anchored,
        humanoidWalkSpeed = humanoid.WalkSpeed,
        humanoidMoveDirectionBefore = vector(humanoid.MoveDirection),
    }

    capture("Humanoid_Move_valid_vector", function()
        humanoid:Move(Vector3.new(0.25, 0, -1))
        return {
            moveDirectionImmediatelyAfter = vector(humanoid.MoveDirection),
            parentStillUnparented = model.Parent == nil,
        }
    end)
    capture("Humanoid_Move_zero_vector", function()
        humanoid:Move(Vector3.zero)
        return vector(humanoid.MoveDirection)
    end)
    capture("Humanoid_Move_wrong_vector_type", function()
        humanoid:Move("not a Vector3")
        return "accepted"
    end)
    capture("Humanoid_Move_wrong_camera_flag_type", function()
        humanoid:Move(Vector3.xAxis, "not a boolean")
        return "accepted"
    end)

    local target = Vector3.new(12, 3, -4)
    capture("Humanoid_MoveTo_vector", function()
        humanoid:MoveTo(target)
        return {
            walkToPoint = vector(humanoid.WalkToPoint),
            walkToPartIsNil = humanoid.WalkToPart == nil,
            moveDirectionImmediatelyAfter = vector(humanoid.MoveDirection),
        }
    end)
    capture("Humanoid_MoveTo_vector_and_temporary_part", function()
        humanoid:MoveTo(target, root)
        return {
            walkToPoint = vector(humanoid.WalkToPoint),
            walkToPartIsTemporaryRoot = humanoid.WalkToPart == root,
        }
    end)
    capture("Humanoid_MoveTo_wrong_argument_type", function()
        humanoid:MoveTo("not a Vector3")
        return "accepted"
    end)
    capture("Humanoid_MoveTo_wrong_second_argument_type", function()
        humanoid:MoveTo(target, "not an Instance")
        return "accepted"
    end)

    -- Player is a service-managed class on some engine builds. Attempt
    -- construction inside pcall and call Move only if the engine allows it.
    capture("Player_constructibility", function()
        local player = make("Player")
        return {
            created = true,
            parentIsNil = player.Parent == nil,
            hasMove = typeof(player.Move) == "function",
        }
    end)
    local playerResult = report.measurements.Player_constructibility
    if playerResult and playerResult.ok and playerResult.value.created then
        local player
        for _, instance in created do
            if instance.ClassName == "Player" then
                player = instance
                break
            end
        end
        if player then
            capture("Player_Move_valid_vector", function()
                player:Move(Vector3.new(1, 0, 0), false)
                return "accepted"
            end)
            capture("Player_Move_wrong_vector_type", function()
                player:Move("not a Vector3", false)
                return "accepted"
            end)
        end
    else
        report.measurements.Player_Move_valid_vector = {
            ok = false,
            skipped = true,
            reason = "Player could not be constructed as an unparented fixture",
        }
    end

    report.observations = {
        rootWorldPositionUnchanged = root.Position == Vector3.new(0, 4, 0),
        modelParentStillNil = model.Parent == nil,
        note = "No heartbeat/physics step was run; this does not measure server replication.",
    }
end, debug.traceback)

for index = #created, 1, -1 do
    local instance = created[index]
    pcall(function()
        instance.Parent = nil
        instance:Destroy()
    end)
end

if not ok then
    report.fixtureError = tostring(failure)
end
report.cleanupComplete = true
print("MOVEMENT_FIXTURE_JSON=" .. HttpService:JSONEncode(report))
return report
