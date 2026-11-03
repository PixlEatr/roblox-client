local h=game:GetService("HttpService")
local out={constructors={},operations={},attributes={}}
local args={{},{1},{1.9,-2.9,3.9},{32768,65535,-32769},{65536,-65536,1},{true,2,3},{"4",2,3},{math.huge,0/0,-math.huge}}
for _,name in ipairs({"Vector2int16","Vector3int16"})do local ctor=name=="Vector2int16" and Vector2int16.new or Vector3int16.new
local rows={}
for _,a in ipairs(args)do local ok,v=pcall(ctor,unpack(a));table.insert(rows,{ok=ok,x=ok and v.X or false,y=ok and v.Y or false,z=ok and name=="Vector3int16" and v.Z or false,text=ok and tostring(v) or false})end
out.constructors[name]=rows
local a=ctor(7,-8,9);local b=ctor(2,3,-4)
for i,f in ipairs({function()return a+b end,function()return a-b end,function()return a*b end,function()return a/b end,function()return a*2.5 end,function()return 2.5*a end,function()return a/2.5 end,function()return -a end,function()return a.x end,function()return a.Magnitude end,function()return a*32768 end,function()return a*"2" end,function()return a+Vector3.new(1,2,3)end,function()return a/0 end})do
local ok,v=pcall(f);table.insert(out.operations,{name=name,case=i,ok=ok,text=ok and tostring(v) or false})end
local folder=Instance.new("Folder")
out.attributes[name]=pcall(function()folder:SetAttribute("integerVector",a)end)
folder:Destroy()
end
return h:JSONEncode(out)
