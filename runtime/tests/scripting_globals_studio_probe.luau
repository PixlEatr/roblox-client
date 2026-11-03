-- Pure scripting reference: no DataModel changes.
local out={version=version(),calls={},cancel={},memory={}}
out.pcallAlias=ypcall==pcall
local completed=0
out.spawnReturns=table.pack(spawn(function(...)out.calls.spawn=table.pack(...);completed+=1 end,7,nil,9)).n
out.delayReturns=table.pack(delay(.01,function(...)out.calls.delay=table.pack(...);completed+=1 end,4,nil,6)).n
task.wait(.15);out.completed=completed
for _,kind in ipairs({"suspended","dead","running"})do
 local thread=coroutine.create(function()end)
 if kind=="dead" then coroutine.resume(thread)elseif kind=="running"then thread=coroutine.running()end
 local ok,result=pcall(task.cancel,thread)
 out.cancel[kind]={ok,tostring(result),coroutine.status(thread)}
end
for _,value in ipairs({"Scripting Reference",17,true,{}})do
 local ok,result=pcall(debug.setmemorycategory,value)
 table.insert(out.memory,{kind=typeof(value),ok=ok})
 debug.resetmemorycategory()
end
out.timeKind=typeof(time())
return game:GetService("HttpService"):JSONEncode(out)
