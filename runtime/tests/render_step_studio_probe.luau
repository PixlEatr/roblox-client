-- Temporary RunService bindings are always removed before returning.
local r=game:GetService("RunService")
local p="CodexRenderReference_"..game:GetService("HttpService"):GenerateGUID(false)
local out={version=version(),priorities={},ordered={},dynamic={},yielding={}}
for _,item in ipairs(Enum.RenderPriority:GetEnumItems())do table.insert(out.priorities,{item.Name,item.Value})end
local names={}
local function bind(suffix,priority,fn)names[#names+1]=p..suffix;r:BindToRenderStep(p..suffix,priority,fn)end
local function clear()for _,name in ipairs(names)do r:UnbindFromRenderStep(name)end;names={}end
local pre,old
local ok,message=pcall(function()
 local function log(name,dt)table.insert(out.ordered,{name,dt})end
 pre=r.PreRender:Connect(function(dt)log("PreRender",dt)end)
 old=r.RenderStepped:Connect(function(dt)log("RenderStepped",dt)end)
 bind("Dup",100,function(dt)log("first",dt)end)
 bind("Dup",200,function(dt)log("second",dt)end)
 task.wait(.13);clear();pre:Disconnect();old:Disconnect()
 local first=true
 bind("removed",400,function(dt)table.insert(out.dynamic,{"removed",dt})end)
 bind("main",200,function(dt)
  table.insert(out.dynamic,{"main",dt})
  if first then first=false
   r:UnbindFromRenderStep(p.."removed")
   bind("low",100,function(d)table.insert(out.dynamic,{"low",d})end)
   bind("same",200,function(d)table.insert(out.dynamic,{"same",d})end)
   bind("high",300,function(d)table.insert(out.dynamic,{"high",d})end)
  end
 end)
 task.wait(.18);clear()
 local once=true
 bind("yield",100,function(dt)
  table.insert(out.yielding,{"start",dt})
  if once then once=false;task.wait(.08);table.insert(out.yielding,{"finish",dt})end
 end)
 bind("later",200,function(dt)table.insert(out.yielding,{"later",dt})end)
 task.wait(.2);clear()
end)
clear();if pre then pre:Disconnect()end;if old then old:Disconnect()end
out.ok=ok;out.error=message and tostring(message) or ""
return game:GetService("HttpService"):JSONEncode(out)
