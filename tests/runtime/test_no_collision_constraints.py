"""Pair-exclusion queries in both native hosts, with received topology updates."""
import unittest
import test_cframe_transforms as transforms
import test_world_raycast as raycast
import test_mouse_picking as picking
from persistent_runtime import PersistentLuauHost

SETUP='''
local a=Instance.new("Part",workspace);local b=Instance.new("Part",workspace)
local c=Instance.new("Part",workspace)
local function link(x,y,parent)
 local n=Instance.new("NoCollisionConstraint",parent or workspace)
 n.Part0=x;n.Part1=y;return n
end
local function contains(list,value)for _,v in list do if v==value then return true end end;return false end
'''


class NoCollisionConstraintTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script

    def check(self,source,data=None):
        return self.run_script(SETUP+source,data or raycast.snapshot())

    def test_pair_query_is_symmetric_and_does_not_connect_rigid_parts(self):
        self.check('''
assert(#a:GetNoCollisionConstraints()==0)
local ab=link(a,b);local bc=link(b,c)
assert(#a:GetNoCollisionConstraints()==1 and a:GetNoCollisionConstraints()[1]==ab)
local middle=b:GetNoCollisionConstraints()
assert(#middle==2 and contains(middle,ab) and contains(middle,bc))
assert(#c:GetNoCollisionConstraints()==1 and c:GetNoCollisionConstraints()[1]==bc)
assert(#a:GetConnectedParts(true)==0 and #a:GetJoints()==0)
local old=b.CFrame;a.CFrame=CFrame.new(10,20,30);frame(b.CFrame,old)
middle[1]=nil;assert(#b:GetNoCollisionConstraints()==2)
''')

    def test_enabled_endpoints_destroy_and_duplicate_constraints(self):
        self.check('''
local n=link(a,b);local duplicate=link(b,a)
assert(#a:GetNoCollisionConstraints()==2)
n.Enabled=false;assert(#a:GetNoCollisionConstraints()==1)
duplicate:Destroy();assert(#a:GetNoCollisionConstraints()==0)
n.Enabled=true;n.Part1=nil;assert(#a:GetNoCollisionConstraints()==0)
n.Part1=c;assert(#a:GetNoCollisionConstraints()==1 and #b:GetNoCollisionConstraints()==0)
n.Part1=a;assert(#a:GetNoCollisionConstraints()==0)
n.Part1=b;b:Destroy();assert(#a:GetNoCollisionConstraints()==0)
''')

    def test_containers_and_worldmodel_isolation(self):
        self.check('''
local n=link(a,b);local model=Instance.new("WorldModel",workspace)
n.Parent=nil;assert(#a:GetNoCollisionConstraints()==0)
n.Parent=a;assert(#a:GetNoCollisionConstraints()==1)
b.Parent=model;assert(#a:GetNoCollisionConstraints()==0 and #b:GetNoCollisionConstraints()==0)
a.Parent=model;assert(#a:GetNoCollisionConstraints()==1)
n.Parent=workspace;assert(#a:GetNoCollisionConstraints()==0)
n.Parent=model;assert(#b:GetNoCollisionConstraints()==1)
model:Destroy();assert(#a:GetNoCollisionConstraints()==0)
''')

    def test_endpoint_types_are_checked_before_mutation(self):
        self.check('''
local n=link(a,b)
for _,value in {workspace,Instance.new("Folder"),Instance.new("Attachment",a),1,"part",{}} do
 assert(not pcall(function()n.Part1=value end))
 assert(n.Part1==b and #a:GetNoCollisionConstraints()==1)
end
assert(not pcall(function()n.Enabled=1 end))
local query=a.GetNoCollisionConstraints
assert(not pcall(query,workspace))
''')

    def test_constraint_does_not_suppress_unrelated_raycast(self):
        self.check('''
a.CFrame=CFrame.new(100,100,100);b.CFrame=CFrame.new(0,0,-10);b.Size=Vector3.new(4,4,4)
c.Parent=nil;local n=link(a,b)
assert(workspace:Raycast(Vector3.zero,Vector3.new(0,0,-100)).Instance==b)
''')

    def test_clone_keeps_pair_references_and_is_registered_only_when_parented(self):
        self.check('''
local n=link(a,b);local copy=n:Clone()
assert(copy.Part0==a and copy.Part1==b and copy.Enabled)
assert(#a:GetNoCollisionConstraints()==1)
copy.Parent=workspace;assert(#a:GetNoCollisionConstraints()==2)
copy.Enabled=false;assert(#a:GetNoCollisionConstraints()==1)
''')


class ReceivedNoCollisionConstraintTests(unittest.TestCase):
    execute=picking.MousePickingTests.execute

    def start(self):
        data=raycast.snapshot()
        self.initial_sequence=data.get('sequence',0)
        a=picking.node('Part');a['properties'].update(Shape=1,Material=256)
        b={**a,'id':'pick:second','name':'Second'}
        n={'id':'pair:1','class_name':'NoCollisionConstraint','name':'Pair','parent':'scope:workspace',
           'properties':{'Enabled':True,'Part0':{'$ref':'pick:test'},'Part1':{'$ref':'pick:second'}}}
        data['nodes'].extend([a,b,n])
        self.host=PersistentLuauHost(data);self.addCleanup(self.host.close)
        self.execute('a=workspace.PickTest;b=workspace.Second;n=workspace.Pair;assert(#a:GetNoCollisionConstraints()==1)')

    def update(self,sequence,fields):
        result=self.host.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':self.initial_sequence+sequence,'kind':'set','id':'pair:1','fields':fields}]}})
        self.assertTrue(result['ok'],result)

    def test_received_enable_endpoint_and_parent_updates_rebuild_query(self):
        self.start();self.update(1,{'Enabled':False})
        self.execute('assert(#a:GetNoCollisionConstraints()==0)')
        self.update(2,{'Enabled':True,'Part1':None})
        self.execute('assert(#a:GetNoCollisionConstraints()==0)')
        self.update(3,{'Part1':{'$ref':'pick:second'}})
        self.execute('assert(#a:GetNoCollisionConstraints()==1 and #b:GetNoCollisionConstraints()==1)')
        self.update(4,{'Parent':None});self.execute('assert(#a:GetNoCollisionConstraints()==0)')

    def test_unknown_related_endpoint_reports_error_then_recovers(self):
        self.start();self.update(1,{'Part1':{'$ref':'not-yet-received'}})
        self.execute('local ok,err=pcall(function()return a:GetNoCollisionConstraints()end);assert(not ok and string.find(err,"unresolved"))')
        self.update(2,{'Part1':{'$ref':'pick:second'}})
        self.execute('assert(#a:GetNoCollisionConstraints()==1)')


if __name__=='__main__':unittest.main()
