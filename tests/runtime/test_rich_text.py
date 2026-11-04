"""Check that native text, layout metadata, and display share the same parse."""
import unittest

import test_cframe_transforms as transforms
import test_motion_math as motion


class RichContentTests(unittest.TestCase):
    run_script = transforms.CFrameTransformTests.run_script

    def test_content_text_preserves_source_and_decodes_only_one_escape_layer(self):
        self.run_script(r'''
for _,class in {"TextLabel","TextButton","TextBox"} do
    local text=Instance.new(class)
    text.Text='<b>A<i>&amp;lt;</i></b><br/>B<!-- comment --> &apos;&quot;&gt;'
    assert(text.ContentText==text.Text)
    text.RichText=true
    assert(text.ContentText=="A&lt;\nB '\">")
    assert(text:Clone().ContentText==text.ContentText)
    assert(not pcall(function()text.ContentText="fake"end))
end
''')

    def test_malformed_and_unknown_markup_stays_literal(self):
        self.run_script('''
local text=Instance.new("TextLabel")
text.RichText=true
for _,source in {"<b>unfinished","<b><i>x</b></i>",'<img src="local">','<font color="red">x</font>',"a < 3"} do
    text.Text=source
    assert(text.ContentText==source,source)
end
''')

    def test_stroke_tags_strip_markup_and_validate_attributes(self):
        self.run_script('''
local text=Instance.new("TextBox")
text.RichText=true
text.Text='<stroke th="2" tr="0.25"><b>A</b></stroke>B'
assert(text.ContentText=="AB")
for _,source in {
    '<stroke th="2" thickness="3">x</stroke>',
    '<stroke tr="1.1">x</stroke>',
    '<stroke joins="invalid">x</stroke>',
    '<stroke sizing="invalid">x</stroke>',
} do
    text.Text=source
    assert(text.ContentText==source,source)
end
''')

    def test_unicode_case_tags_scope_and_preserve_authored_source(self):
        self.run_script('''
for _,class in {"TextLabel","TextButton","TextBox"} do
    local label=Instance.new(class)
    local source="pre <uppercase>straße <i>κόσμος</i> &lt;abc&gt;</uppercase> <uc>𐐨ж</uc> <smallcaps>Été</smallcaps> <sc>i<uc>ß</uc>j</sc> post"
    label.RichText=true
    label.Text=source
    assert(label.Text==source)
    assert(label.ContentText=="pre STRASSE ΚΌΣΜΟΣ <ABC> 𐐀Ж ÉTÉ ISSJ post",label.ContentText)
    label.Text="<uppercase>"..string.rep("ΐ",1000).."</uppercase>"
    assert(label.ContentText==label.Text)
end
''')

    def test_content_changed_signal_uses_the_derived_value(self):
        self.run_script('''
local text=Instance.new("TextLabel")
text.RichText=true
text.Text="<b>A</b>"
local changes=0
text:GetPropertyChangedSignal("ContentText"):Connect(function()changes+=1 end)
text.Text="<i>A</i>";task.wait(0);assert(changes==0)
text.Text="<i>B</i>";task.wait(0);assert(changes==1)
text.RichText=false;task.wait(0);assert(changes==2 and text.ContentText=="<i>B</i>")
''')


class RichFrameTests(unittest.TestCase):
    setUp = motion.MotionMathTests.setUp
    execute = motion.MotionMathTests.execute

    def test_small_caps_keep_combining_marks_at_the_size_of_their_base(self):
        self.execute('''
local root=Instance.new("ScreenGui",game:GetService("CoreGui"))
root.IgnoreGuiInset=true
local label=Instance.new("TextLabel",root)
label.Name="Case"
label.Size=UDim2.fromOffset(300,100)
label.RichText=true
label.Text='<sc>ABéß<uc>cd</uc>f</sc>z'
''')
        update = self.host.call({'op':'update','delta':{
            'version':1,'delta':True,'actions':[],'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        gui = self.host.call({'op':'render'})['frame']['gui']
        row = next(item for item in gui if item['name']=='Case')
        self.assertEqual(row['contentText'],'ABÉSSCDFz')
        self.assertEqual(
            [(run['text'],run['style'].get('sizeScale',1)) for run in row['richRuns']],
            [('AB',1),('ÉSS',0.8),('CD',1),('F',0.8),('z',1)])

    def test_render_frame_carries_inherited_runs_and_resolved_font_choices(self):
        self.execute('''
local root=Instance.new("ScreenGui",game:GetService("CoreGui"))
root.IgnoreGuiInset=true
local label=Instance.new("TextLabel",root)
label.Name="Styled"
label.Size=UDim2.fromOffset(300,100)
label.FontFace=Font.fromName("BuilderSans")
label.RichText=true
label.Text='<b>A<i>B</i></b><br/><mark color="rgb(1,2,3)">C</mark><font face="Code">D</font>'
''')
        update = self.host.call({'op':'update','delta':{
            'version':1,'delta':True,'actions':[],'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        gui = self.host.call({'op':'render'})['frame']['gui']
        row = next(item for item in gui if item['name']=='Styled')
        self.assertEqual(row['contentText'],'AB\nCD')
        a,b,br,c,d = row['richRuns']
        self.assertEqual(a['fontFace']['weight'],700)
        self.assertEqual(b['fontFace']['style'],1)
        self.assertEqual(b['fontFace']['weight'],700)
        self.assertEqual(br['text'],'\n')
        self.assertEqual(c['style']['mark'],[1,2,3])
        self.assertTrue(d['fontFace']['family'].endswith('/Inconsolata.json'))

    def test_text_box_frame_keeps_editable_source_and_styled_preview_runs(self):
        source = '<stroke color="#3080FF" th="0.1" tr="0.25" joins="bevel" sizing="scaled"><b>A</b><stroke>B</stroke>C</stroke>D'
        self.execute(f'''
local root=Instance.new("ScreenGui",game:GetService("CoreGui"))
root.IgnoreGuiInset=true
local box=Instance.new("TextBox",root)
box.Name="StyledBox"
box.Size=UDim2.fromOffset(300,100)
box.FontFace=Font.fromName("BuilderSans")
box.RichText=true
box.Text=[[{source}]]
''')
        update = self.host.call({'op':'update','delta':{
            'version':1,'delta':True,'actions':[],'display':{'size':[800,600]}}})
        self.assertTrue(update['ok'],update)
        gui = self.host.call({'op':'render'})['frame']['gui']
        row = next(item for item in gui if item['name']=='StyledBox')
        self.assertEqual(row['text'],source)
        self.assertEqual(row['contentText'],'ABCD')
        a,b,c,d = row['richRuns']
        self.assertEqual(a['style']['stroke'],{
            'color':[48,128,255],'thickness':0.1,'transparency':0.25,
            'joins':'bevel','sizing':'scaled'})
        self.assertEqual(a['fontFace']['weight'],700)
        self.assertEqual(b['style']['stroke'],{
            'color':[0,0,0],'thickness':1,'transparency':0,
            'joins':'round','sizing':'fixed'})
        self.assertEqual(c['style']['stroke'],a['style']['stroke'])
        self.assertNotIn('stroke',d['style'])
