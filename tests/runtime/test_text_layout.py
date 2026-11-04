"""Typed text layout scripting and native display projection."""
import copy
import json
from pathlib import Path
import unittest

import test_cframe_transforms as transforms
import test_motion_math as motion

ROOT=Path(__file__).resolve().parents[2]


class TextLayoutTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_image_scale_crop_and_sampling_values_are_typed(self):
        self.run_script('''
for _,class in {"ImageLabel","ImageButton"} do
    local image=Instance.new(class)
    assert(image.ScaleType==Enum.ScaleType.Stretch and image.ResampleMode==Enum.ResamplerMode.Default)
    assert(image.ImageRectOffset==Vector2.zero and image.ImageRectSize==Vector2.zero)
    assert(image.TileSize==UDim2.fromScale(1,1) and image.ImageColor3==Color3.new(1,1,1))
    image.ScaleType=Enum.ScaleType.Tile
    image.ResampleMode=Enum.ResamplerMode.Pixelated
    image.ImageRectOffset=Vector2.new(5,7)
    image.ImageRectSize=Vector2.new(20,30)
    image.TileSize=UDim2.fromOffset(40,50)
    image.ImageColor3=Color3.new(.5,1,1)
    local copy=image:Clone()
    assert(copy.ScaleType==image.ScaleType and copy.ResampleMode==image.ResampleMode)
    assert(copy.ImageRectSize==image.ImageRectSize and copy.ImageRectOffset==image.ImageRectOffset)
    assert(copy.TileSize==image.TileSize and copy.ImageColor3==image.ImageColor3)
    assert(not pcall(function()image.ScaleType=Enum.ResamplerMode.Pixelated end))
    assert(not pcall(function()image.ImageRectSize=UDim2.fromOffset(20,30) end))
    image:ResetPropertyToDefault("ScaleType")
    image:ResetPropertyToDefault("ImageRectSize")
    assert(image.ScaleType==Enum.ScaleType.Stretch and image.ImageRectSize==Vector2.zero)
end
''')

    def snapshot(self,properties):
        snapshot=copy.deepcopy(transforms.SNAPSHOT)
        snapshot['nodes'].append({'id':'text:label','class_name':'TextLabel','name':'TextFixture',
                                  'parent':'scope:workspace','properties':properties})
        return snapshot

    def test_alignment_and_direction_enum_metadata_and_identity(self):
        self.run_script('''
for _,case in ipairs({{Enum.TextXAlignment,{"Left","Right","Center"}},
 {Enum.TextYAlignment,{"Top","Center","Bottom"}},
 {Enum.TextDirection,{"Auto","LeftToRight","RightToLeft"}}})do
 local enum,names=case[1],case[2]
 for i,name in ipairs(names)do
  local value=enum[name];assert(value.Value==i-1 and value.Name==name and value.EnumType==enum)
  assert(rawequal(enum:FromValue(i-1),value) and rawequal(enum:FromName(name),value))
 end
end
assert(Enum.TextXAlignment.Left~=Enum.TextYAlignment.Top)
''')

    def test_textbox_placeholder_color_is_typed_and_resettable(self):
        self.run_script('''
local box=Instance.new("TextBox")
local original=box.PlaceholderColor3
assert(typeof(original)=="Color3")
local color=Color3.fromRGB(10,20,30)
box.PlaceholderColor3=color
assert(box.PlaceholderColor3==color and box:Clone().PlaceholderColor3==color)
assert(not pcall(function()box.PlaceholderColor3="red"end))
box:ResetPropertyToDefault("PlaceholderColor3")
assert(box.PlaceholderColor3==original)
''')

    def test_text_truncate_enum_defaults_changes_and_reset(self):
        self.run_script('''
for index,name in {"None","AtEnd","SplitWord"} do
    local item=Enum.TextTruncate[name]
    assert(item.Value==index-1 and rawequal(Enum.TextTruncate:FromValue(index-1),item))
end
for _,class in {"TextLabel","TextButton","TextBox"} do
    local item=Instance.new(class)
    assert(item.TextTruncate==Enum.TextTruncate.None)
    local changes=0
    item:GetPropertyChangedSignal("TextTruncate"):Connect(function()changes+=1 end)
    item.TextTruncate=Enum.TextTruncate.AtEnd
    task.wait(0)
    assert(changes==1 and item:IsPropertyModified("TextTruncate"))
    assert(item:Clone().TextTruncate==Enum.TextTruncate.AtEnd)
    assert(not pcall(function()item.TextTruncate=Enum.TextXAlignment.Left end))
    item:ResetPropertyToDefault("TextTruncate")
    task.wait(0)
    assert(changes==2 and item.TextTruncate==Enum.TextTruncate.None)
end
''')

    def test_stroke_modes_and_text_color_are_typed_and_resettable(self):
        self.run_script('''
local stroke=Instance.new("UIStroke")
assert(stroke.ApplyStrokeMode==Enum.ApplyStrokeMode.Contextual)
assert(stroke.StrokeSizingMode==Enum.StrokeSizingMode.FixedSize)
assert(stroke.BorderStrokePosition==Enum.BorderStrokePosition.Center)
assert(stroke.BorderOffset==UDim.new(0,0) and stroke.LineJoinMode==Enum.LineJoinMode.Round)
stroke.ApplyStrokeMode=Enum.ApplyStrokeMode.Border
stroke.StrokeSizingMode=Enum.StrokeSizingMode.ScaledSize
stroke.BorderStrokePosition=Enum.BorderStrokePosition.Inner
stroke.BorderOffset=UDim.new(.25,-4)
stroke.LineJoinMode=Enum.LineJoinMode.Bevel
assert(not pcall(function()stroke.ApplyStrokeMode=Enum.StrokeSizingMode.FixedSize end))
assert(not pcall(function()stroke.BorderOffset=UDim2.fromOffset(4,5) end))
local clone=stroke:Clone()
assert(clone.ApplyStrokeMode==Enum.ApplyStrokeMode.Border and clone.StrokeSizingMode==Enum.StrokeSizingMode.ScaledSize)
assert(clone.BorderStrokePosition==Enum.BorderStrokePosition.Inner and clone.BorderOffset==UDim.new(.25,-4))
assert(clone.LineJoinMode==Enum.LineJoinMode.Bevel)
stroke:ResetPropertyToDefault("ApplyStrokeMode")
stroke:ResetPropertyToDefault("StrokeSizingMode")
stroke:ResetPropertyToDefault("BorderStrokePosition")
stroke:ResetPropertyToDefault("BorderOffset")
stroke:ResetPropertyToDefault("LineJoinMode")
assert(stroke.ApplyStrokeMode==Enum.ApplyStrokeMode.Contextual and stroke.StrokeSizingMode==Enum.StrokeSizingMode.FixedSize)
assert(stroke.BorderStrokePosition==Enum.BorderStrokePosition.Center and stroke.BorderOffset==UDim.new(0,0))
assert(stroke.LineJoinMode==Enum.LineJoinMode.Round)
for _,class in {"TextLabel","TextButton","TextBox"} do
    local label=Instance.new(class)
    assert(label.TextStrokeColor3==Color3.new(0,0,0))
    label.TextStrokeColor3=Color3.new(1,0,0)
    assert(label:Clone().TextStrokeColor3==label.TextStrokeColor3)
    assert(not pcall(function()label.TextStrokeColor3="red" end))
    label:ResetPropertyToDefault("TextStrokeColor3")
    assert(label.TextStrokeColor3==Color3.new(0,0,0))
end
''')

    def test_gradient_values_are_typed_cloned_and_reset(self):
        self.run_script('''
local gradient=Instance.new("UIGradient")
local defaultColor=ColorSequence.new(Color3.new(1,1,1))
assert(gradient.Color==defaultColor and gradient.Transparency==NumberSequence.new(0))
assert(gradient.Offset==Vector2.zero and gradient.TileMode==Enum.GradientTileMode.Clamp)
assert(gradient.Type==Enum.GradientType.Linear)
gradient.Color=ColorSequence.new(Color3.new(1,0,0),Color3.new(0,0,1))
gradient.Transparency=NumberSequence.new(.25,.75)
gradient.Offset=Vector2.new(.25,-.5)
gradient.TileMode=Enum.GradientTileMode.Mirror
gradient.Type=Enum.GradientType.Elliptical
local clone=gradient:Clone()
assert(clone.Color==gradient.Color and clone.Transparency==gradient.Transparency)
assert(clone.Offset==gradient.Offset and clone.TileMode==gradient.TileMode)
assert(clone.Type==Enum.GradientType.Elliptical and Enum.GradientType:FromValue(2)==Enum.GradientType.Conical)
assert(not pcall(function()gradient.Transparency=.5 end))
assert(not pcall(function()gradient.Color=Color3.new(1,0,0) end))
assert(not pcall(function()gradient.Offset=Vector2.new(math.huge,0) end))
assert(not pcall(function()gradient.TileMode=Enum.LineJoinMode.Round end))
assert(not pcall(function()gradient.Type=Enum.GradientTileMode.Clamp end))
for _,key in {"Color","Transparency","Offset","TileMode","Type"} do gradient:ResetPropertyToDefault(key) end
assert(gradient.Color==defaultColor and gradient.Transparency==NumberSequence.new(0))
assert(gradient.Offset==Vector2.zero and gradient.TileMode==Enum.GradientTileMode.Clamp)
assert(gradient.Type==Enum.GradientType.Linear)
''')

    def test_script_focus_moves_between_textboxes_and_releases_on_detach(self):
        self.run_script('''
local input=game:GetService("UserInputService")
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"))
local a=Instance.new("TextBox",screen)
local b=Instance.new("TextBox",screen)
local detached=Instance.new("TextBox")
assert(input:GetFocusedTextBox()==nil)
a:CaptureFocus()
assert(a:IsFocused() and input:GetFocusedTextBox()==a)
detached:CaptureFocus()
assert(a:IsFocused() and not detached:IsFocused())
b:CaptureFocus()
a:ReleaseFocus()
assert(not a:IsFocused() and b:IsFocused() and input:GetFocusedTextBox()==b)
b.Parent=nil
assert(not b:IsFocused() and input:GetFocusedTextBox()==nil)
''')

    def test_textbox_cursor_and_selection_use_one_based_byte_offsets(self):
        self.run_script('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"))
local box=Instance.new("TextBox",screen)
box.ClearTextOnFocus=false
box.Text="aé🙂z"
assert(box.CursorPosition==-1 and box.SelectionStart==-1)
box.CursorPosition=3
assert(box.CursorPosition==-1)
box:CaptureFocus()
assert(box.CursorPosition==9 and box.SelectionStart==-1)
box.CursorPosition=5
assert(box.CursorPosition==4)
box.SelectionStart=8
assert(box.SelectionStart==8)
box.Text="x"
assert(box.CursorPosition==2 and box.SelectionStart==-1)
for _,invalid in {"1",1.5,math.huge,0/0,-2} do
    assert(not pcall(function()box.CursorPosition=invalid end))
end
box:ReleaseFocus()
assert(box.CursorPosition==-1 and box.SelectionStart==-1)
''')

    def test_all_three_text_classes_accept_typed_alignment_and_direction(self):
        self.run_script('''
for _,class in ipairs({"TextLabel","TextButton","TextBox"})do
 local label=Instance.new(class)
 for _,item in ipairs(Enum.TextXAlignment:GetEnumItems())do label.TextXAlignment=item;assert(label.TextXAlignment==item)end
 for _,item in ipairs(Enum.TextYAlignment:GetEnumItems())do label.TextYAlignment=item;assert(label.TextYAlignment==item)end
 for _,item in ipairs(Enum.TextDirection:GetEnumItems())do label.TextDirection=item;assert(label.TextDirection==item)end
 assert(label:Clone().TextDirection==label.TextDirection)
end
''')

    def test_wrong_alignment_types_are_rejected_without_changing_properties(self):
        self.run_script('''
local label=Instance.new("TextLabel");label.TextXAlignment=Enum.TextXAlignment.Left
for _,bad in ipairs({0,"Left",{},Enum.HorizontalAlignment.Left,Enum.TextYAlignment.Top})do
 assert(not pcall(function()label.TextXAlignment=bad end))
 assert(label.TextXAlignment==Enum.TextXAlignment.Left)
end
assert(not pcall(function()label.TextXAlignment=nil end))
''')

    def test_textwrap_alias_shares_changes_and_constructor_default_operations(self):
        self.run_script('''
local label=Instance.new("TextLabel");local changes=0
label:GetPropertyChangedSignal("TextWrap"):Connect(function()changes+=1 end)
label.TextWrap=true;task.wait(0)
assert(label.TextWrap and label.TextWrapped and changes==1)
assert(label:IsPropertyModified("TextWrap"))
assert(client.propertyOrigin(label,"TextWrap")=="local")
label:ResetPropertyToDefault("TextWrap");task.wait(0)
assert(not label.TextWrap and not label.TextWrapped and changes==2)
local folder=Instance.new("Folder");local child=Instance.new("Folder",folder);child.Name="TextWrap"
assert(folder.TextWrap==child)
''')

    def test_received_legacy_wrap_alias_is_normalized(self):
        self.run_script('assert(workspace.TextFixture.TextWrapped and workspace.TextFixture.TextWrap)',self.snapshot({'TextWrap':True}))
        self.run_script('assert(not workspace.TextFixture.TextWrapped and not workspace.TextFixture.TextWrap)',self.snapshot({'TextWrap':True,'TextWrapped':False}))

    def test_textsize_and_lineheight_must_be_finite_and_in_supported_ranges(self):
        self.run_script('''
local label=Instance.new("TextLabel");label.TextSize=24;label.LineHeight=1.5
for _,bad in ipairs({0,-1,1001,math.huge,0/0,"24",true})do
 assert(not pcall(function()label.TextSize=bad end));assert(label.TextSize==24)
end
for _,bad in ipairs({.9,3.1,math.huge,0/0,"2",true})do
 assert(not pcall(function()label.LineHeight=bad end));assert(label.LineHeight==1.5)
end
label.LineHeight=3;label.TextSize=1000
assert(label.LineHeight==3 and label.TextSize==1000)
''')

    def test_size_constraint_bounds_validate_both_members(self):
        self.run_script('''
local constraint=Instance.new("UITextSizeConstraint")
constraint.MinTextSize=12;constraint.MaxTextSize=36
assert(constraint.MinTextSize==12 and constraint.MaxTextSize==36)
for _,bad in ipairs({0,-1,12.5,1001,math.huge,0/0,"12",true})do
 assert(not pcall(function()constraint.MinTextSize=bad end));assert(constraint.MinTextSize==12)
end
assert(not pcall(function()constraint.MinTextSize=40 end))
assert(not pcall(function()constraint.MaxTextSize=11 end))
assert(constraint.MinTextSize==12 and constraint.MaxTextSize==36)
constraint.MaxTextSize=12;assert(constraint.MinTextSize==constraint.MaxTextSize)
''')

    def test_received_typed_enums_read_correctly_and_malformed_items_error(self):
        self.run_script('''
assert(workspace.TextFixture.TextXAlignment==Enum.TextXAlignment.Right)
assert(workspace.TextFixture.TextYAlignment==Enum.TextYAlignment.Bottom)
assert(workspace.TextFixture.TextDirection==Enum.TextDirection.RightToLeft)
''',self.snapshot({'TextXAlignment':1,'TextYAlignment':{'$enum':{'type':'TextYAlignment','value':2,'name':'Bottom'}},'TextDirection':2}))
        self.run_script('assert(not pcall(function()return workspace.TextFixture.TextXAlignment end))',
                        self.snapshot({'TextXAlignment':{'$enum':{'type':'HorizontalAlignment','value':0}}}))


class TextLayoutPersistentTests(unittest.TestCase):
    setUp=motion.MotionMathTests.setUp
    execute=motion.MotionMathTests.execute

    def test_image_load_receipts_are_scoped_to_the_current_assignment(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
getgenv().icon=Instance.new("ImageButton",screen);icon.Name="Loading";icon.Size=UDim2.fromOffset(100,50)
icon.Image="rbxassetid://1"
getgenv().imageChanges={}
icon:GetPropertyChangedSignal("IsLoaded"):Connect(function() table.insert(imageChanges,icon.IsLoaded) end)
assert(not icon.IsLoaded and not pcall(function()icon.IsLoaded=true end))
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        def frame():
            return next(row for row in self.host.call({'op':'render'})['frame']['gui'] if row['name']=='Loading')
        first=frame()
        def receipt(row):
            result=self.host.call({'op':'gui_action','action':{
                'kind':'image_loaded','id':row['id'],'token':row['imageLoadToken']}})
            self.assertTrue(result['ok'],result)
        self.assertFalse(first['imageLoaded'])
        self.assertRegex(first['imageLoadToken'],r'^[0-9a-f]{32}$')
        receipt(first)
        receipt(first)
        self.execute('assert(icon.IsLoaded and not icon:Clone().IsLoaded and #imageChanges==1 and imageChanges[1])')
        self.execute('icon.Image=icon.Image;assert(icon.IsLoaded)')
        self.execute('icon.Image="rbxassetid://2";icon.Image="rbxassetid://1";assert(not icon.IsLoaded)')
        replacement=frame()
        self.assertNotEqual(replacement['imageLoadToken'],first['imageLoadToken'])
        receipt(first)
        self.execute('assert(not icon.IsLoaded and #imageChanges==2 and imageChanges[2]==false)')
        receipt(replacement)
        self.execute('assert(icon.IsLoaded and #imageChanges==3 and imageChanges[3]);icon.Image=""')
        self.assertNotIn('imageLoadToken',frame())
        self.execute('assert(not icon.IsLoaded and #imageChanges==4 and imageChanges[4]==false)')

    def test_image_button_alternates_preserve_base_load_state(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
getgenv().icon=Instance.new("ImageButton",screen);icon.Name="Variants";icon.Size=UDim2.fromOffset(160,60)
icon.HoverImage="rbxassetid://2";icon.PressedImage="rbxassetid://3"
icon.ScaleType=Enum.ScaleType.Tile;icon.TileSize=UDim2.fromOffset(24,16)
assert(not icon.IsLoaded)
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        def frame():
            return next(row for row in self.host.call({'op':'render'})['frame']['gui'] if row['name']=='Variants')
        alternate=frame()
        self.assertEqual(alternate['imageHover'],'rbxassetid://2')
        self.assertEqual(alternate['imagePressed'],'rbxassetid://3')
        self.assertEqual(alternate['imageMode'],2)
        self.assertEqual(alternate['imageTile'],[24,16])
        self.assertTrue(alternate['imageButtonEnabled'])
        self.assertNotIn('imageLoadToken',alternate)
        self.execute('icon.Image="rbxassetid://1"')
        base=frame()
        result=self.host.call({'op':'gui_action','action':{
            'kind':'image_loaded','id':base['id'],'token':base['imageLoadToken']}})
        self.assertTrue(result['ok'],result)
        self.execute('icon.HoverImage="";icon.PressedImage="rbxassetid://4";icon.Interactable=false;assert(icon.IsLoaded)')
        changed=frame()
        self.assertEqual(changed['imageHover'],'')
        self.assertEqual(changed['imagePressed'],'rbxassetid://4')
        self.assertFalse(changed['imageButtonEnabled'])
        self.assertEqual(changed['imageLoadToken'],base['imageLoadToken'])
        self.assertTrue(changed['imageLoaded'])

    def test_image_frame_resolves_tiles_and_preserves_atlas_rect(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
local image=Instance.new("ImageButton",screen);image.Name="Atlas";image.Size=UDim2.fromOffset(160,60)
image.Image="rbxassetid://1";image.ImageRectOffset=Vector2.new(10,20);image.ImageRectSize=Vector2.new(30,40)
image.ImageColor3=Color3.new(.5,1,.25);image.ResampleMode=Enum.ResamplerMode.Pixelated
image.ScaleType=Enum.ScaleType.Tile;image.TileSize=UDim2.new(.25,5,.5,0)
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        row=next(g for g in self.host.call({'op':'render'})['frame']['gui'] if g['name']=='Atlas')
        self.assertEqual(row['imageMode'],2)
        self.assertEqual(row['imageTile'],[45,30])
        self.assertEqual(row['imageOffset'],{'$vector2':[10,20]})
        self.assertEqual(row['imageRect'],{'$vector2':[30,40]})
        self.assertEqual(row['imageColor'],{'$color3':[.5,1,.25]})
        self.assertTrue(row['imagePixelated'])

    def test_gradient_frame_preserves_sequence_times_ignores_envelopes_and_limits_scope(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
local panel=Instance.new("Frame",screen);panel.Name="GradientPanel";panel.Size=UDim2.fromOffset(160,40)
local child=Instance.new("Frame",panel);child.Name="PlainChild";child.Size=UDim2.fromOffset(20,20)
getgenv().gradient=Instance.new("UIGradient",panel)
gradient.Color=ColorSequence.new(Color3.new(1,0,0),Color3.new(0,0,1))
gradient.Transparency=NumberSequence.new({NumberSequenceKeypoint.new(0,0),NumberSequenceKeypoint.new(.5,.75,.125),NumberSequenceKeypoint.new(1,.5)})
gradient.Rotation=450;gradient.Scale=.5;gradient.Offset=Vector2.new(.25,-.5)
gradient.TileMode=Enum.GradientTileMode.Mirror
gradient.Type=Enum.GradientType.Elliptical
local stroke=Instance.new("UIStroke",panel)
local borderGradient=Instance.new("UIGradient",stroke)
borderGradient.TileMode=Enum.GradientTileMode.Repeat
borderGradient.Type=Enum.GradientType.Conical
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        def gui_rows():
            return {g['name']:g for g in self.host.call({'op':'render'})['frame']['gui']}
        rows=gui_rows()
        paint=rows['GradientPanel']['backgroundGradient']
        self.assertEqual(paint,{'colors':[[0,1,0,0],[1,0,0,1]],'alpha':[[0,1],[.5,.25],[1,.5]],
            'offset':[.25,-.5],'rotation':90,'scale':.5,'tile':2,'type':3})
        self.assertEqual(rows['GradientPanel']['stroke']['gradient']['tile'],1)
        self.assertEqual(rows['GradientPanel']['stroke']['gradient']['type'],2)
        self.assertNotIn('backgroundGradient',rows['PlainChild'])
        self.execute('gradient.Type=Enum.GradientType.Radial')
        self.assertEqual(gui_rows()['GradientPanel']['backgroundGradient']['type'],1)
        self.execute('gradient.Enabled=false')
        self.assertNotIn('backgroundGradient',gui_rows()['GradientPanel'])

    def test_border_stroke_layers_use_offsets_and_order_within_frame_budget(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
getgenv().panel=Instance.new("Frame",screen);panel.Name="LayeredStroke";panel.Size=UDim2.fromOffset(160,40)
for z=10,1,-1 do
    local stroke=Instance.new("UIStroke",panel)
    stroke.ZIndex=z;stroke.Thickness=z
    if z==10 then
        stroke.BorderStrokePosition=Enum.BorderStrokePosition.Outer
        stroke.BorderOffset=UDim.new(.25,-4)
        stroke.LineJoinMode=Enum.LineJoinMode.Miter
    end
end
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        def panel_frame():
            return next(g for g in self.host.call({'op':'render'})['frame']['gui'] if g['name']=='LayeredStroke')
        row=panel_frame()
        self.assertEqual([stroke['thickness'] for stroke in row['borderStrokes']],list(range(3,11)))
        top=row['borderStrokes'][-1]
        self.assertEqual(top,row['stroke'])
        self.assertEqual((top['position'],top['offset'],top['join']),(0,6,2))
        self.execute('panel.Size=UDim2.fromOffset(160,80)')
        self.assertEqual(panel_frame()['stroke']['offset'],16)

    def test_text_outline_legacy_shadow_and_border_project_independently(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
local label=Instance.new("TextLabel",screen);label.Name="OutlinedText";label.Size=UDim2.fromOffset(160,40)
label.TextStrokeTransparency=.75;label.TextStrokeColor3=Color3.new(1,0,0)
local border=Instance.new("UIStroke",label);border.ApplyStrokeMode=Enum.ApplyStrokeMode.Border
border.StrokeSizingMode=Enum.StrokeSizingMode.ScaledSize;border.Thickness=.125
local low=Instance.new("UIStroke",label);low.Color=Color3.new(0,0,1);low.ZIndex=2
getgenv().outline=Instance.new("UIStroke",label);outline.Color=Color3.new(0,1,0);outline.ZIndex=3
outline.StrokeSizingMode=Enum.StrokeSizingMode.ScaledSize;outline.Thickness=.125
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        def label_frame():
            return next(g for g in self.host.call({'op':'render'})['frame']['gui'] if g['name']=='OutlinedText')
        row=label_frame()
        self.assertEqual(row['legacyTextStroke'],{'color':{'$color3':[1,0,0]},'transparency':.75})
        self.assertEqual(row['stroke']['thickness'],5)
        self.assertEqual(row['textOutline'],{'color':{'$color3':[0,1,0]},'transparency':0,
            'thickness':.125,'scaled':True})
        self.execute('outline.Enabled=false')
        row=label_frame()
        self.assertEqual(row['textOutline']['color'],{'$color3':[0,0,1]})
        self.assertFalse(row['textOutline']['scaled'])
        self.assertEqual(row['stroke']['thickness'],5)

    def test_native_frame_carries_reveal_limit_and_placeholder(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
for i,class in {"TextLabel","TextButton","TextBox"} do
    local node=Instance.new(class,screen)
    node.Size=UDim2.fromOffset(200,100)
    node.MaxVisibleGraphemes=i-1
    node.RichText=true
    node.Text='<b>é👨‍👩‍👧‍👦</b>'
    assert(node.ContentText=='é👨‍👩‍👧‍👦')
    if class=="TextBox" then
        node.Text=""
        node.PlaceholderText="Enter text"
        node.PlaceholderColor3=Color3.new(.25,.5,.75)
    end
end
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        response=self.host.call({'op':'render'})
        self.assertTrue(response['ok'],response)
        rows={row['class']:row for row in response['frame']['gui']}
        self.assertEqual(rows['TextLabel']['maxVisibleGraphemes'],0)
        self.assertEqual(rows['TextButton']['maxVisibleGraphemes'],1)
        box=rows['TextBox']
        self.assertEqual(box['maxVisibleGraphemes'],2)
        self.assertEqual(box['text'],'')
        self.assertEqual(box['placeholderText'],'Enter text')
        self.assertEqual(box['placeholderColor'],{'$color3':[.25,.5,.75]})

    def test_native_frame_contains_text_layout_and_constraint_state(self):
        self.execute('''
local screen=Instance.new("ScreenGui",game:GetService("CoreGui"));screen.IgnoreGuiInset=true
getgenv().label=Instance.new("TextLabel",screen);label.Name="LayoutLabel"
label.Size=UDim2.fromOffset(240,80);label.Text="A long text sample";label.TextSize=25
label.TextXAlignment=Enum.TextXAlignment.Left;label.TextYAlignment=Enum.TextYAlignment.Top
label.TextDirection=Enum.TextDirection.LeftToRight;label.TextWrapped=true;label.TextScaled=true;label.LineHeight=1.5
label.TextTruncate=Enum.TextTruncate.SplitWord
local constraint=Instance.new("UITextSizeConstraint",label);constraint.MaxTextSize=36;constraint.MinTextSize=12
''')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[],
            'display':{'size':[800,600],'device_insets':[0,0,0,0],'core_insets':[0,0,0,0]}}})
        self.assertTrue(update['ok'],update)
        response=self.host.call({'op':'render'});self.assertTrue(response['ok'],response)
        label=next(g for g in response['frame']['gui'] if g['name']=='LayoutLabel')
        self.assertEqual(label['textXAlignment'],0);self.assertEqual(label['textYAlignment'],0)
        self.assertEqual(label['textDirection'],1);self.assertEqual(label['lineHeight'],1.5)
        self.assertEqual(label['textTruncate'],2)
        self.assertEqual(label['textSizeConstraint'],[12,36]);self.assertTrue(label['textWrapped'])
        self.assertTrue(label['textScaled']);self.assertEqual(label['textScale'],1)
        self.execute('assert(label.TextSize==25)')

    def test_incoming_alias_update_wakes_canonical_signal_and_overrides_local_value(self):
        result=self.execute('getgenv().label=Instance.new("TextLabel");label.TextWrapped=false')
        node=next(m['node']['id'] for m in result['mutations'] if m.get('kind')=='create' and m['node']['class_name']=='TextLabel')
        self.execute('getgenv().changes=0;label:GetPropertyChangedSignal("TextWrap"):Connect(function()changes+=1 end)')
        update=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':100,'kind':'set','id':node,'fields':{'TextWrap':True}}]}})
        self.assertTrue(update['ok'],update)
        self.execute('assert(label.TextWrap and label.TextWrapped and changes==1)')


if __name__=='__main__':unittest.main()
