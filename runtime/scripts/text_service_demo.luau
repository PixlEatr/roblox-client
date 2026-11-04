local service=game:GetService("TextService")
local info=service:GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")
local params=Instance.new("GetTextBoundsParams")
params.Font=Font.fromName("BuilderSans")
params.Size=20
params.Text="iiiiiiii"
local narrow=service:GetTextBoundsAsync(params)
params.Text="WWWWWWWW"
local wide=service:GetTextBoundsAsync(params)
assert(wide.X>narrow.X*3)
params.Text="A long string with several words"
params.Width=60
local wrapped=service:GetTextBoundsAsync(params)
assert(wrapped.X<=60 and wrapped.Y>20)
return info.Name,#info.Faces,narrow.X,wide.X,wrapped.Y
