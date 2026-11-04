"""Model/part pivot geometry and persistent batched transforms."""
import copy
import os
from pathlib import Path
import unittest
from unittest import mock
import test_cframe_transforms as transforms


class ModelPivotTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def host(self):
        rust=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(transforms.DEFAULT_HOSTS[1])))
        with mock.patch.object(transforms.persistent_runtime,'RUST_HOST',rust):
            h=transforms.persistent_runtime.PersistentLuauHost(copy.deepcopy(transforms.SNAPSHOT))
        self.addCleanup(h.close);return h

    def execute(self,h,source):
        r=h.call({'op':'execute','source':transforms.HELPERS+source,'timeout_ms':1000})
        self.assertTrue(r['ok'],r);self.assertTrue(r['completed'],r);self.assertFalse(r['task_errors'],r)
        return r

    def test_part_pivot_offset_and_derived_coordinates(self):
        self.run_script('''
local p=Instance.new("Part")
p.CFrame=CFrame.new(3,4,5)*CFrame.Angles(.2,.3,.4)
p.PivotOffset=CFrame.new(2,1,-3)*CFrame.Angles(.1,-.4,.2)
frame(p:GetPivot(),p.CFrame*p.PivotOffset)
local target=CFrame.new(20,30,40)*CFrame.Angles(-.5,.6,.7)
assert(select('#',p:PivotTo(target))==0)
frame(p:GetPivot(),target);frame(p.CFrame,target*p.PivotOffset:Inverse())
vec(p.Position,p.CFrame.Position)
local x,y,z=p.CFrame:ToOrientation();vec(p.Orientation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
x,y,z=p.CFrame:ToEulerAnglesXYZ();vec(p.Rotation,Vector3.new(math.deg(x),math.deg(y),math.deg(z)))
''')

    def test_new_model_bounds_follow_contents_until_pivot_is_set(self):
        self.run_script('''
local m=Instance.new("Model")
frame(m:GetPivot(),CFrame.identity)
local a,b=Instance.new("Part",m),Instance.new("Part",m)
a.Size=Vector3.new(2,2,2);b.Size=Vector3.new(4,4,4)
b.CFrame=CFrame.new(10,0,0)
frame(m:GetPivot(),CFrame.new(5.5,0,0));frame(m.WorldPivot,m:GetPivot())
local box,size=m:GetBoundingBox();frame(box,CFrame.new(5.5,0,0));vec(size,Vector3.new(13,4,4));vec(m:GetExtentsSize(),size)
b.Position=Vector3.new(20,0,0);frame(m:GetPivot(),CFrame.new(10.5,0,0))
m.WorldPivot=CFrame.new(100,0,0)
b.Position=Vector3.new(30,0,0);frame(m:GetPivot(),CFrame.new(100,0,0))
''')

    def test_primary_part_pivot_and_model_transform(self):
        self.run_script('''
local m=Instance.new("Model")
local a,b=Instance.new("Part",m),Instance.new("Part",m)
a.CFrame=CFrame.new(5,0,0)*CFrame.Angles(.2,.4,.1)
b.CFrame=CFrame.new(10,3,2)*CFrame.Angles(.3,.1,.6)
a.PivotOffset=CFrame.new(1,2,3)*CFrame.Angles(0,.5,0)
m.PrimaryPart=a;m.WorldPivot=CFrame.new(-100,0,0)
frame(m:GetPivot(),a:GetPivot())
local relative=a.CFrame:ToObjectSpace(b.CFrame)
local target=CFrame.new(40,20,-10)*CFrame.Angles(-.4,.8,.2)
m:PivotTo(target)
frame(m:GetPivot(),target);frame(a.CFrame,target*a.PivotOffset:Inverse())
frame(a.CFrame:ToObjectSpace(b.CFrame),relative)
''')

    def test_bounds_follow_pivot_orientation(self):
        self.run_script('''
local m=Instance.new("Model");local p=Instance.new("Part",m)
p.Size=Vector3.new(4,2,2)
p.CFrame=CFrame.new(10,3,-2)*CFrame.Angles(0,0,math.pi/4)
local box,size=m:GetBoundingBox();vec(box.Position,p.Position);vec(size,Vector3.new(3*math.sqrt(2),3*math.sqrt(2),2))
m.WorldPivot=CFrame.Angles(0,0,math.pi/4)
box,size=m:GetBoundingBox();frame(box,p.CFrame);vec(size,p.Size)
''')

    def test_nested_models_parts_and_cameras_move_once(self):
        self.run_script('''
local root=Instance.new("Model");root.WorldPivot=CFrame.new(1,2,3)
local child=Instance.new("Model",root);child.WorldPivot=CFrame.new(5,6,7)
local p=Instance.new("Part",child);p.CFrame=CFrame.new(4,3,2)
local nested=Instance.new("Part",p);nested.CFrame=CFrame.new(9,8,7)
local camera=Instance.new("Camera",root);camera.CFrame=CFrame.new(2,4,6)
local target=CFrame.new(20,30,40)*CFrame.Angles(.2,.3,.4)
local delta=target*root:GetPivot():Inverse()
local cp,pp,np,cf=child:GetPivot(),p.CFrame,nested.CFrame,camera.CFrame
root:PivotTo(target)
frame(root:GetPivot(),target);frame(child:GetPivot(),delta*cp)
frame(p.CFrame,delta*pp);frame(nested.CFrame,delta*np)
frame(camera.CFrame,delta*cf);frame(camera.CoordinateFrame,camera.CFrame)
''')

    def test_uninitialized_and_explicit_model_pivots_survive_clone(self):
        self.run_script('''
local m=Instance.new("Model");local p=Instance.new("Part",m);p.CFrame=CFrame.new(4,0,0)
local c=m:Clone();c.Part.CFrame=CFrame.new(10,0,0);frame(c:GetPivot(),CFrame.new(10,0,0))
m.WorldPivot=CFrame.new(20,0,0)
c=m:Clone();c.Part.CFrame=CFrame.new(30,0,0);frame(c:GetPivot(),CFrame.new(20,0,0))
m.PrimaryPart=p;c=m:Clone();assert(c.PrimaryPart==c.Part);frame(c:GetPivot(),c.Part:GetPivot())
''')

    def test_unknown_descendant_transform_does_not_partially_move_model(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        identity={'$cframe':{'position':[0,0,0],'rotation':[1,0,0,0,1,0,0,0,1]}}
        data['nodes'] += [
            {'id':'m','class_name':'Model','name':'M','parent':'scope:workspace','properties':{'PrimaryPart':None,'WorldPivot':identity}},
            {'id':'a','class_name':'Part','name':'A','parent':'m','properties':{'CFrame':identity,'Size':{'$vector3':[2,2,2]},'PivotOffset':identity}},
            {'id':'b','class_name':'Part','name':'B','parent':'m','properties':{'CFrame':{'$cframe':{'position':[10,0,0],'rotation_id':17}},'Size':{'$vector3':[2,2,2]},'PivotOffset':identity}},
        ]
        results=self.run_script('''
local m=workspace.M
assert(type(m.PivotTo)=="function" and type(m.GetBoundingBox)=="function")
assert(not pcall(function()m:PivotTo(CFrame.new(20,0,0))end))
assert(not pcall(function()m:GetBoundingBox()end))
frame(m.A.CFrame,CFrame.identity);frame(m.WorldPivot,CFrame.identity)
''',data)
        for r in results:self.assertFalse(any(m.get('id') in {'m','a','b'} for m in r['mutations']))

    def test_invalid_receivers_and_primary_part_types(self):
        self.run_script('''
local m=Instance.new("Model");local p=Instance.new("Part",m)
for _,bad in {
 function()m.PrimaryPart=Instance.new("Folder",m)end,
 function()m:PivotTo(Vector3.zero)end,
 function()m.GetPivot(Instance.new("Folder"))end,
 function()m.GetBoundingBox(p)end,
} do assert(not pcall(bad))end
m.PrimaryPart=p;frame(m:GetPivot(),p:GetPivot())
''')

    def test_explicit_pivot_writes_fill_missing_received_properties(self):
        data=copy.deepcopy(transforms.SNAPSHOT)
        self.run_script('''
local m=game:GetService("Players").LocalPlayer.Character
local p=m.HumanoidRootPart
assert(not pcall(function()p:GetPivot()end))
m.WorldPivot=CFrame.new(2,3,4)
m.PrimaryPart=nil;frame(m:GetPivot(),CFrame.new(2,3,4))
p.PivotOffset=CFrame.new(1,0,0)
m.PrimaryPart=p
frame(m:GetPivot(),p.CFrame*CFrame.new(1,0,0))
''',data)

    def test_pivot_suppresses_derived_transform_events_but_updates_mass_observers(self):
        h=self.host()
        self.execute(h,'''getgenv().m=Instance.new("Model");getgenv().p=Instance.new("Part",m)
getgenv().events={};getgenv().massCalls=0
getgenv().positionCalls=0;getgenv().orientationCalls=0;getgenv().rotationCalls=0
p.Changed:Connect(function(key)
 if key=="AssemblyCenterOfMass" then
  massCalls+=1;vec(p.AssemblyCenterOfMass,p.Position)
 else table.insert(events,key)end
end)
p:GetPropertyChangedSignal("Position"):Connect(function()positionCalls+=1 end)
p:GetPropertyChangedSignal("Orientation"):Connect(function()orientationCalls+=1 end)
p:GetPropertyChangedSignal("Rotation"):Connect(function()rotationCalls+=1 end)
m:PivotTo(CFrame.new(10,20,30)*CFrame.Angles(0,.5,0))''')
        self.assertEqual(self.execute(h,'return #events,events[1],positionCalls,orientationCalls,rotationCalls,massCalls')['values'],[1,'CFrame',0,0,0,1])

    def test_repeated_moves_retain_offsets_and_external_changes_invalidate_cache(self):
        h=self.host()
        self.execute(h,'''getgenv().m=Instance.new("Model");m.WorldPivot=CFrame.new(1,2,3)*CFrame.Angles(.3,.7,.2)
getgenv().a=Instance.new("Part",m);getgenv().b=Instance.new("Part",m)
a.CFrame=CFrame.new(7,4,-2)*CFrame.Angles(.2,.4,.8);b.CFrame=CFrame.new(-3,5,9)*CFrame.Angles(.9,.1,.2)
getgenv().initial=m:GetPivot();getgenv().initialA=a.CFrame;getgenv().initialB=b.CFrame''')
        for batch in range(12):
            self.execute(h,'''for i=1,6 do m:PivotTo(CFrame.new(i*10,-i,i/3)*CFrame.Angles(i/7,i/3,i/5));m:PivotTo(initial)end
local x,y={a.CFrame:GetComponents()},{initialA:GetComponents()}
for i=1,12 do assert(math.abs(x[i]-y[i])<1e-10)end''')
        self.execute(h,'''b.CFrame=b.CFrame+Vector3.new(5,6,7)
local offset=m:GetPivot():ToObjectSpace(b.CFrame)
local target=CFrame.new(50,60,70)*CFrame.Angles(.1,.2,.3)
m:PivotTo(target);frame(b.CFrame,target*offset)
local newPart=Instance.new("Part",m);newPart.CFrame=CFrame.new(8,9,10)
local extra=m:GetPivot():ToObjectSpace(newPart.CFrame)
m:PivotTo(initial);frame(newPart.CFrame,initial*extra)''')

    def test_mutation_budget_failure_is_atomic(self):
        h=self.host()
        self.execute(h,'''getgenv().m=Instance.new("Model");m.WorldPivot=CFrame.identity
getgenv().p=Instance.new("Part",m)
getgenv().filler=Instance.new("Folder")''')
        self.execute(h,'''local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()m:PivotTo(CFrame.new(100,0,0))end))
frame(m:GetPivot(),CFrame.identity);frame(p.CFrame,CFrame.identity)''')


if __name__=='__main__':unittest.main()
