-- just a small offline check so you know the vm is alive
local world = game:GetService("Workspace")
print("hello from rove")
print("world:", world.Name)
print("instances:", #world:GetDescendants())
return true
