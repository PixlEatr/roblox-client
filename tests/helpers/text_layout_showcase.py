"""Text layout fixture authored through actual Luau properties."""
SOURCE='''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.Name="TextLayoutShowcase";screen.IgnoreGuiInset=true
local panel=Instance.new("Frame",screen);panel.Size=UDim2.fromScale(1,1);panel.BackgroundColor3=Color3.fromRGB(24,27,35)
local function label(name,text,x,y,width,height)
 local result=Instance.new("TextLabel",panel);result.Name=name;result.Text=text;result.TextSize=18
 result.FontFace=Font.fromName("BuilderSans");result.TextColor3=Color3.fromRGB(238,242,250)
 result.BackgroundColor3=Color3.fromRGB(40,45,58);result.Position=UDim2.new(x,12,y,16)
 result.Size=UDim2.new(width,-24,0,height);return result
end
local xs={Enum.TextXAlignment.Left,Enum.TextXAlignment.Center,Enum.TextXAlignment.Right}
local ys={Enum.TextYAlignment.Top,Enum.TextYAlignment.Center,Enum.TextYAlignment.Bottom}
for row,y in ipairs(ys)do for col,x in ipairs(xs)do
 local result=label("Align"..row..col,x.Name.." / "..y.Name,(col-1)/3,0,1/3,64)
 result.Position=UDim2.new((col-1)/3,12,0,16+(row-1)*80)
 result.TextXAlignment=x;result.TextYAlignment=y
end end
local wrapped=label("Wrapped","Wrapped text prefers spaces and also breaks averyveryverylongword across lines.",0,0,.5,116)
wrapped.Position=UDim2.new(0,12,0,260);wrapped.TextWrapped=true;wrapped.TextXAlignment=Enum.TextXAlignment.Left;wrapped.TextYAlignment=Enum.TextYAlignment.Top;wrapped.LineHeight=1.25
local scaled=label("Scaled","TextScaled uses actual glyph measurements",.5,0,.5,116)
scaled.Position=UDim2.new(.5,12,0,260);scaled.TextScaled=true;scaled.TextSize=94
local constraint=Instance.new("UITextSizeConstraint",scaled);constraint.MaxTextSize=36;constraint.MinTextSize=12
local rtl=label("RTL","שלום | Right-to-left",0,0,.5,70)
rtl.Position=UDim2.new(0,12,0,392);rtl.TextDirection=Enum.TextDirection.RightToLeft;rtl.TextXAlignment=Enum.TextXAlignment.Right
local box=Instance.new("TextBox",panel);box.Name="MultilineBox";box.Text="Editable multiline\\nTextBox";box.MultiLine=true;box.TextWrapped=true
box.Position=UDim2.new(.5,12,0,392);box.Size=UDim2.new(.5,-24,0,70)
box.FontFace=Font.fromName("BuilderSans");box.TextSize=18;box.TextColor3=Color3.fromRGB(238,242,250);box.BackgroundColor3=Color3.fromRGB(40,45,58)
box.TextXAlignment=Enum.TextXAlignment.Left;box.TextYAlignment=Enum.TextYAlignment.Top
return true
'''


def install_text_layout_showcase(host):
    result=host.call({'op':'execute','source':SOURCE,'timeout_ms':1000})
    if not result.get('ok') or result.get('task_errors'):raise RuntimeError(result)
