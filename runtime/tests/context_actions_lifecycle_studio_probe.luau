local cas=game:GetService("ContextActionService")
local p="__CodexActionLifecycle_"..game:GetService("HttpService"):GenerateGUID(false)
local names={p.."a",p.."b",p.."c"};local out={calls={},steps={}}
local function callback(label)return function(name,state,input)
 table.insert(out.calls,{label=label,state=tostring(state),key=tostring(input.KeyCode),inputType=tostring(input.UserInputType),objectState=tostring(input.UserInputState)})
end end
local function snap(label)
 local rows={}
 for i,n in ipairs(names)do local v=cas:GetBoundActionInfo(n);rows[i]={priority=v.priorityLevel,stack=v.stackOrder}end
 table.insert(out.steps,{name=label,rows=rows})
end
local ok,err=pcall(function()
 for i,n in ipairs(names)do cas:BindAction(n,callback("old"..i),false,Enum.KeyCode.W)end
 snap("initial")
 cas:BindAction(names[1],callback("replacement"),false,Enum.KeyCode.A);snap("rebind")
 cas:UnbindAction(names[2]);snap("remove_middle")
 cas:BindAction(names[2],callback("new2"),false,Enum.KeyCode.S);snap("readd")
end)
for _,n in ipairs(names)do cas:UnbindAction(n)end
if not ok then out.error=tostring(err)end
return game:GetService("HttpService"):JSONEncode(out)
