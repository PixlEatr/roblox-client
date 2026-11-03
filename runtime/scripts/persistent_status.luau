assert(client.persistent and not client.snapshot)
local observed = assert(getgenv().headlessObserved, "run persistent_watch.luau first")
local player = game:GetService("Players").LocalPlayer
-- Streaming callbacks can span several bounded scheduler polls.
for _ = 1, 500 do
    if player.Character and observed.character == player.Character and observed.ticks > 0 then
        break
    end
    task.wait(0.01)
end
assert(player == observed.player, "LocalPlayer identity changed")
local character = assert(player.Character, "Character is not resolved")
assert(character:IsA("Model"))
assert(character == observed.character, "watcher did not receive the character")
assert(observed.characterAdded > 0 or observed.alreadyPresent)
assert(observed.ticks > 0)
print("Persistent ticks:", observed.ticks)
print("Character events:", observed.characterAdded)
print("Received remote events:", observed.remoteEvents)
return observed.ticks, observed.characterAdded, observed.alreadyPresent,
    observed.remoteEvents, player == observed.player, client.sequence
