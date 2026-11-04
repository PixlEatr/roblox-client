local u=game:GetService("UserInputService")
local saved=u.MouseDeltaSensitivity
local result={version=version(),cases={}}
local samples={-5,0,.25,1,10,25,math.huge,-math.huge,0/0,"2",true}
local ok,err=pcall(function()
 for _,value in ipairs(samples)do
  local success,message=pcall(function()u.MouseDeltaSensitivity=value;return u.MouseDeltaSensitivity end)
  table.insert(result.cases,{input=tostring(value),ok=success,value=tostring(message)})
 end
end)
u.MouseDeltaSensitivity=saved
if not ok then result.error=tostring(err)end
return game:GetService("HttpService"):JSONEncode(result)
