R"DRAWING(
-- A real headless vector surface. ToSVG exports the currently visible scene.
local objects={};local nextId,count=0,0
local circleScreen
local function syncCircle(s)
 if s.kind~="Circle" then return end
 local p=s.properties
 if not s.frame then
  if not circleScreen then
   circleScreen=Instance.new("ScreenGui")
   circleScreen.Name="DrawingSurface";circleScreen.IgnoreGuiInset=true
   circleScreen.ResetOnSpawn=false;circleScreen.DisplayOrder=1000
   circleScreen.Parent=game:GetService("CoreGui")
  end
  local frame=Instance.new("Frame")
  frame.Name="DrawingCircle";frame.BorderSizePixel=0
  frame.AnchorPoint=Vector2.new(.5,.5)
  local corner=Instance.new("UICorner",frame);corner.CornerRadius=UDim.new(1,0)
  s.stroke=Instance.new("UIStroke",frame)
  s.frame=frame
 end
 local frame=s.frame
 frame.Visible=p.Visible;frame.ZIndex=p.ZIndex
 frame.Position=UDim2.fromOffset(p.Position.X,p.Position.Y)
 frame.Size=UDim2.fromOffset(p.Radius*2,p.Radius*2)
 frame.BackgroundColor3=p.Color
 frame.BackgroundTransparency=p.Filled and 1-p.Transparency or 1
 s.stroke.Color=p.Color;s.stroke.Thickness=p.Thickness
 s.stroke.Transparency=1-p.Transparency;s.stroke.Enabled=p.Thickness>0
 frame.Parent=circleScreen
end
local fonts={[0]="Arial, sans-serif",[1]="system-ui, sans-serif",[2]="IBM Plex Sans, sans-serif",[3]="monospace"}
local function finite(v) return type(v)=="number" and v==v and math.abs(v)<1e7 end
local function escape(s) return (s:gsub("&","&amp;"):gsub("<","&lt;"):gsub(">","&gt;"):gsub('"',"&quot;")) end
local function color(c) return string.format("rgb(%d,%d,%d)",math.round(math.clamp(c.R,0,1)*255),math.round(math.clamp(c.G,0,1)*255),math.round(math.clamp(c.B,0,1)*255)) end
Drawing={Fonts=table.freeze({UI=0,System=1,Plex=2,Monospace=3})}
local function state(object) local s=objects[object];assert(s and s.exists,"drawing object is destroyed or invalid");return s end
local function remove(object)
 local s=objects[object]
 if s and s.exists then
  s.exists=false;s.properties.Visible=false;count-=1
  if s.frame then s.frame:Destroy();s.frame=nil;s.stroke=nil end
 end
end
local function textbounds(p)
 if #p.Text==0 then return Vector2.zero end
 local size=p.Size>0 and p.Size or 16
 local width,lines=0,0
 for line in (p.Text.."\n"):gmatch("(.-)\n") do
  local ok,length=pcall(utf8.len,line);length=ok and length or #line
  width=math.max(width,length);lines+=1
 end
 local advance=p.Font==3 and 0.6 or 0.55
 return Vector2.new(width*size*advance,lines*size)
end
function Drawing.new(kind)
 assert(table.find({"Square","Line","Circle","Text","Image","Triangle","Quad"},kind),"unknown Drawing type")
 assert(count<512,"drawing object limit exceeded")
 local p={Visible=false,ZIndex=1,Transparency=1,Color=Color3.new(1,1,1)}
 if kind=="Line" then p.From=Vector2.zero;p.To=Vector2.zero;p.Thickness=1
 elseif kind=="Triangle" or kind=="Quad" then
  p.PointA=Vector2.zero;p.PointB=Vector2.zero;p.PointC=Vector2.zero
  if kind=="Quad" then p.PointD=Vector2.zero end;p.Thickness=1;p.Filled=false
 else
  p.Position=Vector2.new(500,500)
  if kind=="Square" then p.Size=Vector2.new(25,25);p.Thickness=1;p.Filled=false
  elseif kind=="Circle" then p.Radius=5;p.Thickness=1;p.Filled=false;p.NumSides=12
  elseif kind=="Image" then p.Size=Vector2.new(64,64);p.Data="";p.Rounding=0
  else p.Text="";p.Size=16;p.Font=0;p.Center=false;p.Outline=false;p.OutlineColor=Color3.new(0,0,0);p.OutlineOpacity=1 end
 end
 local object={};nextId+=1;objects[object]={kind=kind,properties=p,id=nextId,exists=true};count+=1
 syncCircle(objects[object])
 return setmetatable(object,{
  __metatable="Drawing object",
  __index=function(self,key)
   if key=="__OBJECT_EXISTS" then local s=objects[self];return s~=nil and s.exists end
   if key=="Remove" or key=="Destroy" then return remove end
   local s=objects[self];if not s or not s.exists then return nil end
   if key=="TextBounds" and s.kind=="Text" then return textbounds(s.properties) end
   local value=s.properties[key];assert(value~=nil,"unknown Drawing property: "..tostring(key));return value
  end,
  __newindex=function(self,key,value)
   local s=objects[self];if not s or not s.exists then return end
   local old=s.properties[key];assert(old~=nil,"unknown Drawing property: "..tostring(key))
   assert(typeof(old)==typeof(value),"Drawing property type mismatch")
   if type(value)=="number" then assert(finite(value),"Drawing number must be finite") end
   if typeof(value)=="Vector2" then assert(finite(value.X) and finite(value.Y),"Drawing coordinates must be finite") end
   if key=="Transparency" then assert(value>=0 and value<=1,"Transparency must be 0..1") end
   if key=="OutlineOpacity" then value=math.clamp(value,0,1) end
   if key=="Font" then assert(fonts[value],"unknown Drawing font") end
   if key=="NumSides" then assert(value==math.floor(value) and value>=0 and value<=1024,"Circle NumSides must be an integer from 0 to 1024") end
   if type(value)=="string" then assert(#value<=128*1024,"Drawing string exceeds limit") end
   if key=="Radius" or key=="Thickness" or (key=="Size" and type(value)=="number") then assert(value>=0,"Drawing size must be nonnegative") end
   s.properties[key]=value
   syncCircle(s)
  end,
 })
end
function isrenderobj(object) local s=objects[object];return s~=nil and s.exists end
function getrenderproperty(object,key) return object[key] end
function setrenderproperty(object,key,value) state(object);object[key]=value end
function cleardrawcache()
 for object in objects do remove(object) end
 table.clear(objects);count=0
end
function Drawing.ToSVG(width,height)
 width=width or 800;height=height or 600
 assert(finite(width) and finite(height) and width>0 and height>0 and width<=4096 and height<=4096,"invalid SVG surface size")
 local visible={};for _,s in objects do if s.exists and s.properties.Visible then table.insert(visible,s) end end
 table.sort(visible,function(a,b)return a.properties.ZIndex<b.properties.ZIndex or (a.properties.ZIndex==b.properties.ZIndex and a.id<b.id)end)
 local out={string.format('<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" viewBox="0 0 %g %g">',width,height,width,height)}
 for _,s in visible do
  local p=s.properties;local fill=p.Filled and color(p.Color) or "none"
  local style=string.format(' opacity="%g" stroke="%s" stroke-width="%g" fill="%s"',p.Transparency,color(p.Color),p.Thickness or 0,fill)
  if s.kind=="Square" then table.insert(out,string.format('<rect x="%g" y="%g" width="%g" height="%g"%s/>',p.Position.X,p.Position.Y,math.max(0,p.Size.X),math.max(0,p.Size.Y),style))
  elseif s.kind=="Line" then table.insert(out,string.format('<line x1="%g" y1="%g" x2="%g" y2="%g"%s/>',p.From.X,p.From.Y,p.To.X,p.To.Y,style))
  elseif s.kind=="Circle" then
   local sides=p.NumSides==0 and 64 or math.max(3,p.NumSides);local points={}
   for i=0,sides-1 do
    local angle=i*math.pi*2/sides-math.pi/2
    table.insert(points,string.format('%g,%g',p.Position.X+math.cos(angle)*p.Radius,p.Position.Y+math.sin(angle)*p.Radius))
   end
   table.insert(out,'<polygon points="'..table.concat(points," ")..'"'..style..'/>')
  elseif s.kind=="Triangle" or s.kind=="Quad" then
   local points={};for _,key in {"PointA","PointB","PointC","PointD"} do if p[key] then table.insert(points,string.format('%g,%g',p[key].X,p[key].Y)) end end
   table.insert(out,'<polygon points="'..table.concat(points," ")..'"'..style..'/>')
  elseif s.kind=="Text" then
   local outline=p.Outline and string.format(' stroke="%s" stroke-width="1" stroke-opacity="%g" paint-order="stroke"',color(p.OutlineColor),p.OutlineOpacity) or ""
   table.insert(out,string.format('<text x="%g" y="%g" fill="%s" opacity="%g" font-family="%s" font-size="%g" text-anchor="%s" dominant-baseline="hanging"%s>%s</text>',p.Position.X,p.Position.Y,color(p.Color),p.Transparency,fonts[p.Font],p.Size,p.Center and "middle" or "start",outline,escape(p.Text)))
  elseif s.kind=="Image" and #p.Data>0 then
   local data=p.Data;if data:sub(1,11)=="rbxasset://" then data=getcustomassetdata(data) end
   local mime=data:sub(1,8)=="\137PNG\13\10\26\10" and "image/png" or data:sub(1,3)=="\255\216\255" and "image/jpeg" or data:sub(1,3)=="GIF" and "image/gif" or nil
   assert(mime,"Drawing Image requires PNG, JPEG, or GIF data")
   local w,h=math.max(0,p.Size.X),math.max(0,p.Size.Y);local clip,tint="",""
   if p.Rounding>0 and w>0 and h>0 then
    local radius=math.min(p.Rounding,w/2,h/2);local id="drawing-clip-"..s.id
    table.insert(out,string.format('<defs><clipPath id="%s" clipPathUnits="userSpaceOnUse"><rect x="%g" y="%g" width="%g" height="%g" rx="%g" ry="%g"/></clipPath></defs>',id,p.Position.X,p.Position.Y,w,h,radius,radius))
    clip=string.format(' clip-path="url(#%s)"',id)
   end
   if p.Color.R<1 or p.Color.G<1 or p.Color.B<1 then
    local id="drawing-tint-"..s.id
    local values=string.format('%g 0 0 0 0 0 %g 0 0 0 0 0 %g 0 0 0 0 0 1 0',p.Color.R,p.Color.G,p.Color.B)
    table.insert(out,string.format('<defs><filter id="%s" color-interpolation-filters="sRGB"><feColorMatrix type="matrix" values="%s"/></filter></defs>',id,values))
    tint=string.format(' filter="url(#%s)"',id)
   end
   table.insert(out,string.format('<image x="%g" y="%g" width="%g" height="%g" opacity="%g"%s%s href="data:%s;base64,%s"/>',p.Position.X,p.Position.Y,w,h,p.Transparency,clip,tint,mime,crypt.base64encode(data)))
  end
 end
 table.insert(out,"</svg>");local svg=table.concat(out,"\n");assert(#svg<=1024*1024,"SVG scene exceeds 1 MiB");return svg
end
)DRAWING"
