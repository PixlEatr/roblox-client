-- Fresh, unparented controls only; no DataModel changes.
local out={version=version(),classes={},booleans={},rect={}}
for _,name in ipairs({"Frame","TextLabel","TextButton","TextBox","ImageLabel","ImageButton","ScrollingFrame","ViewportFrame","CanvasGroup","VideoFrame"})do
 local x=Instance.new(name);local s=x.Size
 out.classes[name]={size={s.X.Scale,s.X.Offset,s.Y.Scale,s.Y.Offset}}
 if x:IsA("ImageLabel") or x:IsA("ImageButton")then
  local r=x.SliceCenter
  out.classes[name].sliceCenter={r.Min.X,r.Min.Y,r.Max.X,r.Max.Y}
  out.classes[name].sliceScale=x.SliceScale
 end
 x:Destroy()
end
local service=game:GetService("TweenService")
for _,initial in ipairs({false,true})do
 local v=Instance.new("BoolValue");v.Value=initial
 local t=service:Create(v,TweenInfo.new(.2,Enum.EasingStyle.Linear),{Value=not initial})
 local row={initial=initial}
 t:Play();row.immediate=v.Value
 task.wait(.025);row.early=v.Value
 task.wait(.12);row.late=v.Value
 task.wait(.1);row.final=v.Value
 table.insert(out.booleans,row)
 t:Destroy();v:Destroy()
end
local image=Instance.new("ImageLabel")
local target=Rect.new(10,20,40,50)
local tween=service:Create(image,TweenInfo.new(.2,Enum.EasingStyle.Linear),{SliceCenter=target})
tween:Play();task.wait(.07)
local middle=image.SliceCenter
out.rect.middle={middle.Min.X,middle.Min.Y,middle.Max.X,middle.Max.Y}
task.wait(.2)
local final=image.SliceCenter
out.rect.final={final.Min.X,final.Min.Y,final.Max.X,final.Max.Y}
tween:Destroy();image:Destroy()
out.booleanProgress={}
for _,initial in ipairs({false,true})do
 local p=Instance.new("Part");p.Anchored=initial;p.Transparency=0
 local t=service:Create(p,TweenInfo.new(.6,Enum.EasingStyle.Linear),{Anchored=not initial,Transparency=1})
 local row={initial=initial,samples={}}
 t:Play()
 for i=1,16 do
  task.wait(.04);table.insert(row.samples,{alpha=p.Transparency,value=p.Anchored})
  if p.Transparency==1 then break end
 end
 table.insert(out.booleanProgress,row);t:Destroy();p:Destroy()
end
out.zeroRect=tostring(Rect.new())
return game:GetService("HttpService"):JSONEncode(out)
