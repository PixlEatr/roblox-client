local h=game:GetService("HttpService")
local out={version=version(),iso={},localTime={}}
for _,s in ipairs({
"2020-01-02T03:04:05+00:00","2020-01-02T03:04:05-07:30",
"2020-01-02T03:04:05.123456Z","2020-01-02T03:04:05.9999+05:45",
"2020-01-02T03:04:05","2020-01-02","2020-01-02t03:04:05z",
"2020-01-02T03:04:05+24:00","2020-01-02T03:04:05+01:60",
"2020-01-02T24:00:00Z","2020-01-02T03:04:60Z",
"2020-01-02T03:04:05+0730","2020-01-02T03:04:05.1Z",
"2020-01-02T03:04:05.Z","2020-01-02T03:04:05Zextra",
"1399-12-31T23:59:59-01:00","9999-12-31T23:59:59-01:00"})do
local ok,v=pcall(DateTime.fromIsoDate,s)
table.insert(out.iso,{input=s,ok=ok,millis=ok and v and v.UnixTimestampMillis or false})
end
for _,args in ipairs({{}, {1970,1,1},{2026,1,15,12,34,56,789},{2026,7,15,12,34,56,789},
{2026,3,8,2,30},{2026,11,1,1,30},{2000,2,29,24,90,-10,-1},{1900,2,29},
{1400,1,1},{9999,12,31,23,59,59,999}})do
local ok,v=pcall(function()local d=DateTime.fromLocalTime(unpack(args));return {millis=d.UnixTimestampMillis,components=d:ToLocalTime()}end)
table.insert(out.localTime,{args=args,ok=ok,millis=ok and v.millis or false,components=ok and v.components or false})
end
for _,ms in ipairs({-1,0,1793639017123,-17987443200000,253402300799999})do
local d=DateTime.fromUnixTimestampMillis(ms)
local ok,v=pcall(function()return d:ToLocalTime()end);out["local"..tostring(ms)]={ok=ok,value=ok and v or tostring(v)}
end
return h:JSONEncode(out)
