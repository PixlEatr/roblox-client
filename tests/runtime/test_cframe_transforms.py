"""Execute authored CFrame scripts in compiled hosts, without a live session."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import persistent_runtime
from local_runtime import HOST, RUST_HOST
DEFAULT_HOSTS = [HOST, RUST_HOST]
HOSTS = [Path(p) for p in os.environ['CFRAME_TEST_HOSTS'].split(os.pathsep)] \
    if 'CFRAME_TEST_HOSTS' in os.environ else DEFAULT_HOSTS
SNAPSHOT = json.loads((ROOT / 'tests/fixtures/world_snapshot.json').read_text())
HELPERS = '''
local function near(a,b) assert(math.abs(a-b)<2e-5, tostring(a).." != "..tostring(b)) end
local function vec(a,b) near(a.X,b.X); near(a.Y,b.Y); near(a.Z,b.Z) end
local function frame(a,b)
    local aa,bb={a:GetComponents()},{b:GetComponents()}
    for i=1,12 do near(aa[i],bb[i]) end
end
'''


class CFrameTransformTests(unittest.TestCase):
    def run_script(self, source, snapshot=None):
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'source.luau').write_text(HELPERS + source + '\nreturn true')
            (path / 'snapshot.json').write_text(json.dumps(snapshot or SNAPSHOT))
            for host in HOSTS:
                with self.subTest(host=str(host)):
                    completed = subprocess.run(
                        [str(host), '--source', str(path/'source.luau'),
                         '--world-snapshot', str(path/'snapshot.json')],
                        capture_output=True, timeout=10)
                    result = json.loads(completed.stdout)
                    self.assertEqual(completed.returncode, 0, result)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['values'], [True], result)
                    self.assertFalse(result.get('task_errors'), result)
                    results.append(result)
        return results

    def test_rotation_constructors_and_aliases(self):
        self.run_script('''
local q=math.pi/2
vec(CFrame.Angles(q,0,0)*Vector3.yAxis,Vector3.zAxis)
vec(CFrame.Angles(0,q,0)*Vector3.xAxis,-Vector3.zAxis)
vec(CFrame.Angles(0,0,q)*Vector3.xAxis,Vector3.yAxis)
local x,y,z=CFrame.Angles(.4,0,0),CFrame.Angles(0,.7,0),CFrame.Angles(0,0,-.8)
frame(CFrame.Angles(.4,.7,-.8),x*y*z)
frame(CFrame.fromEulerAnglesXYZ(.4,.7,-.8),x*y*z)
frame(CFrame.fromEulerAnglesYXZ(.4,.7,-.8),y*x*z)
frame(CFrame.fromOrientation(.4,.7,-.8),y*x*z)
frame(CFrame.fromAxisAngle(Vector3.yAxis,q),CFrame.Angles(0,q,0))
vec(CFrame.fromAxisAngle(Vector3.new(1,1,1),2*math.pi/3)*Vector3.xAxis,Vector3.yAxis)
''')

    def test_composition_inverse_and_translation(self):
        self.run_script('''
local a=CFrame.new(10,20,30)*CFrame.Angles(0,math.pi/2,0)
local b=CFrame.new(2,3,4)
vec((a*b).Position,Vector3.new(14,23,28))
vec((b*a).Position,Vector3.new(12,23,34))
frame(a*a:Inverse(),CFrame.identity)
frame(a:Inverse()*a,CFrame.identity)
frame((a*b):Inverse(),b:Inverse()*a:Inverse())
local translated=a+Vector3.new(3,-2,5)
vec(translated.Position,Vector3.new(13,18,35))
frame(translated-Vector3.new(3,-2,5),a)
frame(translated.Rotation,a.Rotation)
near(a.X,10);near(a.Y,20);near(a.Z,30)
vec(a.RightVector,a.XVector);vec(a.UpVector,a.YVector)
vec(a.LookVector,-a.ZVector)
vec(a.Rotation.Position,Vector3.zero)
''')

    def test_points_vectors_and_variadic_frame_conversions(self):
        self.run_script('''
local a=CFrame.new(10,20,30)*CFrame.Angles(0,math.pi/2,0)
local p,q=Vector3.new(2,3,4),Vector3.new(-3,2,1)
local wp,wq=a:PointToWorldSpace(p,q)
vec(wp,Vector3.new(14,23,28));vec(wq,Vector3.new(11,22,33))
local rp,rq=a:PointToObjectSpace(wp,wq);vec(rp,p);vec(rq,q)
local vp,vq=a:VectorToWorldSpace(p,q)
vec(vp,Vector3.new(4,3,-2));vec(vq,Vector3.new(1,2,3))
rp,rq=a:VectorToObjectSpace(vp,vq);vec(rp,p);vec(rq,q)
local b,c=CFrame.new(p),CFrame.new(q)*CFrame.Angles(.5,.2,.1)
local wb,wc=a:ToWorldSpace(b,c)
frame(wb,a*b);frame(wc,a*c)
local rb,rc=a:ToObjectSpace(wb,wc);frame(rb,b);frame(rc,c)
assert(select('#',a:ToWorldSpace())==0)
assert(select('#',a:PointToObjectSpace())==0)
local many=table.create(200,Vector3.xAxis)
local converted={a:PointToWorldSpace(table.unpack(many))}
assert(#converted==200);vec(converted[200],Vector3.new(10,20,29))
''')

    def test_local_instance_writes_and_attributes_keep_rotation(self):
        results = self.run_script('''
local p=Instance.new("Part")
p.CFrame=CFrame.new(3,4,5)*CFrame.Angles(0,math.pi/2,0)
vec(p.Position,Vector3.new(3,4,5))
vec(p.CFrame.LookVector,Vector3.new(-1,0,0))
p.Position=Vector3.new(6,7,8)
vec(p.CFrame.Position,Vector3.new(6,7,8))
vec(p.CFrame.LookVector,Vector3.new(-1,0,0))
p:SetAttribute("pose",p.CFrame:Inverse())
frame(p:Clone():GetAttribute("pose"),p.CFrame:Inverse())
''')
        for result in results:
            updates = [m['value']['$cframe'] for m in result['mutations']
                       if m.get('property') == 'CFrame']
            self.assertEqual(updates[-1]['position'], [6, 7, 8])
            self.assertAlmostEqual(updates[-1]['rotation'][2], 1)

    def test_unknown_rotation_is_preserved_or_rejected(self):
        snapshot = copy.deepcopy(SNAPSHOT)
        root = next(n for n in snapshot['nodes'] if n['id'] == 'scope:root-1')
        root['properties']['CFrame'] = {'$cframe': {
            'position': [1,2,3], 'rotation_id': 'packed:unknown'}}
        results = self.run_script('''
local p=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local a=p.CFrame
for _,f in {
    function()return a:Inverse()end,
    function()return a.Rotation end,
    function()return a*CFrame.identity end,
    function()return CFrame.identity*a end,
    function()return a:PointToWorldSpace(Vector3.zero)end,
    function()return a:VectorToObjectSpace(Vector3.zero)end,
} do local ok,err=pcall(f);assert(not ok and string.find(err,"unavailable"))end
p.CFrame=a+Vector3.new(4,5,6)
vec(p.CFrame.Position,Vector3.new(5,7,9))
assert(not pcall(function()return p.CFrame.LookVector end))
''', snapshot)
        for result in results:
            frame = next(m['value']['$cframe'] for m in result['mutations']
                         if m.get('property') == 'CFrame')
            self.assertEqual(frame, {'position':[5,7,9], 'rotation_id':'packed:unknown'})

    def test_invalid_arguments_fail_without_corrupting_vm(self):
        self.run_script('''
local a=CFrame.identity
for _,f in {
    function()return a*3 end,
    function()return a+a end,
    function()return a:ToObjectSpace(Vector3.zero)end,
    function()return a:PointToWorldSpace(a)end,
    function()return a.Inverse({})end,
    function()return CFrame.Angles(0,math.huge,0)end,
    function()return CFrame.fromAxisAngle(Vector3.zero,1)end,
    function()a.X=9 end,
} do assert(not pcall(f))end
frame(a,CFrame.identity)
vec(a*Vector3.xAxis,Vector3.xAxis)
''')

    def test_persistent_camera_script_handles_world_updates(self):
        rust_host = Path(os.environ.get('CFRAME_PERSISTENT_HOST', str(DEFAULT_HOSTS[1])))
        with mock.patch.object(persistent_runtime, 'RUST_HOST', rust_host):
            host = persistent_runtime.PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(host.close)
        first = host.call({'op':'execute', 'source':HELPERS + '''
getgenv().follow=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
getgenv().offset=CFrame.new(0,3,8)*CFrame.Angles(-.2,0,0)
getgenv().updates=0
follow:GetPropertyChangedSignal("CFrame"):Connect(function()
    workspace.CurrentCamera.CFrame=follow.CFrame*offset
    updates+=1
end)
return true
'''})
        self.assertTrue(first['ok'], first)
        updated = host.call({'op':'update', 'delta':{
            'version':1, 'delta':True, 'actions':[
                {'sequence':8, 'kind':'set', 'id':'scope:root-1', 'fields':{
                    'CFrame':{'$cframe':{'position':[10,20,30],
                        'rotation':[0,0,1, 0,1,0, -1,0,0]}}}}]}, 'timeout_ms':100})
        self.assertTrue(updated['ok'], updated)
        self.assertFalse(updated['task_errors'], updated)
        second = host.call({'op':'execute', 'source':HELPERS + '''
assert(updates==1)
vec(workspace.CurrentCamera.CFrame.Position,Vector3.new(18,23,30))
frame(follow.CFrame:ToObjectSpace(workspace.CurrentCamera.CFrame),offset)
return true
'''})
        self.assertTrue(second['ok'], second)
        self.assertEqual(second['values'], [True])


if __name__ == '__main__':
    unittest.main()
