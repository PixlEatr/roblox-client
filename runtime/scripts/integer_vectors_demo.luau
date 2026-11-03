-- A ModuleScript computes a grid region using the immutable integer types.
local module=Instance.new("ModuleScript")
module.Source=[[
local grid={}
function grid.cell(x,y,z)return Vector3int16.new(x,y,z)end
function grid.bounds(a,b)return Region3int16.new(a,b)end
function grid.position(cell)return Vector3.new(cell.X,cell.Y,cell.Z)end
return grid
]]
local grid=require(module)
local origin=grid.cell(10,5,-8)
local nextCell=origin+grid.cell(2,1,-1)
local region=grid.bounds(origin,nextCell)
assert(region.Min==origin and region.Max==nextCell)
assert(grid.position(nextCell)==Vector3.new(12,6,-9))
module:Destroy()
return "Integer vector scripting completed",nextCell
