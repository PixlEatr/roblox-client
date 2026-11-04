"""ScaleTo behavior captured from Studio 0.745, replayed in both native hosts."""
import json
from pathlib import Path
import unittest

import test_cframe_transforms as native


FIXTURE=Path(__file__).resolve().parents[1]/'fixtures/model_scaling_studio745.json'
REFERENCE=json.loads(FIXTURE.read_text())


def number(value):
    return {'inf':'math.huge','-inf':'-math.huge','nan':'(0/0)'}.get(str(value),repr(value))


def value_literal(kind,value):
    if kind=='number':return number(value)
    return {'Vector3':'Vector3.new','Vector2':'Vector2.new','CFrame':'CFrame.new'}[kind]+'('+','.join(number(v) for v in value)+')'


SETUP='''
local m=Instance.new("Model",workspace)
local root=Instance.new("Part",m);root.Anchored=true;root.CFrame=CFrame.new(3,5,7);root.Size=Vector3.new(2,3,4)
local other=Instance.new("Part",m);other.Anchored=true;other.CFrame=CFrame.new(9,11,13)
m.PrimaryPart=root
local at0=Instance.new("Attachment",root);at0.CFrame=CFrame.new(1,2,3)
local at1=Instance.new("Attachment",other);at1.CFrame=CFrame.new(-2,-3,-4)
'''


class ModelPhysicsScalingTests(unittest.TestCase):
    run_script=native.CFrameTransformTests.run_script

    def test_vector_constructor_preserves_ieee_components(self):
        self.run_script('''
local v=Vector3.new(math.huge,-math.huge,0/0)
assert(v.X==math.huge and v.Y==-math.huge and v.Z~=v.Z)
local m=Instance.new("Model",workspace)
local root=Instance.new("Part",m);m.PrimaryPart=root
local force=Instance.new("BodyForce",root);force.Force=v
local before=root.Size
assert(not pcall(function()m:ScaleTo(2)end))
assert(m:GetScale()==1 and root.Size==before)
''')

    def test_native_studio_reference_for_all_physical_properties(self):
        self.assertEqual(REFERENCE['version'],'0.745.0.7451137')
        for case in REFERENCE['cases']:
            if case['class']=='Part':continue # Assembly semantics have separate checks below.
            with self.subTest(reference_class=case['class']):
                self.assertFalse(case['errors'])
                source=SETUP+'local obj=Instance.new('+json.dumps(case['class'])+',root)\n'
                source+='pcall(function()obj.Attachment0=at0;obj.Attachment1=at1 end)\n'
                source+='pcall(function()obj.Part0=root;obj.Part1=other end)\n'
                if case['class']=='Humanoid':source+='obj.Parent=m;obj.AutomaticScalingEnabled=false\n'
                properties=case['properties'] or {}
                for name,values in properties.items():
                    source+='obj['+json.dumps(name)+']='+value_literal(values['type'],values['before'])+'\n'
                for scale in (2,.5,1):
                    source+='m:ScaleTo('+str(scale)+')\n'
                    for name,values in properties.items():
                        expected=value_literal(values['type'],values['at_'+str(scale if scale!=.5 else 0.5)])
                        check={'number':'near','Vector3':'vec','CFrame':'frame'}[values['type']]
                        source+=check+'(obj['+json.dumps(name)+'],'+expected+')\n'
                self.run_script(source)

    def test_planar_velocity_force_limits_and_infinite_force_components(self):
        self.run_script(SETUP+'''
local v=Instance.new("LinearVelocity",root)
v.VelocityConstraintMode=Enum.VelocityConstraintMode.Plane
v.PlaneVelocity=Vector2.new(4,6);v.MaxPlanarAxesForce=Vector2.new(4,6)
local f=Instance.new("BodyForce",root);f.Force=Vector3.new(math.huge,4,6)
local spring=Instance.new("SpringConstraint",root);spring.MaxForce=math.huge
m:ScaleTo(2)
assert(v.PlaneVelocity==Vector2.new(8,12));assert(v.MaxPlanarAxesForce==Vector2.new(32,48))
assert(f.Force.X==math.huge and f.Force.Y==32 and f.Force.Z==48 and spring.MaxForce==math.huge)
m:ScaleTo(1);assert(v.PlaneVelocity==Vector2.new(4,6) and v.MaxPlanarAxesForce==Vector2.new(4,6))
assert(f.Force==Vector3.new(math.huge,4,6))
''')

    def test_force_edits_rebase_and_callbacks_observe_the_committed_geometry(self):
        self.run_script(SETUP+'''
local f=Instance.new("BodyForce",root);f.Force=Vector3.new(4,6,8)
local spring=Instance.new("SpringConstraint",root);spring.Stiffness=4;spring.Damping=4
m:ScaleTo(2);f.Force=Vector3.new(16,24,32)
m:ScaleTo(4);vec(f.Force,Vector3.new(128,192,256))
m:ScaleTo(2);vec(f.Force,Vector3.new(16,24,32))
local events=0
f:GetPropertyChangedSignal("Force"):Connect(function()
 events+=1;assert(m:GetScale()==1);vec(root.Size,Vector3.new(2,3,4));near(spring.Stiffness,4);near(spring.Damping,4)
end)
m:ScaleTo(1);task.wait();assert(events==1);vec(f.Force,Vector3.new(2,3,4))
''')

    def test_force_overflow_does_not_commit_geometry_or_change_scale(self):
        self.run_script(SETUP+'''
local f=Instance.new("SpringConstraint",root);f.Stiffness=1e300
local before=root.CFrame;local size=root.Size
assert(not pcall(function()m:ScaleTo(1e10)end))
assert(m:GetScale()==1 and root.CFrame==before and root.Size==size)
f.Stiffness=4;m:ScaleTo(2);near(f.Stiffness,16)
''')

    def test_legacy_body_mover_aliases_share_scaled_values_and_change_signals(self):
        self.run_script(SETUP+'''
local gyro=Instance.new("BodyGyro",root);gyro.maxTorque=Vector3.new(4,6,8)
local angular=Instance.new("BodyAngularVelocity",root);angular.maxTorque=Vector3.new(4,6,8);angular.angularvelocity=Vector3.new(1,2,3)
local thrust=Instance.new("BodyThrust",root);thrust.force=Vector3.new(4,6,8);thrust.location=Vector3.new(1,2,3)
local events=0;gyro:GetPropertyChangedSignal("maxTorque"):Connect(function()events+=1 end)
m:ScaleTo(2);task.wait()
assert(gyro.maxTorque==gyro.MaxTorque and events==1);vec(gyro.maxTorque,Vector3.new(32,48,64))
assert(angular.maxTorque==angular.MaxTorque);vec(angular.angularvelocity,Vector3.new(1,2,3))
assert(thrust.force==thrust.Force and thrust.location==thrust.Location)
vec(thrust.location,Vector3.new(2,4,6));vec(thrust.force,Vector3.new(32,48,64))
''')

    def test_part_linear_velocity_scales_and_angular_velocity_does_not(self):
        self.run_script(SETUP+'''
root.AssemblyLinearVelocity=Vector3.new(4,6,8);root.Velocity=Vector3.new(4,6,8)
root.AssemblyAngularVelocity=Vector3.new(1,2,3);root.RotVelocity=Vector3.new(1,2,3)
m:ScaleTo(2);vec(root.AssemblyLinearVelocity,Vector3.new(8,12,16));vec(root.Velocity,Vector3.new(8,12,16))
vec(root.AssemblyAngularVelocity,Vector3.new(1,2,3));vec(root.RotVelocity,Vector3.new(1,2,3))
m:ScaleTo(1);vec(root.AssemblyLinearVelocity,Vector3.new(4,6,8))
''')


if __name__=='__main__':unittest.main()
