"""Native GUI defaults and additional tween goal types in actual Luau workers."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_motion_math as motion

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((ROOT/'tests/fixtures/references/gui-tween-studio-reference.json').read_text())


@unittest.skipUnless(motion.RUST_HOST.is_file(),'build the persistent worker first')
class GuiTweenGoalTests(unittest.TestCase):
    setUp=motion.MotionMathTests.setUp
    execute=motion.MotionMathTests.execute

    def test_ten_control_size_defaults_match_studio(self):
        self.execute('local classes=game:GetService("HttpService"):JSONDecode([==['+json.dumps(REFERENCE['classes'])+']==])\n'+'''
for name,reference in pairs(classes)do
 local object=Instance.new(name)
 local s=object.Size;local values={s.X.Scale,s.X.Offset,s.Y.Scale,s.Y.Offset}
 assert(typeof(s)=="UDim2")
 for i,value in ipairs(values)do assert(value==reference.size[i],name.." Size component "..i)end
 if reference.sliceCenter then
  local r=object.SliceCenter
  assert(typeof(r)=="Rect" and r.Min==Vector2.zero and r.Max==Vector2.zero)
  assert(object.SliceScale==reference.sliceScale)
 end
 object:Destroy()
end
''')

    def test_zero_rect_normalization_clone_and_pinned_constructors(self):
        self.execute('''
local zero=Rect.new()
assert(zero==Rect.new(0,0,0,0) and zero.Width==0 and zero.Height==0)
assert(not pcall(function()zero.Min=Vector2.one end))
local image=Instance.new("ImageLabel")
assert(image.SliceCenter==zero)
local normalized=Rect.new(9,12,1,2)
image.SliceCenter=normalized
local clone=image:Clone()
Rect=nil;Vector2=nil
assert(clone.SliceCenter==normalized and clone.SliceCenter.Width==8)
clone:Destroy();image:Destroy()
''')

    def test_rect_progress_changes_all_corners_and_fires_signals(self):
        self.execute('''
local image=Instance.new("ImageLabel")
local changed=0
image:GetPropertyChangedSignal("SliceCenter"):Connect(function()changed+=1 end)
local target=Rect.new(10,20,40,50)
local t=game:GetService("TweenService"):Create(image,TweenInfo.new(.18,Enum.EasingStyle.Linear),{SliceCenter=target})
t:Play();task.wait(.04)
local r=image.SliceCenter
assert(r.Min.X>0 and r.Min.X<10)
assert(math.abs(r.Min.Y-2*r.Min.X)<1e-5)
assert(math.abs(r.Max.X-4*r.Min.X)<1e-5 and math.abs(r.Max.Y-5*r.Min.X)<1e-5)
assert(t.Completed:Wait()==Enum.PlaybackState.Completed)
assert(image.SliceCenter==target and changed>1)
t:Destroy();image:Destroy()
''')

    def test_boolean_tweens_follow_studio_nonzero_conversion(self):
        self.execute('''
local service=game:GetService("TweenService")
for _,initial in ipairs({false,true})do
 local part=Instance.new("Part");part.Anchored=initial;part.Transparency=0
 local tween=service:Create(part,TweenInfo.new(.18,Enum.EasingStyle.Linear),{Anchored=not initial,Transparency=1})
 tween:Play();assert(part.Anchored==initial)
 task.wait(.04)
 local alpha=part.Transparency
 assert(alpha>0 and alpha<1)
 local start=initial and 1 or 0;local goal=initial and 0 or 1
 assert(part.Anchored==(start+(goal-start)*alpha~=0))
 assert(part.Anchored==true)
 assert(tween.Completed:Wait()==Enum.PlaybackState.Completed)
 assert(part.Anchored==not initial and part.Transparency==1)
 tween:Destroy();part:Destroy()
end
''')

    def test_boolean_delay_and_reverse_preserve_typed_endpoints(self):
        self.execute('''
local value=Instance.new("BoolValue")
local t=game:GetService("TweenService"):Create(value,TweenInfo.new(.1,Enum.EasingStyle.Linear,Enum.EasingDirection.Out,0,true,.05),{Value=true})
t:Play();task.wait(.02);assert(value.Value==false)
task.wait(.07);assert(value.Value==true)
assert(t.Completed:Wait()==Enum.PlaybackState.Completed)
assert(value.Value==false)
t:Destroy();value:Destroy()
''')

    def test_rect_conflicting_tween_cancels_and_restarts_from_current_value(self):
        self.execute('''
local service=game:GetService("TweenService")
local image=Instance.new("ImageButton")
local first=service:Create(image,TweenInfo.new(.2),{SliceCenter=Rect.new(10,20,40,50)})
first:Play();task.wait(.035)
local held=image.SliceCenter
local goal=Rect.new(3,4,9,12)
local second=service:Create(image,TweenInfo.new(.04),{SliceCenter=goal})
second:Play()
assert(first.PlaybackState==Enum.PlaybackState.Cancelled and image.SliceCenter==held)
assert(second.Completed:Wait()==Enum.PlaybackState.Completed and image.SliceCenter==goal)
first:Destroy();second:Destroy();image:Destroy()
''')

    def test_wrong_types_and_spoofed_rects_reject_atomically(self):
        self.execute('''
local service=game:GetService("TweenService")
local image=Instance.new("ImageLabel")
assert(not pcall(function()image.SliceCenter=Vector2.zero end))
assert(not pcall(function()image.Size=Vector2.zero end))
assert(not pcall(function()service:Create(image,TweenInfo.new(.1),{Visible=false,SliceCenter=Vector2.zero})end))
assert(not pcall(function()service:Create(image,TweenInfo.new(.1),{SliceCenter={ ["$rect"]={0,0,1,1}}})end))
assert(not pcall(function()service:Create(image,TweenInfo.new(.1),{ImageTransparency=true})end))
assert(image.Visible==true and image.SliceCenter==Rect.new())
image:Destroy()
''')

    def test_authored_gui_demo_completes_without_size_initialization(self):
        result=self.execute((ROOT/'runtime/scripts/gui_tween_demo.luau').read_text())
        self.assertEqual(result['values'][0],'GUI tween completed')
        self.assertGreater(result['values'][1],1)
        self.assertGreater(result['values'][2],1)

    @unittest.skipUnless(motion.STANDALONE.is_file(),'build the standalone host first')
    def test_standalone_executes_gui_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'source.luau').write_text((ROOT/'runtime/scripts/gui_tween_demo.luau').read_text())
            (path/'world.json').write_text(json.dumps(motion.SNAPSHOT))
            completed=subprocess.run([str(motion.STANDALONE),'--source',str(path/'source.luau'),
                '--world-snapshot',str(path/'world.json'),'--timeout-ms','2000'],capture_output=True,timeout=10)
            result=json.loads(completed.stdout)
            self.assertEqual(completed.returncode,0,result.get('error'))
            self.assertTrue(result['ok'],result.get('error'))
            self.assertFalse(result.get('task_errors'),result.get('task_errors'))
            self.assertEqual(result['values'][0],'GUI tween completed')


if __name__=='__main__':unittest.main()
