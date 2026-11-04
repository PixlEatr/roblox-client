"""Native observed mouse sensitivity and frame delta semantics."""
import json
from pathlib import Path
import time
import unittest
import test_motion_math as motion
import test_input_events as inputs

ROOT=Path(__file__).resolve().parents[2]
REFERENCE=json.loads((ROOT/'tests/fixtures/references/mouse-sensitivity-studio-reference.json').read_text())


class MouseSensitivityTests(unittest.TestCase):
    setUp=inputs.InputEventTests.setUp
    execute=motion.MotionMathTests.execute
    deliver=inputs.InputEventTests.deliver

    def test_property_values_clamp_and_coercion_match_native_studio(self):
        source='''local u=game:GetService("UserInputService");assert(u.MouseDeltaSensitivity==1)
assert(client.propertyOrigin(u,"MouseDeltaSensitivity")=="local_input")
local cases=game:GetService("HttpService"):JSONDecode([==['''+json.dumps(REFERENCE['cases'])+''']==])
for _,case in ipairs(cases)do
 local v=case.input=="inf" and math.huge or case.input=="-inf" and -math.huge or case.input=="nan" and 0/0 or case.input=="true" and true or tonumber(case.input)
 u.MouseDeltaSensitivity=v
 local result=u.MouseDeltaSensitivity
 if case.value=="nan"then assert(result~=result)else assert(result==tonumber(case.value),case.input)end
end
for _,v in ipairs({false,{},function()end,"abc",Enum.KeyCode.W,Vector3.one})do u.MouseDeltaSensitivity=v;assert(u.MouseDeltaSensitivity==0)end
u.MouseDeltaSensitivity=nil;assert(u.MouseDeltaSensitivity==0)
u.MouseDeltaSensitivity="2";assert(u.MouseDeltaSensitivity==2)
u.MouseDeltaSensitivity=1.23456789;assert(u.MouseDeltaSensitivity==1.2345678806304932)
'''
        self.execute(source)

    def test_property_change_signal_and_unchanged_assignments(self):
        self.execute('''local u=game:GetService("UserInputService");local count=0
u:GetPropertyChangedSignal("MouseDeltaSensitivity"):Connect(function()count+=1 end)
u.MouseDeltaSensitivity=.5;u.MouseDeltaSensitivity=.5;u.MouseDeltaSensitivity=2
u.MouseDeltaSensitivity=25;u.MouseDeltaSensitivity=50
task.wait(.005);assert(count==3 and u.MouseDeltaSensitivity==10)''')

    def test_scaled_delta_applies_once_to_events_and_frame_samples(self):
        self.execute('''local u=game:GetService("UserInputService");u.MouseDeltaSensitivity=-2;u.MouseBehavior=Enum.MouseBehavior.LockCenter
u.InputChanged:Connect(function(i)eventDelta=i.Delta;eventPosition=i.Position end)
game:GetService("RunService"):BindToRenderStep("sensitivity",100,function()
 local d=u:GetMouseDelta();if d.Magnitude>0 then frameDelta=d end
end)''')
        self.deliver({'phase':'change','input_type':'MouseMovement','position':[50,80,0],'mouse_location':[50,80],'delta':[3,-4,0]})
        time.sleep(.025)
        self.execute('''game:GetService("RunService").PreRender:Wait()
assert(eventDelta==Vector3.new(-6,8,0) and eventPosition==Vector3.new(50,80,0))
assert(frameDelta==Vector2.new(-6,8));game:GetService("RunService"):UnbindFromRenderStep("sensitivity")''')

    def test_zero_sensitivity_keeps_motion_events_and_absolute_coordinates(self):
        self.execute('''local u=game:GetService("UserInputService");u.MouseDeltaSensitivity=0;zeroCalls=0
u.InputChanged:Connect(function(i)zeroCalls+=1;zeroDelta=i.Delta;zeroPosition=i.Position end)''')
        self.deliver({'phase':'change','input_type':'MouseMovement','position':[90,60,0],'delta':[10,20,0]})
        self.execute('assert(zeroCalls==1 and zeroDelta==Vector3.zero and zeroPosition==Vector3.new(90,60,0))')

    def test_nan_sensitivity_is_retained_and_default_behavior_delta_remains_zero(self):
        self.execute('''local u=game:GetService("UserInputService");u.MouseDeltaSensitivity=0/0
assert(u.MouseDeltaSensitivity~=u.MouseDeltaSensitivity and u:GetMouseDelta()==Vector2.zero)
u.MouseBehavior=Enum.MouseBehavior.LockCenter
local d=u:GetMouseDelta();assert(d.X~=d.X and d.Y~=d.Y)
u.MouseDeltaSensitivity=1;assert(u:GetMouseDelta()==Vector2.zero)''')

if __name__=='__main__':unittest.main()
