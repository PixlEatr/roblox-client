local arcs=Instance.new("ArcHandles")
arcs.Axes=Axes.new(Enum.Axis.X,Enum.NormalId.Top)

local handles=Instance.new("Handles")
handles.Faces=Faces.new(Enum.NormalId.Front,Enum.NormalId.Left)

local copied=handles:Clone()
assert(copied.Faces==handles.Faces)
local x,y,z,front=arcs.Axes.X,arcs.Axes.Y,arcs.Axes.Z,copied.Faces.Front
arcs:Destroy();handles:Destroy();copied:Destroy()
return x,y,z,front
