"""Actual Luau TextService methods and font-service host round trips."""
import copy
import json
import math
from pathlib import Path
import time
import unittest

from PIL import ImageFont
import test_cframe_transforms as transforms
import test_motion_math as motion
from persistent_runtime import PersistentLuauHost

FONT=Path('/Applications/Roblox.app/Contents/Resources/content/fonts/BuilderSans-Regular.otf')


class TextParameterTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def test_parameter_font_assignments_are_typed_and_copied(self):
        self.run_script('''
local params=Instance.new("GetTextBoundsParams")
local font=Font.fromName("BuilderSans");params.Font=font;font.Bold=true
assert(not params.Font.Bold and params.Font==Font.fromName("BuilderSans"))
assert(params:Clone().Font==params.Font)
for _,bad in ipairs({Enum.Font.SourceSans,"BuilderSans",{},Vector2.zero})do
 assert(not pcall(function()params.Font=bad end))
end
assert(not pcall(function()params.Font=nil end))
local service=game:GetService("TextService")
assert(service==game:GetService("TextService") and service:IsA("Instance"))
''')


@unittest.skipUnless(FONT.is_file(),'installed Roblox font fixture needed')
class TextServiceTests(unittest.TestCase):
    setUp=motion.MotionMathTests.setUp
    execute=motion.MotionMathTests.execute

    def test_family_metadata_has_canonical_enum_items_and_warm_calls_do_not_yield(self):
        result=self.execute('''
local service=game:GetService("TextService")
local info=service:GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")
assert(info.Name=="Builder Sans" and #info.Faces==7)
for _,face in ipairs(info.Faces)do
 assert(typeof(face.Weight)=="EnumItem" and face.Weight.EnumType==Enum.FontWeight)
 assert(typeof(face.Style)=="EnumItem" and face.Style.EnumType==Enum.FontStyle)
end
info.Name="Changed";info.Faces[1].Name="Changed"
local thread=coroutine.create(function()
 local fresh=service:GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")
 assert(fresh.Name=="Builder Sans" and fresh.Faces[1].Name~="Changed")
 return fresh.Faces[1].Weight
end)
local ok,weight=coroutine.resume(thread)
assert(ok and coroutine.status(thread)=="dead" and rawequal(weight,Enum.FontWeight.Thin))
return info.Faces[3].Weight.Name
''')
        self.assertEqual(result['values'],['Regular'])

    def test_bounds_match_actual_font_measurement_and_reuse_typed_cached_results(self):
        result=self.execute('''
local params=Instance.new("GetTextBoundsParams");params.Font=Font.fromName("BuilderSans")
params.Size=20;params.Text="iiiiiiii"
local service=game:GetService("TextService")
local narrow=service:GetTextBoundsAsync(params)
params.Text="WWWWWWWW";local wide=service:GetTextBoundsAsync(params)
assert(typeof(narrow)=="Vector2" and wide.X>narrow.X*3 and wide.Y==20)
local thread=coroutine.create(function()return service:GetTextBoundsAsync(params)end)
local ok,cached=coroutine.resume(thread)
assert(ok and coroutine.status(thread)=="dead" and cached==wide)
return narrow,wide
''')
        font=ImageFont.truetype(str(FONT),size=20)
        self.assertEqual(result['values'],[{'$vector2':[math.ceil(font.getlength(text)),20]} for text in ['iiiiiiii','WWWWWWWW']])

    def test_wrapping_newlines_and_empty_text(self):
        self.execute('''
local params=Instance.new("GetTextBoundsParams");params.Font=Font.fromName("BuilderSans");params.Size=20
local service=game:GetService("TextService")
params.Text="A long string with several words";params.Width=0
local unwrapped=service:GetTextBoundsAsync(params)
params.Width=60;local wrapped=service:GetTextBoundsAsync(params)
assert(wrapped.Y>unwrapped.Y and wrapped.X<=60)
params.Text="hello\\nhello";params.Width=0;assert(service:GetTextBoundsAsync(params).Y==40)
params.Text="";assert(service:GetTextBoundsAsync(params)==Vector2.zero)
''')

    def test_asset_failure_is_catchable_and_the_next_request_succeeds(self):
        self.execute('''
local service=game:GetService("TextService")
local ok,err=pcall(function()return service:GetFamilyInfoAsync("rbxasset://fonts/families/DefinitelyMissingFixtureFont.json")end)
assert(not ok and type(err)=="string")
local info=service:GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")
assert(info.Name=="Builder Sans")
''')

    def test_bad_parameters_fail_before_any_host_request(self):
        result=self.execute('''
local service=game:GetService("TextService")
local params=Instance.new("GetTextBoundsParams")
assert(not pcall(function()return service:GetTextBoundsAsync(params)end))
params.Font=Font.fromName("BuilderSans")
for _,change in ipairs({function()params.Size=0 end,function()params.Size=math.huge end,
 function()params.Size=20;params.Width=-1 end,function()params.Width=0;params.Text=string.char(255) end,
 function()params.Text=string.rep("x",4097)end})do
 change();assert(not pcall(function()return service:GetTextBoundsAsync(params)end))
end
assert(not pcall(function()return service:GetFamilyInfoAsync({})end))
assert(not pcall(function()return service:GetTextBoundsAsync(Instance.new("Folder"))end))
''')
        self.assertFalse(result.get('text_service_requests'))

    def test_rich_bounds_use_parsed_font_sizes_and_face_variants(self):
        result = self.execute('''
local service=game:GetService("TextService")
local params=Instance.new("GetTextBoundsParams")
params.Font=Font.fromName("BuilderSans")
params.RichText=true
params.Text='<b>WW</b><br/><font size="32">iii</font>'
local styled=service:GetTextBoundsAsync(params)
params.Text='A<font color="#ff0000">V</font>'
local colored=service:GetTextBoundsAsync(params)
params.Text='AV'
assert(service:GetTextBoundsAsync(params)==colored)
params.Text='&lt;b&gt;'
local escaped=service:GetTextBoundsAsync(params)
params.RichText=false;params.Text='<b>'
assert(service:GetTextBoundsAsync(params)==escaped)
return styled
''')
        bold = ImageFont.truetype(str(FONT.with_name('BuilderSans-Bold.otf')),size=20)
        large = ImageFont.truetype(str(FONT),size=32)
        width = math.ceil(max(bold.getlength('WW'),large.getlength('iii')))
        self.assertEqual(result['values'],[{'$vector2':[width,52]}])

    def test_rich_bounds_measure_case_expansion_and_small_cap_font_sizes(self):
        result = self.execute('''
local service=game:GetService("TextService")
local params=Instance.new("GetTextBoundsParams")
params.Font=Font.fromName("BuilderSans")
params.Size=20
params.RichText=true
params.Text='<uc>straße</uc>'
local uppercase=service:GetTextBoundsAsync(params)
params.Text='STRASSE'
assert(service:GetTextBoundsAsync(params)==uppercase)
params.Text='<smallcaps>Aß</smallcaps><br/><font size="30"><sc>w</sc></font>'
return service:GetTextBoundsAsync(params)
''')
        regular = ImageFont.truetype(str(FONT),size=20)
        small = ImageFont.truetype(str(FONT),size=16)
        large_small = ImageFont.truetype(str(FONT),size=24)
        width = math.ceil(max(regular.getlength('A')+small.getlength('SS'),large_small.getlength('W')))
        self.assertEqual(result['values'],[{'$vector2':[width,44]}])


class NativeTextProtocolTests(unittest.TestCase):
    def setUp(self):
        self.host=PersistentLuauHost(copy.deepcopy(motion.SNAPSHOT),text_service_factory=False);self.addCleanup(self.host.close)

    def test_request_snapshots_parameters_and_valid_response_resumes_the_original_job(self):
        initial=self.host.call({'op':'execute','source':'''getgenv().params=Instance.new("GetTextBoundsParams")
params.Font=Font.fromName("BuilderSans");params.Text="original";params.Size=20
return game:GetService("TextService"):GetTextBoundsAsync(params)''','timeout_ms':1000})
        self.assertTrue(initial['ok'],initial);self.assertFalse(initial['completed'])
        request=initial['text_service_requests'][0];self.assertEqual(request['text'],'original')
        self.host.call({'op':'execute','source':'params.Text="changed";params.Font=Font.fromName("Arimo")','timeout_ms':1000})
        invalid=self.host.call({'op':'poll','text_service_responses':[{'request_id':request['request_id'],'result':[-1,20]}]})
        self.assertFalse(invalid['ok'],invalid)
        valid=self.host.call({'op':'poll','text_service_responses':[{'request_id':request['request_id'],'result':[77,20]}],'timeout_ms':100})
        self.assertTrue(valid['ok'],valid)
        completed=next(job for job in valid['completed_jobs'] if job['job_id']==initial['job_id'])
        self.assertEqual(completed['values'],[{'$vector2':[77,20]}])
        self.assertFalse(valid['active_text_service_requests'])

    def test_cancelled_script_releases_its_pending_text_request(self):
        result=self.host.call({'op':'execute','source':'''getgenv().waiting=task.spawn(function()
 game:GetService("TextService"):GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")
end)
task.wait(0)
task.cancel(waiting)
return true''','timeout_ms':1000})
        saw_request=bool(result.get('text_service_requests'))
        for _ in range(10):
            result=self.host.call({'op':'poll','timeout_ms':100})
            self.assertTrue(result['ok'],result)
            saw_request=saw_request or bool(result.get('text_service_requests'))
            if not result.get('active_text_service_requests'):break
        self.assertTrue(saw_request)
        self.assertFalse(result['active_text_service_requests'])

    def test_pending_text_requests_do_not_block_unrelated_script_jobs(self):
        pending=self.host.call({'op':'execute','source':'return game:GetService("TextService"):GetFamilyInfoAsync("rbxasset://fonts/families/BuilderSans.json")','timeout_ms':1000})
        self.assertFalse(pending['completed'])
        start=time.monotonic()
        next_job=self.host.call({'op':'execute','source':'return 6*7','timeout_ms':1000})
        self.assertTrue(next_job['ok'],next_job);self.assertEqual(next_job['values'],[42])
        self.assertLess(time.monotonic()-start,1)
        self.assertEqual(next_job['active_text_service_requests'],pending['active_text_service_requests'])


if __name__=='__main__':unittest.main()
