-- This example manages local client objects; it sends no gameplay packets.
local folder = Instance.new("Folder", workspace)
folder.Name = "__HeadlessEventsExample"
local channel = Instance.new("BindableEvent", folder)
channel.Name = "Ready"

local added = 0
local attributeChanges = 0
local childConnection = folder.ChildAdded:Connect(function()
    added += 1
end)

local part = Instance.new("Part", folder)
part.Name = "Original"
part.Anchored = true
part:GetAttributeChangedSignal("Status"):Connect(function()
    attributeChanges += 1
end)
part:SetAttribute("Status", "created")
part:SetAttribute("Status", "ready")
part:SetAttribute("Owner", folder)

local copy = part:Clone()
copy.Name = "Copy"
copy.Parent = folder
assert(copy:GetAttribute("Status") == "ready")
assert(copy:GetAttribute("Owner"):Get() == folder)

task.delay(0.01, function()
    channel:Fire("ready", {message = "scheduled source code ran"})
end)
local state, payload = channel.Event:Wait()
assert(state == "ready")
assert(payload.message == "scheduled source code ran")

local destroyed = false
local destroyConnection = copy.Destroying:Once(function()
    destroyed = true
end)
copy:Destroy()
task.wait(0.01)
assert(destroyed and not destroyConnection.Connected)
assert(attributeChanges == 2)
assert(added == 2)

print("Local child events:", added)
print("Attribute events:", attributeChanges)
print("Scheduled bindable:", state)
childConnection:Disconnect()
folder:Destroy()
return added, attributeChanges, destroyed, state
