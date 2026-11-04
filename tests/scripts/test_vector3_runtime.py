"""Roblox Vector3 access on authored, received and native-generated vectors."""
import copy
import unittest

from persistent_runtime import PersistentLuauHost
from test_persistent_runtime import SNAPSHOT


class Vector3RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.host = PersistentLuauHost(copy.deepcopy(SNAPSHOT))
        self.addCleanup(self.host.close)

    def execute(self, source):
        result = self.host.call({'op': 'execute', 'source': source, 'timeout_ms': 1000})
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result.get('task_errors', []), [])
        return result['values']

    def test_defaults_components_and_roblox_type(self):
        self.assertEqual(self.execute('''
local a,b,c=Vector3.new(),Vector3.new(2),Vector3.new(2,3)
local v=Vector3.new(2,3,4)
local key="X"
return a==Vector3.zero,b==Vector3.new(2,0,0),c==Vector3.new(2,3,0),
    v[key],v.x,v.Y,v.z,typeof(v),typeof(vector.create(1,2,3))
'''), [True, True, True, 2, 2, 3, 4, 'Vector3', 'Vector3'])

    def test_magnitude_unit_dynamic_access_and_zero(self):
        values = self.execute('''
local v=Vector3.new(3,4,12)
local key="Magnitude"
local zero=Vector3.zero.Unit
return v.Magnitude,v[key],v.magnitude,math.abs(v.Unit.Magnitude-1)<1e-6,
    (v.Unit*13-v).Magnitude<1e-5,v.unit==v.Unit,
    zero.X~=zero.X,zero.Y~=zero.Y,zero.Z~=zero.Z
''')
        self.assertEqual(values, [13, 13, 13] + [True] * 6)

    def test_dot_cross_and_signed_angles(self):
        self.assertEqual(self.execute('''
local x,y,z=Vector3.xAxis,Vector3.yAxis,Vector3.zAxis
local a,b=Vector3.new(1,2,3),Vector3.new(4,5,6)
local cross=a:Cross(b)
return a:Dot(b),x:Cross(y)==z,y:Cross(x)==-z,
    cross:Dot(a)==0,cross:Dot(b)==0,
    math.abs((x*7):Angle(y*4)-math.pi/2)<1e-6,
    math.abs(y:Angle(x,z)+math.pi/2)<1e-6,
    math.abs(x:Angle(y,-z)+math.pi/2)<1e-6,
    math.abs(x:Angle(-x)-math.pi)<1e-6,a:dot(b)==32,x:cross(y)==z
'''), [32] + [True] * 10)

    def test_lerp_extrapolates_and_extracted_methods_work(self):
        self.assertEqual(self.execute('''
local a,b=Vector3.new(2,4,6),Vector3.new(6,8,10)
local lerp=a.Lerp
return lerp(a,b,0)==a,a:Lerp(b,1)==b,a:Lerp(b,.5)==Vector3.new(4,6,8),
    a:Lerp(b,2)==Vector3.new(10,12,14),a:Lerp(b,-1)==Vector3.new(-2,0,2),
    a:lerp(b,.5)==a:Lerp(b,.5)
'''), [True] * 6)

    def test_component_methods_and_native_arithmetic(self):
        self.assertEqual(self.execute('''
local v=Vector3.new(-2.6,5.1,0)
local a,b=Vector3.new(2,6,12),Vector3.new(1,2,3)
return v:Abs()==Vector3.new(2.6,5.1,0),v:Floor()==Vector3.new(-3,5,0),
    v:Ceil()==Vector3.new(-2,6,0),v:Sign()==Vector3.new(-1,1,0),
    a:Min(Vector3.new(3,4,20))==Vector3.new(2,4,12),
    a:Max(Vector3.new(3,4,20))==Vector3.new(3,6,20),
    a/b==Vector3.new(2,3,4),a*b==Vector3.new(2,12,36),
    2*b==b*2,(a-b+b)==a,(-a).Magnitude==a.Magnitude
'''), [True] * 11)

    def test_cframe_outputs_and_received_property_updates(self):
        self.execute('''
getgenv().part=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
getgenv().lengths={}
part:GetPropertyChangedSignal("Position"):Connect(function()
    table.insert(lengths,part.Position.Magnitude)
end)
''')
        response = self.host.call({'op': 'update', 'timeout_ms': 100, 'delta': {
            'version': 1, 'delta': True, 'actions': [
                {'sequence': 8, 'kind': 'set', 'id': 'scope:root-1',
                 'fields': {'Position': {'$vector3': [3, 4, 12]}}}]}})
        self.assertTrue(response['ok'])
        self.assertEqual(self.execute('''
local cf=CFrame.new(1,2,3)*CFrame.Angles(0,math.pi/2,0)
local v=Vector3.xAxis
return part.Position.Magnitude,lengths[1],
    (cf*v-Vector3.new(1,2,2)).Magnitude<1e-5,
    (cf:PointToObjectSpace(cf*v)-v).Magnitude<1e-5,
    (cf:Inverse()*cf).Position.Magnitude<1e-5,
    cf.LookVector.Unit.Magnitude==1
'''), [13, 13] + [True] * 4)

    def test_vectors_survive_bindable_and_attribute_roundtrips(self):
        self.assertEqual(self.execute('''
local folder=Instance.new("Folder")
folder:SetAttribute("Direction",Vector3.new(3,4,0))
local f=Instance.new("BindableFunction")
f.OnInvoke=function(v)return v.Unit,v.Magnitude end
local unit,length=f:Invoke(folder:GetAttribute("Direction"))
return length,math.abs(unit.Magnitude-1)<1e-6,typeof(unit),
    folder:GetAttribute("Direction"):Dot(Vector3.xAxis)
'''), [5, True, 'Vector3', 3])

    def test_invalid_receivers_unknown_members_and_immutability(self):
        self.assertEqual(self.execute('''
local v=Vector3.one
local calls={function()return v:Dot({})end,function()return v.Dot({},v)end,
    function()return v:Cross(1)end,function()return v:Lerp(v,{})end,
    function()return v:Angle(v,{})end,function()return v.NotAProperty end,
    function()v.X=4 end,function()Vector3.zero=v end,
    function()return Vector3.new(1,2,3,4)end}
local errors=0
for _,f in calls do if not pcall(f)then errors+=1 end end
return errors,math.abs(v.Magnitude-math.sqrt(3))<1e-6,v.X==1
'''), [9, True, True])

    def test_captured_methods_survive_user_global_shadowing(self):
        self.assertEqual(self.execute('''
local v=Vector3.new(3,4,0)
getgenv().vector={magnitude=function()return 999 end,dot=function()return 999 end}
return v.Magnitude,v:Dot(Vector3.xAxis),v.Unit:Dot(v.Unit)>0.99999
'''), [5, 3, True])


if __name__ == '__main__':
    unittest.main()
