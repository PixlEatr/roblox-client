"""Create the font browser fixture through real native Luau bindings."""
SOURCE='''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"))
screen.Name="FontShowcase";screen.IgnoreGuiInset=true
local panel=Instance.new("Frame",screen)
panel.Size=UDim2.fromScale(1,1);panel.BackgroundColor3=Color3.fromRGB(24,27,35)
local cases={
 {"Builder Sans | Regular",Font.fromName("BuilderSans")},
 {"Builder Sans | Bold",Font.fromName("BuilderSans",Enum.FontWeight.Bold)},
 {"Source Sans | Italic",Font.fromEnum(Enum.Font.SourceSansItalic)},
 {"Inconsolata | Monospace",Font.fromEnum(Enum.Font.Code)},
 {"Arimo | Regular",Font.fromName("Arimo")},
}
for i,case in ipairs(cases)do
 local label=Instance.new(i==5 and "TextBox" or i==2 and "TextButton" or "TextLabel",panel)
 label.Name="FontSample"..i;label.Text=case[1];label.FontFace=case[2]
 label.TextColor3=Color3.fromRGB(238,242,250);label.TextSize=26
 label.BackgroundTransparency=1;label.Size=UDim2.new(1,-64,0,64)
 label.Position=UDim2.new(0,32,0,24+(i-1)*76)
end
return true
'''


def install_font_showcase(host):
    result=host.call({'op':'execute','source':SOURCE,'timeout_ms':1000})
    if not result.get('ok') or result.get('task_errors'):raise RuntimeError(result)
