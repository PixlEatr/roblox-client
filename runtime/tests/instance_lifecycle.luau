local out={}
local f=Instance.new("Folder")
f.Name="Root"
local p=Instance.new("Part",f);p.Name="PartA"
local skip=Instance.new("Folder",f);skip.Name="Skip";skip.Archivable=false
local skippedChild=Instance.new("Part",skip);skippedChild.Name="SkippedChild"
local a=Instance.new("ObjectValue",f);a.Name="InternalRef";a.Value=p
local b=Instance.new("ObjectValue",f);b.Name="SkippedRef";b.Value=skippedChild
local external=Instance.new("Part")
local c=Instance.new("ObjectValue",f);c.Name="ExternalRef";c.Value=external
local s=Instance.new("ObjectValue",f);s.Name="SelfRef";s.Value=f
f:SetAttribute("Count",5);f:SetAttribute("Vector",Vector3.new(1,2,3))
local copy=f:Clone()
out.cloneInternal=copy:FindFirstChild("InternalRef").Value==copy:FindFirstChild("PartA")
out.cloneSkipped=copy:FindFirstChild("Skip")==nil
out.cloneSkippedRefOriginal=copy:FindFirstChild("SkippedRef").Value==skippedChild
out.cloneExternal=copy:FindFirstChild("ExternalRef").Value==external
out.cloneSelf=copy:FindFirstChild("SelfRef").Value==copy
out.cloneAttrs=copy:GetAttribute("Count")==5 and copy:GetAttribute("Vector")==Vector3.new(1,2,3)
out.cloneUnparented=copy.Parent==nil
f.Archivable=false;out.cloneFalseNil=f:Clone()==nil;f.Archivable=true
f:Destroy()
out.destroyRootParentNil=f.Parent==nil
out.destroyChildParentNil=p.Parent==nil
out.destroyChildrenEmpty=#f:GetChildren()==0
out.destroyNameReadable=f.Name
out.destroyNameWritable=pcall(function() f.Name="AfterDestroy" end)
out.destroyParentNilWritable=pcall(function() f.Parent=nil end)
out.destroyChildParentWritable=pcall(function() p.Parent=copy end)
out.destroyAgain=pcall(function()f:Destroy()end)
out.destroyAttrReadable=f:GetAttribute("Count")==5
out.destroyAttrWritable=pcall(function() f:SetAttribute("Count",6) end)
local cloneDestroyed=f:Clone()
out.cloneDestroyed=cloneDestroyed~=nil and cloneDestroyed.Parent==nil and cloneDestroyed.Name=="AfterDestroy"
if cloneDestroyed then cloneDestroyed:Destroy() end
local child=Instance.new("Folder")
out.parentIntoDestroyed=pcall(function()child.Parent=f end)
out.newIntoDestroyed=pcall(function()Instance.new("Folder",f):Destroy()end)
child:Destroy()
local attr=Instance.new("Folder")
out.attributeNames={}
for _,name in {"","1","a.b","a/b","a-b","a_b","a b","RBXTest","rbxTest",string.rep("a",100),string.rep("a",101)} do
 out.attributeNames[name=="" and "<empty>" or name]=pcall(function()attr:SetAttribute(name,1)end)
end
out.attrTypes={}
for name,value in {number=1,bool=true,text="x",vector=Vector3.new(1,2,3),frame=CFrame.new(1,2,3),table={},instance=attr,callback=function()end} do
 out.attrTypes[name]=pcall(function()attr:SetAttribute("TypeTest",value)end)
end
attr:SetAttribute("Delete",true);attr:SetAttribute("Delete",nil)
out.attrDelete=attr:GetAttribute("Delete")==nil
local all=attr:GetAttributes();all.TypeTest=42
out.attributesCopy=attr:GetAttribute("TypeTest")~=42
attr:Destroy();copy:Destroy();external:Destroy()
return out
