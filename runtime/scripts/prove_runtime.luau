-- Run in the headless client's console. These commands run in this script.
local commands = {}

function commands.whoami()
    local player = assert(game:GetService("Players").LocalPlayer, "Player not replicated yet")
    print("Player:", player.Name, "UserId:", player.UserId)
end

function commands.character()
    local player = assert(game:GetService("Players").LocalPlayer, "Player not replicated yet")
    local character = assert(player.Character, "Character not replicated yet")
    local root = assert(character:FindFirstChild("HumanoidRootPart"), "Root not replicated yet")
    print("Character:", character.Name, "Descendants:", #character:GetDescendants())
    print("Root anchored:", root.Anchored, "Origin:", client.propertyOrigin(root, "Anchored"))
end

function commands.objects()
    local folder = Instance.new("Folder", workspace)
    folder.Name = "RuntimeProof"
    local ok, message = pcall(function()
        local part = Instance.new("Part", folder)
        part.Name = "TestPart"
        part.Anchored = true
        part:SetAttribute("Proof", "Luau ran")
        local copy = part:Clone()
        copy.Name = "Copy"
        copy.Parent = folder
        assert(folder:FindFirstChild("Copy") == copy)
        assert(copy.Anchored and copy:GetAttribute("Proof") == "Luau ran")

        local event = Instance.new("BindableEvent", folder)
        local received = false
        event.Event:Connect(function(text, number)
            received = text == "hello" and number == 42
        end)
        event:Fire("hello", 42)
        task.wait(0.01)
        assert(received, "Event did not fire")
        print("Created, cloned, read attributes, and fired an event successfully")
    end)
    folder:Destroy()
    assert(ok, message)
end

local passed, failed = 0, 0
for _, name in {"whoami", "character", "objects"} do
    print(">", name)
    local ok, message = pcall(commands[name])
    if ok then
        passed += 1
        print("PASS:", name)
    else
        failed += 1
        warn("FAIL: " .. name .. ": " .. tostring(message))
    end
end
print("Finished:", passed, "passed,", failed, "failed")
print("Object tests are local to this client; they are not server-visible.")
return passed, failed
