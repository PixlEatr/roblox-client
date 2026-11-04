local label = Instance.new("TextLabel")
assert(not label:IsPropertyModified("Text"))

label.Text = "Custom message"
label.Rotation = 30
assert(label:IsPropertyModified("Text"))
assert(label:IsPropertyModified("Rotation"))

label:ResetPropertyToDefault("Text")
label:ResetPropertyToDefault("Rotation")
assert(label.Text == "Label" and label.Rotation == 0)
assert(not label:IsPropertyModified("Text"))

label:Destroy()
return "Property defaults restored"
