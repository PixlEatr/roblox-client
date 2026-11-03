"""Derived assembly properties after edits to received rigid parts."""
import copy
import unittest
import test_cframe_transforms as transforms
import test_joint_poses as poses
from test_part_transforms import pose


def snapshot(joined=False):
    data=copy.deepcopy(transforms.SNAPSHOT)
    for name,mass,x in [('A',2,0),('B',3,10),('C',4,20)]:
        data['nodes'].append({'id':name,'name':name,'class_name':'Part',
            'parent':'scope:workspace','properties':{
                'Mass':mass,'AssemblyMass':5 if joined and name!='C' else mass,
                'CenterOfMass':{'$vector3':[0,0,0]},
                'AssemblyCenterOfMass':{'$vector3':[6 if joined and name!='C' else x,0,0]},
                'Size':{'$vector3':[1,1,1]},'Shape':1,'Material':256,
                'MaterialVariant':'','CustomPhysicalProperties':None,
                'CFrame':pose((x,0,0)),'Anchored':False,'Massless':False,
                'RootPriority':100 if name=='A' else 0,
                'AssemblyLinearVelocity':{'$vector3':[0,0,0]},
                'AssemblyAngularVelocity':{'$vector3':[0,2,0]}}})
    if joined:
        data['nodes'].append({'id':'W','name':'W','class_name':'WeldConstraint',
            'parent':'A','properties':{'Part0':{'$ref':'A'},'Part1':{'$ref':'B'},'Enabled':True}})
    return data


class ReceivedMassEditTests(unittest.TestCase):
    run_script=transforms.CFrameTransformTests.run_script
    host=poses.JointPoseTests.host
    execute=poses.JointPoseTests.execute

    def test_created_weld_and_destroy_recalculate_both_members(self):
        self.run_script('''
local a,b=workspace.A,workspace.B
near(a.AssemblyMass,2);near(b.AssemblyMass,3)
local w=Instance.new("WeldConstraint",a);w.Part0=a;w.Part1=b
near(a.AssemblyMass,5);near(b.AssemblyMass,5)
vec(a.AssemblyCenterOfMass,Vector3.new(6,0,0))
vec(b:GetVelocityAtPosition(Vector3.new(10,0,0)),Vector3.new(0,0,-8))
assert(client.propertyOrigin(a,"AssemblyMass")=="local")
w:Destroy();near(a.AssemblyMass,2);near(b.AssemblyMass,3)
vec(b.AssemblyCenterOfMass,Vector3.new(10,0,0))
''',snapshot=snapshot())

    def test_received_weld_disable_retarget_and_detach(self):
        self.run_script('''
local a,b,c=workspace.A,workspace.B,workspace.C;local w=a.W
w.Enabled=false;near(a.AssemblyMass,2);near(b.AssemblyMass,3)
w.Enabled=true;near(a.AssemblyMass,5)
w.Part1=c;near(a.AssemblyMass,6);near(c.AssemblyMass,6);near(b.AssemblyMass,3)
vec(a.AssemblyCenterOfMass,Vector3.new(40/3,0,0))
w.Parent=nil;near(a.AssemblyMass,2);near(c.AssemblyMass,4)
w.Parent=a;near(a.AssemblyMass,6)
c:Destroy();near(a.AssemblyMass,2)
''',snapshot=snapshot(True))

    def test_geometry_density_and_massless_changes(self):
        self.run_script('''
local a,b=workspace.A,workspace.B
b.Size=Vector3.new(2,3,4);near(b.Mass,16.8);near(b:GetMass(),16.8)
near(a.AssemblyMass,18.8)
b.CustomPhysicalProperties=PhysicalProperties.new(2,0,0);near(a.AssemblyMass,50)
b.Massless=true;near(a.AssemblyMass,2);vec(a.AssemblyCenterOfMass,Vector3.zero)
b.Massless=false;a.Anchored=true;assert(b.AssemblyMass==math.huge)
a.Anchored=false;near(a.AssemblyMass,50)
''',snapshot=snapshot(True))

    def test_pose_updates_center_and_point_velocity(self):
        self.run_script('''
local a=workspace.A;a.CFrame=CFrame.new(4,0,0)
vec(a.AssemblyCenterOfMass,Vector3.new(4,0,0))
vec(a:GetVelocityAtPosition(Vector3.new(5,0,0)),Vector3.new(0,0,-2))
near(a.AssemblyMass,2)
''',snapshot=snapshot())

    def test_chained_world_reads_are_live_without_getgenv(self):
        self.run_script('''
near(workspace.A.Mass,2)
workspace.A.Size=Vector3.new(2,2,2)
near(workspace.A.Mass,5.6)
workspace.A.Size=Vector3.one
near(workspace.A.Mass,.7)
''',snapshot=snapshot())

    def test_missing_mass_is_not_replaced_by_stale_assembly_value(self):
        data=snapshot(True)
        next(n for n in data['nodes'] if n['id']=='B')['properties'].pop('Mass')
        self.run_script('''local a=workspace.A;a.W.Enabled=true
assert(not pcall(function()return a.AssemblyMass end))''',snapshot=data)

    def test_reparenting_joint_container_updates_separated_members(self):
        data=snapshot(True)
        data['nodes'][-1]['parent']='folder'
        data['nodes'].append({'id':'folder','name':'Joints','class_name':'Folder',
                              'parent':'scope:workspace','properties':{}})
        self.run_script('''
workspace.Joints.Parent=nil
near(workspace.A.AssemblyMass,2);near(workspace.B.AssemblyMass,3)
''',snapshot=data)

    def test_new_receipt_supersedes_local_geometry_and_mass(self):
        h=self.host(snapshot())
        self.execute(h,'getgenv().a=workspace.A;a.Size=Vector3.new(2,2,2);near(a.Mass,5.6)')
        result=h.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':transforms.SNAPSHOT['sequence']+1,'kind':'set','id':'A',
             'fields':{'Size':{'$vector3':[3,3,3]},'Mass':19,'AssemblyMass':19}}]}})
        self.assertTrue(result['ok'],result)
        self.execute(h,'near(a.Mass,19);near(a.AssemblyMass,19);assert(client.propertyOrigin(a,"Mass")=="received")')

    def test_unrelated_edits_do_not_invalidate_incomplete_received_assembly(self):
        data=snapshot(True)
        data['nodes'][-1]['properties'].pop('Enabled')
        self.run_script('''
local f=Instance.new("Folder",workspace)
workspace.C.Size=Vector3.new(2,2,2)
near(workspace.A.AssemblyMass,5)
assert(client.propertyOrigin(workspace.A,"AssemblyMass")=="received")
''',snapshot=data)

    def test_received_derived_signals_and_direct_receipt_are_not_duplicated(self):
        h=self.host(snapshot(True))
        self.execute(h,'''getgenv().a=workspace.A;getgenv().changes=0
a:GetPropertyChangedSignal("AssemblyMass"):Connect(function()changes+=1 end)
a.W.Enabled=false;task.wait(.001);assert(changes==1)
task.wait(.001);assert(changes==1)''')
        h2=self.host(snapshot())
        self.execute(h2,'''getgenv().a=workspace.A;getgenv().changes=0
a:GetPropertyChangedSignal("AssemblyMass"):Connect(function()changes+=1 end)''')
        result=h2.call({'op':'update','delta':{'version':1,'delta':True,'actions':[
            {'sequence':transforms.SNAPSHOT['sequence']+1,'kind':'set','id':'A','fields':{'AssemblyMass':9}}]}})
        self.assertTrue(result['ok'],result)
        self.execute(h2,'task.wait(.001);near(a.AssemblyMass,9);assert(changes==1,tostring(changes))')


if __name__=='__main__':unittest.main()
