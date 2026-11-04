local rs=game:GetService("ReflectionService")
local out={version=version(),source="Studio ReflectionService and fresh unparented Instance.new fixtures",classes={}}
local function encode(v)
    local t=typeof(v)
    if t=="nil" then return {value={["$nil"]=true}} end
    if t=="boolean" or t=="string" then return {value=v} end
    if t=="number" then
        if v~=v then return {value={["$number"]="nan"}} end
        if v==math.huge then return {value={["$number"]="inf"}} end
        if v==-math.huge then return {value={["$number"]="-inf"}} end
        return {value=v}
    end
    if t=="Vector3" then return {value={["$vector3"]={v.X,v.Y,v.Z}}} end
    if t=="CFrame" then
        local c={v:GetComponents()}
        return {value={["$cframe"]={position={c[1],c[2],c[3]},rotation={c[4],c[5],c[6],c[7],c[8],c[9],c[10],c[11],c[12]}}}}
    end
    return nil
end
local list=table.clone(rs:GetClasses())
table.sort(list,function(a,b)return a.Name<b.Name end)
for i,c in list do
    local entry={parent=c.Superclass,creatable=false,properties={}}
    out.classes[c.Name]=entry
    if c.Permits.New~=nil then
        local ok,obj=pcall(Instance.new,c.Name)
        if ok then
            entry.creatable=true
            entry.name=obj.Name
            for _,p in rs:GetPropertiesOfClass(c.Name) do
                if p.Name~="Name" and p.Name~="Parent" and p.Name~="ClassName" and p.Name~="className" then
                    local read,value=pcall(function()return obj[p.Name]end)
                    local encoded=read and encode(value) or nil
                    if encoded then
                        entry.properties[p.Name]={type=p.Type.ScriptType,engine_type=p.Type.EngineType,writable=p.Permits.Write~=nil,value=encoded.value}
                    end
                end
            end
            obj:Destroy()
        end
    end
    if i%25==0 then task.wait() end
end
return game:GetService("HttpService"):JSONEncode(out)
