-- Start with --early-script in persistent mode. This code stays alive while
-- the network controller receives subsequent world changes.
assert(client.persistent and not client.snapshot)
local player = game:GetService("Players").LocalPlayer
local observed = {
    player = player,
    ticks = 0,
    characterAdded = 0,
    remoteEvents = 0,
    remotesWatched = 0,
    alreadyPresent = false,
}
getgenv().headlessObserved = observed

player.CharacterAdded:Connect(function(character)
    assert(character and character:IsA("Model"))
    observed.character = character
    observed.characterAdded += 1
    print("Server character event:", character.ClassName)
end)
local ok, current = pcall(function() return player.Character end)
if ok and current then
    observed.character = current
    observed.alreadyPresent = true
end

local watching = {}
local function watch(instance)
    if (instance:IsA("RemoteEvent") or instance:IsA("UnreliableRemoteEvent")) and not watching[instance] then
        watching[instance] = instance.OnClientEvent:Connect(function(...)
            observed.remoteEvents += 1
            observed.lastArgumentCount = select("#", ...)
        end)
        observed.remotesWatched += 1
    end
end
for _, instance in game:GetDescendants() do watch(instance) end
game.DescendantAdded:Connect(watch)

getgenv().headlessHeartbeat = task.spawn(function()
    while true do
        observed.ticks += 1
        task.wait(0.1)
    end
end)
print("Persistent watcher armed")
return "watcher armed", observed.alreadyPresent, observed.remotesWatched
