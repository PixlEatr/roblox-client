-- Calendar conversion, module execution, and task scheduling in the real VM.
local module=Instance.new("ModuleScript")
module.Source=[[
local value={}
function value.toLocalTimestamp(t)
 return DateTime.fromLocalTime(t.Year,t.Month,t.Day,t.Hour,t.Minute,t.Second,t.Millisecond)
end
return value
]]
local calendar=require(module)
local now=DateTime.now()
local roundtrip=calendar.toLocalTimestamp(now:ToLocalTime())==now
local offset=DateTime.fromIsoDate("2026-11-02T12:00:00+05:45")
assert(offset:ToIsoDate()=="2026-11-02T06:15:00Z")
local completed=false
task.delay(.01,function()
 local stamp=DateTime.now()
 completed=stamp.UnixTimestampMillis>=now.UnixTimestampMillis
end)
for _=1,100 do if completed then break end;task.wait(.005)end
module:Destroy()
return "DateTime scripting completed",roundtrip,completed
