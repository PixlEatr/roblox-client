local font=Font.fromName("BuilderSans",Enum.FontWeight.SemiBold,Enum.FontStyle.Italic)
assert(font.Bold)
font.Bold=true
assert(font.Weight==Enum.FontWeight.Bold)
assert(Font.fromEnum(Enum.Font.SourceSansBold).Bold)
assert(Font.fromId(12345).Family=="rbxassetid://12345")
local found=false
for _,item in ipairs(Enum:GetEnums())do if item==Enum.FontWeight then found=true end end
assert(found)
return font.Family,font.Weight.Value,font.Style.Value,font.Bold
