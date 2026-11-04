"""Legacy model movement on real native hosts, with offline physics integration."""
import copy
import unittest

import test_cframe_transforms as transforms
import test_model_pivots as pivots
from test_part_transforms import pose


SETUP='''
local m=Instance.new("Model",workspace)
local function part(parent,cf)
 local p=Instance.new("Part",parent);p.CFrame=cf;return p
end
local a=part(m,CFrame.new(2,3,4)*CFrame.Angles(.2,.3,.4))
local nested=Instance.new("Model",m)
local folder=Instance.new("Folder",nested)
local b=part(folder,CFrame.new(-5,8,10)*CFrame.Angles(-.4,.5,-.6))
local beforeA,beforeB=a.CFrame,b.CFrame
'''


def received_model(incomplete=False):
    data=copy.deepcopy(transforms.SNAPSHOT)
    data['nodes'] += [
        {'id':'legacy:model','class_name':'Model','name':'Legacy','parent':'scope:workspace','properties':{}},
        {'id':'legacy:a','class_name':'Part','name':'A','parent':'legacy:model','properties':{'CFrame':pose((1,2,3))}},
        {'id':'legacy:b','class_name':'Part','name':'B','parent':'legacy:model','properties':{'CFrame':
            {'$cframe':{'position':[4,5,6],'rotation_id':17}} if incomplete else pose((4,5,6))}},
    ]
    return data


class ModelMovementTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source):return self.run_script(SETUP+source)

    def test_translation_uses_world_axes_and_preserves_exact_rotation(self):
        self.check('''
m.PrimaryPart=a;a.PivotOffset=CFrame.new(50,60,70)*CFrame.Angles(.8,.9,1)
local delta=Vector3.new(10,-20,30)
local size=a.Size
assert(select('#',m:TranslateBy(delta))==0)
frame(a.CFrame,beforeA+delta);frame(b.CFrame,beforeB+delta)
assert(a.CFrame.Rotation==beforeA.Rotation and b.CFrame.Rotation==beforeB.Rotation)
assert(a.Size==size)
''')

    def test_translation_does_not_need_size_primary_or_pivot_offset_metadata(self):
        self.run_script('''
local m=workspace.Legacy
assert(not pcall(function()return m:GetPivot()end))
m:TranslateBy(Vector3.new(10,20,30))
vec(m.A.Position,Vector3.new(11,22,33));vec(m.B.Position,Vector3.new(14,25,36))
assert(client.propertyOrigin(m,"PrimaryPart")=="unavailable")
''',received_model())

    def test_primary_methods_use_raw_frame_and_leave_pivot_offset_unchanged(self):
        self.check('''
m.PrimaryPart=a
local offset=CFrame.new(9,8,7)*CFrame.Angles(.7,.8,.9);a.PivotOffset=offset
frame(m:GetPrimaryPartCFrame(),beforeA)
local target=CFrame.new(100,200,300)*CFrame.Angles(-.2,.4,.9)
local expected=target*beforeA:Inverse()*beforeB
assert(select('#',m:SetPrimaryPartCFrame(target))==0)
assert(a.CFrame==target);frame(b.CFrame,expected)
frame(m:GetPrimaryPartCFrame(),target);frame(m:GetPivot(),target*offset)
assert(a.PivotOffset==offset)
''')

    def test_primary_methods_do_not_require_size_or_pivot_offset_metadata(self):
        data=received_model();data['nodes'][-3]['properties']['PrimaryPart']={'$ref':'legacy:a'}
        self.run_script('''
local m=workspace.Legacy;frame(m:GetPrimaryPartCFrame(),CFrame.new(1,2,3))
local target=CFrame.new(10,20,30)*CFrame.Angles(.2,.5,.8)
m:SetPrimaryPartCFrame(target)
frame(m.A.CFrame,target);frame(m.B.CFrame,target*CFrame.new(3,3,3))
''',data)

    def test_nested_rigid_parts_and_attachments_move_exactly_once(self):
        self.check('''
local weld=Instance.new("Weld",a);weld.Part0=a;weld.Part1=b
weld.C0=beforeA:ToObjectSpace(beforeB)
local at=Instance.new("Attachment",b);at.CFrame=CFrame.new(1,2,3)
m.PrimaryPart=a
local target=CFrame.new(20,40,60)*CFrame.Angles(.3,.7,.9)
local expected=target*beforeA:Inverse()*beforeB
m:SetPrimaryPartCFrame(target);m:TranslateBy(Vector3.new(5,6,7))
frame(a.CFrame,target+Vector3.new(5,6,7));frame(b.CFrame,expected+Vector3.new(5,6,7))
frame(at.WorldCFrame,b.CFrame*at.CFrame)
frame(a.CFrame*weld.C0,b.CFrame*weld.C1)
''')

    def test_destination_obstructions_do_not_lift_translated_model(self):
        self.check('''
local block=part(workspace,CFrame.new(12,23,34));block.Size=Vector3.new(100,100,100)
block.Anchored=true
m:TranslateBy(Vector3.new(10,20,30))
frame(a.CFrame,beforeA+Vector3.new(10,20,30));frame(b.CFrame,beforeB+Vector3.new(10,20,30))
frame(block.CFrame,CFrame.new(12,23,34))
''')

    def test_explicit_nested_pivots_move_and_untouched_pivots_still_follow_bounds(self):
        self.check('''
m.WorldPivot=CFrame.new(1,2,3)*CFrame.Angles(.3,.5,.7)
local saved=m.WorldPivot;local derived=nested:GetPivot()
m:TranslateBy(Vector3.new(10,20,30))
frame(m.WorldPivot,saved+Vector3.new(10,20,30));frame(nested:GetPivot(),derived+Vector3.new(10,20,30))
assert(client.propertyOrigin(nested,"WorldPivot")~="local")
local old=nested:GetPivot();local extra=part(nested,CFrame.new(1000,0,0))
assert(nested:GetPivot()~=old)
nested.WorldPivot=CFrame.new(6,7,8);local oldNested=nested.WorldPivot
m.PrimaryPart=a;local oldA=a.CFrame
local target=CFrame.new(9,8,7)*CFrame.Angles(-.3,.6,.2);local delta=target*oldA:Inverse()
local oldWorld=m.WorldPivot;m:SetPrimaryPartCFrame(target)
frame(m.WorldPivot,delta*oldWorld);frame(nested.WorldPivot,delta*oldNested)
''')

    def test_empty_models_and_worldmodel_inheritance(self):
        self.run_script('''
local empty=Instance.new("Model");empty:TranslateBy(Vector3.new(10,20,30))
frame(empty:GetPivot(),CFrame.identity)
empty.WorldPivot=CFrame.new(1,2,3);empty:TranslateBy(Vector3.new(10,20,30))
frame(empty:GetPivot(),CFrame.new(11,22,33))
local model=Instance.new("WorldModel");local p=Instance.new("Part",model);model.PrimaryPart=p
model:SetPrimaryPartCFrame(CFrame.new(7,8,9));model:TranslateBy(Vector3.new(1,2,3))
frame(model:GetPrimaryPartCFrame(),CFrame.new(8,10,12))
''')

    def test_nonparts_and_external_parts_are_not_model_movement_targets(self):
        self.check('''
local outside=part(workspace,CFrame.new(100,200,300))
local camera=Instance.new("Camera",m);camera.CFrame=CFrame.new(5,6,7)
m.PrimaryPart=a;m:SetPrimaryPartCFrame(CFrame.new(20,30,40));m:TranslateBy(Vector3.one)
frame(outside.CFrame,CFrame.new(100,200,300));frame(camera.CFrame,CFrame.new(5,6,7))
assert(b.Parent==folder and a.Parent==m)
''')

    def test_missing_destroyed_and_external_primary_do_not_partially_move_model(self):
        self.check('''
assert(not pcall(function()return m:GetPrimaryPartCFrame()end))
assert(not pcall(function()m:SetPrimaryPartCFrame(CFrame.new(100,0,0))end))
local p=part(m,CFrame.identity);m.PrimaryPart=p;p:Destroy()
assert(not pcall(function()return m:GetPrimaryPartCFrame()end))
assert(not pcall(function()m:SetPrimaryPartCFrame(CFrame.identity)end))
local outside=part(workspace,CFrame.new(1,2,3));m.PrimaryPart=outside
frame(m:GetPrimaryPartCFrame(),outside.CFrame)
assert(not pcall(function()m:SetPrimaryPartCFrame(CFrame.identity)end))
frame(a.CFrame,beforeA);frame(b.CFrame,beforeB);frame(outside.CFrame,CFrame.new(1,2,3))
''')

    def test_invalid_argument_receiver_and_nonfinite_input_leave_world_unchanged(self):
        self.check('''
m.PrimaryPart=a
for _,bad in {
 function()m:TranslateBy(CFrame.identity)end,
 function()m:TranslateBy(nil)end,
 function()m:TranslateBy(Vector3.new(math.huge,0,0))end,
 function()m:TranslateBy(Vector3.new(0/0,0,0))end,
 function()m.TranslateBy(a,Vector3.one)end,
 function()m:TranslateBy(Vector3.one,1)end,
 function()m:SetPrimaryPartCFrame(Vector3.one)end,
 function()m:SetPrimaryPartCFrame(CFrame.new(math.huge,0,0))end,
 function()m:SetPrimaryPartCFrame(CFrame.identity,1)end,
 function()m.GetPrimaryPartCFrame(a)end,
 function()m:GetPrimaryPartCFrame(1)end,
} do assert(not pcall(bad));frame(a.CFrame,beforeA);frame(b.CFrame,beforeB)end
local dead=Instance.new("Model");local move=dead.TranslateBy;dead:Destroy()
assert(not pcall(move,dead,Vector3.one))
''')

    def test_missing_later_pose_rejects_entire_batch(self):
        data=received_model(incomplete=True)
        data['nodes'][-3]['properties'].update(PrimaryPart={'$ref':'legacy:a'},WorldPivot=pose((8,9,10)))
        results=self.run_script('''
local m=workspace.Legacy
assert(not pcall(function()m:TranslateBy(Vector3.new(10,20,30))end))
assert(not pcall(function()m:SetPrimaryPartCFrame(CFrame.new(100,200,300))end))
frame(m.A.CFrame,CFrame.new(1,2,3));frame(m.WorldPivot,CFrame.new(8,9,10))
vec(m.B.Position,Vector3.new(4,5,6))
''',data)
        for result in results:self.assertFalse(any(m.get('id','').startswith('legacy:') for m in result['mutations']))

    def test_movement_between_pivot_calls_invalidates_cached_offsets(self):
        self.check('''
m.PrimaryPart=a;local first=CFrame.new(30,40,50)*CFrame.Angles(.3,.4,.5)
m:PivotTo(first);m:TranslateBy(Vector3.new(8,9,10))
m:SetPrimaryPartCFrame(CFrame.new(15,25,35)*CFrame.Angles(.7,.8,.9))
local offset=a.CFrame:ToObjectSpace(b.CFrame)
m:PivotTo(CFrame.new(100,200,300)*CFrame.Angles(.2,.1,.4))
frame(b.CFrame,a.CFrame*offset)
''')


class PersistentModelMovementTests(unittest.TestCase):
    host=pivots.ModelPivotTests.host
    execute=pivots.ModelPivotTests.execute

    def test_property_callbacks_observe_whole_batch_and_correct_transform_signals(self):
        h=self.host()
        self.execute(h,SETUP+'''
getgenv().m=m;getgenv().a=a;getgenv().b=b;m.PrimaryPart=a
getgenv().calls={CFrame=0,Position=0,Orientation=0,Rotation=0}
getgenv().expectedA=a.CFrame+Vector3.new(10,20,30)
getgenv().expectedB=b.CFrame+Vector3.new(10,20,30)
for _,p in {a,b} do for key in calls do
 p:GetPropertyChangedSignal(key):Connect(function()
  frame(a.CFrame,expectedA);frame(b.CFrame,expectedB);calls[key]+=1
 end)
end end
m:TranslateBy(Vector3.new(10,20,30))
''')
        self.assertEqual(self.execute(h,'return calls.CFrame,calls.Position,calls.Orientation,calls.Rotation')['values'],[2,2,0,0])
        self.execute(h,'''local target=CFrame.new(70,80,90)*CFrame.Angles(.7,.8,.9)
expectedB=target*a.CFrame:Inverse()*b.CFrame;expectedA=target
m:SetPrimaryPartCFrame(target)''')
        self.assertEqual(self.execute(h,'return calls.CFrame,calls.Position,calls.Orientation,calls.Rotation')['values'],[4,4,2,2])

    def test_budget_failure_does_not_move_parts_or_explicit_pivots(self):
        h=self.host()
        self.execute(h,'''m=Instance.new("Model");p=Instance.new("Part",m);m.PrimaryPart=p
m.WorldPivot=CFrame.new(1,2,3);filler=Instance.new("Folder")''')
        self.execute(h,'''local i=0
while pcall(function()i+=1;filler.Name=tostring(i)end)do end
assert(not pcall(function()m:TranslateBy(Vector3.new(100,0,0))end))
assert(not pcall(function()m:SetPrimaryPartCFrame(CFrame.new(200,0,0))end))
frame(p.CFrame,CFrame.identity);frame(m.WorldPivot,CFrame.new(1,2,3))''')

    def test_noop_does_not_consume_mutation_budget_or_emit_events(self):
        h=self.host()
        self.execute(h,'''m=Instance.new("Model");p=Instance.new("Part",m);m.PrimaryPart=p
calls=0;p.Changed:Connect(function()calls+=1 end)''')
        result=self.execute(h,'''for i=1,1000 do
m:TranslateBy(Vector3.zero);m:SetPrimaryPartCFrame(CFrame.identity)
end''')
        self.assertEqual(result['mutations'],[])
        self.assertEqual(self.execute(h,'return calls')['values'],[0])

    def test_new_received_primary_pose_supersedes_local_model_movement(self):
        data=received_model();data['nodes'][-3]['properties']['PrimaryPart']={'$ref':'legacy:a'}
        h=transforms.persistent_runtime.PersistentLuauHost(data);self.addCleanup(h.close)
        self.execute(h,'''m=workspace.Legacy
m:SetPrimaryPartCFrame(CFrame.new(10,20,30));m:TranslateBy(Vector3.new(5,6,7))
frame(m:GetPrimaryPartCFrame(),CFrame.new(15,26,37))''')
        result=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':data['sequence']+100,'kind':'set','id':'legacy:a','fields':{'CFrame':pose((2,4,6))}}]}})
        self.assertTrue(result['ok'],result)
        self.execute(h,'''frame(m:GetPrimaryPartCFrame(),CFrame.new(2,4,6))
assert(client.propertyOrigin(m.A,"CFrame")=="received")
m:TranslateBy(Vector3.new(1,2,3));frame(m:GetPrimaryPartCFrame(),CFrame.new(3,6,9))''')


if __name__=='__main__':unittest.main()
