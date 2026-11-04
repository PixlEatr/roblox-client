"""Real Luau execution compared with pure-library probes in Roblox Studio."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from persistent_runtime import PersistentLuauHost, RUST_HOST

SNAPSHOT=json.loads((ROOT/'tests/fixtures/world_snapshot.json').read_text())
REFERENCE=json.loads((ROOT/'tests/fixtures/references/motion-math-studio-reference.json').read_text())
STANDALONE=Path(os.environ.get('MOTION_MATH_STANDALONE_HOST',ROOT/'runtime/build/luau-host'))


@unittest.skipUnless(RUST_HOST.is_file(),'build the persistent worker first')
class MotionMathTests(unittest.TestCase):
    def setUp(self):
        self.host=PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self,source):
        result=self.host.call({'op':'execute','source':source,'timeout_ms':1000})
        self.assertTrue(result['ok'],result.get('error'))
        self.assertFalse(result.get('task_errors'),result.get('task_errors'))
        if result.get('completed'):return result
        job=result['job_id'];deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=self.host.call({'op':'poll','timeout_ms':100})
            self.assertTrue(result['ok'],result.get('error'))
            self.assertFalse(result.get('task_errors'),result.get('task_errors'))
            for done in result.get('completed_jobs',[]):
                if done['job_id']==job:
                    self.assertTrue(done['ok'],done)
                    return done
            time.sleep(.005)
        self.fail('motion script did not complete')

    def reference(self,key):
        return 'local cases=game:GetService("HttpService"):JSONDecode([==['+json.dumps(REFERENCE[key])+']==])\n'

    def test_fuzzy_equality_matches_studio_for_all_three_datatypes(self):
        self.execute(self.reference('fuzzy')+'''
for i,c in ipairs(cases) do
 local ctor=c.kind=="Vector2" and function(x,y,z)return Vector2.new(x,y)end or c.kind=="Vector3" and Vector3.new or CFrame.new
 local ok,result=pcall(function()return ctor(c.a,0,0):FuzzyEq(ctor(c.b,0,0),c.epsilon)end)
 assert(ok==c.ok,"FuzzyEq acceptance case "..i)
 if ok then assert(result==c.value,"FuzzyEq result case "..i)end
end
''')

    def test_fuzzy_equality_checks_components_and_uses_default_epsilon(self):
        self.execute('''
for _,ctor in ipairs({function(x,y,z)return Vector2.new(x,y)end,Vector3.new,CFrame.new})do
 assert(ctor(0,0,0):FuzzyEq(ctor(.000001,0,0)))
 assert(not ctor(0,0,0):FuzzyEq(ctor(.001,0,0)))
 assert(ctor(0,0,0):FuzzyEq(ctor(.08,.08,.08),.1))
 assert(not ctor(100,0,0):FuzzyEq(ctor(100,.5,0),.001))
end
assert(not pcall(function()return Vector3.zero:FuzzyEq(Vector2.zero)end))
assert(not pcall(function()return CFrame.identity:FuzzyEq(Vector3.zero)end))
''')

    def test_cframe_rotation_comparison_matches_studio(self):
        self.execute(self.reference('frames')+'''
local function frame(v)return CFrame.new(v[1],v[2],v[3])*CFrame.Angles(v[4],v[5],v[6])end
for i,c in ipairs(cases)do
 assert(frame(c.a):FuzzyEq(frame(c.b),c.epsilon)==c.value,"rotation case "..i)
end
assert(not CFrame.identity:FuzzyEq(CFrame.Angles(.008,.008,.008),.01))
assert(not CFrame.identity:FuzzyEq(CFrame.fromAxisAngle(Vector3.one.Unit,.012),.01))
''')

    def test_vector2_angles_match_studio_and_require_boolean_sign_option(self):
        self.execute(self.reference('angles')+'''
for i,c in ipairs(cases)do
 local a,b=Vector2.new(unpack(c.a)),Vector2.new(unpack(c.b))
 assert(math.abs(a:Angle(b,c.signed)-c.value)<1e-6,"angle case "..i)
end
assert(math.abs(Vector2.xAxis:Angle(Vector2.yAxis)-math.pi/2)<1e-6)
assert(not pcall(function()return Vector2.xAxis:Angle(Vector2.yAxis,1)end))
''')

    def test_vector2_min_max_process_all_arguments(self):
        self.execute('''
local a,b,c,d=Vector2.new(1,8),Vector2.new(-2,10),Vector2.new(4,-3),Vector2.new(-7,20)
assert(a:Min(b,c,d)==Vector2.new(-7,-3))
assert(a:Max(b,c,d)==Vector2.new(4,20))
assert(a:Min()==a and a:Max()==a)
assert(not pcall(function()return a:Min(b,4)end))
''')

    def test_scalar_smoothing_matches_studio_including_overshoot_and_zero_dt(self):
        self.execute(self.reference('damp')+'''
local service=game:GetService("TweenService")
for i,c in ipairs(cases)do
 local a,b=service:SmoothDamp(unpack(c.args))
 assert(math.abs(a-c.value)<2e-5,"smoothed value case "..i..": "..a)
 assert(math.abs(b-c.velocity)<math.max(2e-5,math.abs(c.velocity)*2e-6),"velocity case "..i..": "..b)
end
''')

    def test_vector_smoothing_clamps_speed_by_length_and_preserves_type(self):
        ref=REFERENCE['vectorDamp']
        result=self.execute('''
local service=game:GetService("TweenService")
local a,b=service:SmoothDamp(Vector3.zero,Vector3.new(10,10,0),Vector3.zero,.5,2,.1)
local c,d=service:SmoothDamp(Vector2.zero,Vector2.new(10,10),Vector2.zero,.5,2,.1)
assert(typeof(a)=="Vector3" and typeof(b)=="Vector3")
assert(typeof(c)=="Vector2" and typeof(d)=="Vector2")
assert(math.abs(c.X-a.X)<1e-6 and math.abs(d.Y-b.Y)<1e-6)
return a.X,a.Y,a.Z,b.X,b.Y,b.Z
''')
        for actual,expected in zip(result['values'],ref['value']+ref['velocity']):
            self.assertAlmostEqual(actual,expected,delta=2e-6)

    def test_smoothing_defaults_and_invalid_inputs(self):
        self.execute('''
local s=game:GetService("TweenService")
local a,b=s:SmoothDamp(0,10,0,.5)
assert(a>0 and a<10 and b>0)
local v,w=s:SmoothDamp(0,10,0,.5,nil,.1)
assert(math.abs(v-.6156158447265625)<2e-5)
for _,args in ipairs({
 {0,Vector3.one,0,.5},{Vector2.zero,Vector2.one,Vector3.zero,.5},
 {CFrame.identity,Vector3.zero,CFrame.identity,.5},
 {0,10,0,0/0},{0,10,0,.5,0/0},{0,10,0,.5,1,math.huge},
 {math.huge,10,0,.5},{0,"10",0,.5}
})do assert(not pcall(function()s:SmoothDamp(unpack(args))end))end
''')

    def test_default_delta_matches_last_local_heartbeat(self):
        self.execute('''
local s=game:GetService("TweenService")
local dt=game:GetService("RunService").Heartbeat:Wait()
local a,b=s:SmoothDamp(0,10,0,.5)
local c,d=s:SmoothDamp(0,10,0,.5,nil,dt)
assert(math.abs(a-c)<1e-6 and math.abs(b-d)<1e-6)
''')

    def test_methods_survive_global_replacement(self):
        self.execute('''
local a,b,c=Vector2.xAxis,Vector3.zero,CFrame.identity
Vector2=nil;Vector3=nil;CFrame=nil;vector=nil
assert(a:FuzzyEq(a) and b:FuzzyEq(b) and c:FuzzyEq(c))
assert(a:Angle(a)==0)
''')

    def test_authored_motion_demo_completes_with_property_changes(self):
        result=self.execute((ROOT/'runtime/scripts/motion_math_demo.luau').read_text())
        self.assertEqual(result['values'][0],'Smoothed')
        self.assertGreater(result['values'][1],1)
        self.assertLess(result['values'][2],.01)

    @unittest.skipUnless(STANDALONE.is_file(),'build the standalone host first')
    def test_standalone_host_runs_studio_reference_cases_and_motion_demo(self):
        source=self.reference('fuzzy')+'''
for i,c in ipairs(cases)do
 local ctor=c.kind=="Vector2" and function(x,y,z)return Vector2.new(x,y)end or c.kind=="Vector3" and Vector3.new or CFrame.new
 local ok,value=pcall(function()return ctor(c.a,0,0):FuzzyEq(ctor(c.b,0,0),c.epsilon)end)
 assert(ok==c.ok and (not ok or value==c.value),"standalone fuzzy case "..i)
end
'''+self.reference('frames')+'''
local function frame(v)return CFrame.new(v[1],v[2],v[3])*CFrame.Angles(v[4],v[5],v[6])end
for i,c in ipairs(cases)do assert(frame(c.a):FuzzyEq(frame(c.b),c.epsilon)==c.value,"standalone rotation case "..i)end
'''+self.reference('damp')+'''
for i,c in ipairs(cases)do
 local a,b=game:GetService("TweenService"):SmoothDamp(unpack(c.args))
 assert(math.abs(a-c.value)<2e-5,"standalone smoothing case "..i)
end
'''+(ROOT/'runtime/scripts/motion_math_demo.luau').read_text()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'motion.luau').write_text(source)
            (path/'world.json').write_text(json.dumps(SNAPSHOT))
            completed=subprocess.run([str(STANDALONE),'--source',str(path/'motion.luau'),
                '--world-snapshot',str(path/'world.json'),'--timeout-ms','2000'],capture_output=True,timeout=10)
            result=json.loads(completed.stdout)
            self.assertEqual(completed.returncode,0,result)
            self.assertTrue(result['ok'],result.get('error'))
            self.assertFalse(result.get('task_errors'),result)
            self.assertEqual(result['values'][0],'Smoothed')
            self.assertGreater(result['values'][1],1)
            self.assertLess(result['values'][2],.01)


if __name__=='__main__':unittest.main()
