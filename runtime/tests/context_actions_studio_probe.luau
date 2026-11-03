local cas=game:GetService("ContextActionService")
local prefix="__CodexActionProbe_"..game:GetService("HttpService"):GenerateGUID(false)
local names={};local function key(n)local v=prefix..n;table.insert(names,v);return v end
local a,b,c=key("a"),key("b"),key("c")
local function simplify(info)
 local result={}
 for k,v in pairs(info)do
  if k=="inputTypes"then
   local items={};for _,x in ipairs(v)do table.insert(items,{kind=typeof(x),name=tostring(x)})end;result[k]=items
  elseif typeof(v)=="EnumItem"then result[k]=tostring(v)
  elseif typeof(v)=="function"then result[k]="function"
  else result[k]=v end
 end
 return result
end
local result={version=version(),enums={},checks={}}
local ok,err=pcall(function()
 for _,name in ipairs({"ContextActionResult","ContextActionPriority","PlayerActions"})do
  local items={};for _,item in ipairs(Enum[name]:GetEnumItems())do table.insert(items,{name=item.Name,value=item.Value})end;result.enums[name]=items
 end
 cas:BindAction(a,function()end,false,Enum.KeyCode.W,Enum.UserInputType.MouseButton1)
 result.first=simplify(cas:GetBoundActionInfo(a))
 cas:BindActionAtPriority(b,function()return Enum.ContextActionResult.Pass end,false,3000,Enum.KeyCode.W)
 result.second=simplify(cas:GetBoundActionInfo(b))
 cas:SetTitle(a,"Probe title");cas:SetDescription(a,"Probe description");cas:SetImage(a,"rbxassetid://123")
 result.labeled=simplify(cas:GetBoundActionInfo(a))
 cas:BindAction(a,function()end,false,Enum.KeyCode.A);result.rebound=simplify(cas:GetBoundActionInfo(a))
 local checks={
 {"missingInfo",function()return simplify(cas:GetBoundActionInfo(prefix.."missing"))end},
 {"stringInput",function()cas:BindAction(c,function()end,false,"W");return simplify(cas:GetBoundActionInfo(c))end},
 {"numberInput",function()cas:BindAction(c,function()end,false,119);return simplify(cas:GetBoundActionInfo(c))end},
 {"playerAction",function()cas:BindAction(c,function()end,false,Enum.PlayerActions.CharacterForward);return simplify(cas:GetBoundActionInfo(c))end},
 {"badInput",function()cas:BindAction(c,function()end,false,{});return simplify(cas:GetBoundActionInfo(c))end},
 {"priorityEnum",function()cas:BindActionAtPriority(c,function()end,false,Enum.ContextActionPriority.High,Enum.KeyCode.W);return simplify(cas:GetBoundActionInfo(c))end},
 {"noInputs",function()cas:BindAction(c,function()end,false);return simplify(cas:GetBoundActionInfo(c))end},
 {"missingDescription",function()return cas:SetDescription(prefix.."missing","none")end},
 {"callFunctionMember",function()return typeof(cas.CallFunction)end}
 }
 for _,entry in ipairs(checks)do local success,value=pcall(entry[2]);table.insert(result.checks,{name=entry[1],ok=success,value=value})end
end)
for _,name in ipairs(names)do cas:UnbindAction(name)end
if not ok then result.error=tostring(err)end
return game:GetService("HttpService"):JSONEncode(result)
