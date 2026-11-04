"""Modern velocity constraints through native mutations and local physics."""
import copy
import math
import unittest

from physics_linear_velocity import validated_constraint, constraint_acceleration
from physics_forces import collect_received_force_sources
from test_body_velocity import world, bridge
from test_authored_forces import write
from test_physics_force_frames import frame, IDENTITY, Y90


def constraint(axes=((1,0,0),(0,1,0),(0,0,1)),targets=(10,0,0),**kwargs):
    return validated_constraint({'axes':axes,'targets':targets,'limit_mode':'Unlimited',**kwargs})


def world_with_constraint(mode=2,relative=2):
    data=world();root=data['nodes'][-1]
    root['properties']['CFrame']={'$cframe':{'position':[0,20,0],'rotation':IDENTITY}}
    root['origins']['CFrame']='received'
    props={'Enabled':True,'Attachment0':{'$ref':'attachment'},'RelativeTo':relative,
        'VelocityConstraintMode':mode,'ForceLimitsEnabled':False,'ForceLimitMode':0,
        'VectorVelocity':{'$vector3':[10,0,0]},'LineDirection':{'$vector3':[1,0,0]},
        'LineVelocity':10,'PrimaryTangentAxis':{'$vector3':[1,0,0]},
        'SecondaryTangentAxis':{'$vector3':[0,0,1]},'PlaneVelocity':{'$vector2':[10,20]},
        'MaxForce':10,'MaxAxesForce':{'$vector3':[2,0,4]},'MaxPlanarAxesForce':{'$vector2':[2,4]},
        'ReactionForceEnabled':False}
    data['nodes'] += [
        {'id':'attachment','class_name':'Attachment','parent':'root',
         'properties':{'CFrame':frame(IDENTITY)},'origins':{'CFrame':'received'}},
        {'id':'constraint','class_name':'LinearVelocity','parent':'root','properties':props,
         'origins':dict.fromkeys(props,'received')}]
    return data


class LinearVelocityTests(unittest.TestCase):
    def test_unlimited_vector_reaches_target_velocity(self):
        a=constraint_acceleration((0,0,0),[constraint()],2,.1)
        self.assertAlmostEqual(a[0]*.1,10)

    def test_line_leaves_other_velocity_components_free(self):
        a=constraint_acceleration((0,8,3),[constraint(((1,0,0),),(10,))],2,.1)
        self.assertEqual(a,(100,0,0))

    def test_plane_leaves_normal_velocity_free(self):
        a=constraint_acceleration((0,8,3),[constraint(((1,0,0),(0,0,1)),(10,20))],2,.1)
        self.assertEqual(a,(100,0,170))

    def test_magnitude_limit_is_shared_across_axes(self):
        a=constraint_acceleration((0,0,0),[constraint(targets=(3,4,0),limit_mode='Magnitude',max_force=1)],2,.1)
        self.assertAlmostEqual(math.hypot(*a),.5)
        self.assertAlmostEqual(a[0],.3);self.assertAlmostEqual(a[1],.4)

    def test_axis_caps_and_repeated_iterations_do_not_multiply_force(self):
        s=constraint(targets=(1000,1000,1000),limit_mode='PerAxis',limits=(2,0,4))
        a=constraint_acceleration((0,0,0),[s,s],2,.1)
        for actual,expected in zip(a,(2,0,4)):self.assertAlmostEqual(actual,expected)

    def test_reference_velocity_adds_to_relative_goal(self):
        s=constraint(reference_velocity=(5,6,7))
        a=constraint_acceleration((0,0,0),[s],2,.1)
        self.assertEqual(a,(150,60,70))

    def test_invalid_axes_are_rejected(self):
        for axes in [((0,0,0),),((1,0,0),(1,0,0))]:
            with self.assertRaises(ValueError):constraint(axes,(10,)*len(axes))

    def test_all_modes_reach_controller_and_line_preserves_gravity(self):
        for mode,expected in [(2,(10,0,0)),(0,(10,-10,0)),(1,(10,-10,20))]:
            b=bridge(world_with_constraint(mode),gravity=100);b.controller.step(.1)
            for actual,want in zip(b.controller.linear_velocity,expected):self.assertAlmostEqual(actual,want)

    def test_force_limited_controller_cannot_cancel_unbounded_gravity(self):
        data=world_with_constraint();data['nodes'][-1]['properties']['ForceLimitsEnabled']=True
        b=bridge(data,gravity=100);b.controller.step(.1)
        self.assertLess(b.controller.linear_velocity[0],1)
        self.assertLess(b.controller.vertical_velocity,-9)

    def test_attachment_frame_axes_turn_with_predicted_root(self):
        data=world_with_constraint(relative=0);b=bridge(data)
        b.controller.yaw=math.pi/2;b.controller.step(.1)
        self.assertAlmostEqual(b.controller.linear_velocity[0],0,places=6)
        self.assertAlmostEqual(b.controller.linear_velocity[2],-10,places=6)

    def test_plane_relative_axes_use_attachment_axes(self):
        data=world_with_constraint(mode=1,relative=0)
        data['nodes'][-2]['properties']['CFrame']=frame(Y90)
        records=collect_received_force_sources(data,'root')
        self.assertEqual(records[0]['axes'],((0,0,-1),(0,1,0)))

    def test_axis_force_limits_rotate_with_attachment_frame(self):
        data=world_with_constraint(relative=0)
        data['nodes'][-2]['properties']['CFrame']=frame(Y90)
        data['nodes'][-1]['properties'].update(ForceLimitsEnabled=True,ForceLimitMode=1)
        b=bridge(data);b.controller.step(.1)
        self.assertAlmostEqual(b.controller.linear_velocity[0],0)
        self.assertAlmostEqual(b.controller.linear_velocity[2],-.1)

    def test_unlimited_constraint_does_not_require_unused_limit_fields(self):
        data=world_with_constraint()
        for key in ('ForceLimitMode','MaxForce','MaxAxesForce'):
            data['nodes'][-1]['properties'].pop(key);data['nodes'][-1]['origins'].pop(key)
        self.assertEqual(len(collect_received_force_sources(data,'root')),1)

    def test_plane_force_limits_preserve_the_free_axis(self):
        data=world_with_constraint(mode=1)
        data['nodes'][-1]['properties'].update(ForceLimitsEnabled=True,ForceLimitMode=1)
        b=bridge(data,gravity=100);b.controller.step(.1)
        for actual,expected in zip(b.controller.linear_velocity,(.1,-10,.2)):
            self.assertAlmostEqual(actual,expected)

    def test_reference_frame_velocity_and_reaction_support_are_explicit(self):
        data=world_with_constraint(relative=1);p=data['nodes'][-1]['properties'];p['Attachment1']={'$ref':'reference-attachment'}
        data['nodes'][-1]['origins']['Attachment1']='received'
        props={'CFrame':frame(IDENTITY),'Anchored':False,'AssemblyLinearVelocity':{'$vector3':[5,0,0]},
               'AssemblyAngularVelocity':{'$vector3':[0,0,0]}}
        data['nodes'] += [{'id':'reference','class_name':'Part','parent':'workspace','properties':props,
                          'origins':dict.fromkeys(props,'received')},
                         {'id':'reference-attachment','class_name':'Attachment','parent':'reference',
                          'properties':{'CFrame':frame(IDENTITY)},'origins':{'CFrame':'received'}}]
        b=bridge(data);b.controller.step(.1);self.assertAlmostEqual(b.controller.linear_velocity[0],15)
        p['ReactionForceEnabled']=True
        self.assertEqual(collect_received_force_sources(data,'root'),[])
        props['Anchored']=True
        self.assertEqual(collect_received_force_sources(data,'root')[0]['reference_velocity'],(0,0,0))
        props['Anchored']=False;p['ReactionForceEnabled']=False
        props['AssemblyAngularVelocity']={'$vector3':[0,0,2]}
        props['AssemblyCenterOfMass']={'$vector3':[0,0,0]}
        data['nodes'][-2]['origins']['AssemblyCenterOfMass']='received'
        data['nodes'][-1]['properties']['WorldCFrame']={'$cframe':{'position':[1,0,0],'rotation':IDENTITY}}
        data['nodes'][-1]['origins']['WorldCFrame']='received'
        self.assertEqual(collect_received_force_sources(data,'root')[0]['reference_velocity'],(5,2,0))

    def test_disable_reparent_destroy_and_invalid_write_clear_constraint(self):
        b=bridge(world_with_constraint())
        b.apply_mutations([write('constraint','Enabled',False)])
        self.assertEqual(b.controller._linear_velocity_sources,())
        b.apply_mutations([write('constraint','Enabled',True),write('constraint','Parent',None)])
        self.assertEqual(b.controller._linear_velocity_sources,())
        b.apply_mutations([write('constraint','Parent',{'$ref':'root'})])
        self.assertEqual(len(b.controller._linear_velocity_sources),1)
        b.apply_mutations([write('constraint','VectorVelocity',{'$vector3':[math.nan,0,0]})])
        self.assertEqual(b.controller._linear_velocity_sources,())


class NativeLinearVelocityTests(unittest.TestCase):
    def test_native_enum_and_vector2_fields(self):
        import test_cframe_transforms as transforms
        transforms.CFrameTransformTests.run_script(self,'''
local mover=Instance.new("LinearVelocity")
assert(mover.RelativeTo==Enum.ActuatorRelativeTo.World)
assert(mover.VelocityConstraintMode==Enum.VelocityConstraintMode.Vector)
mover.VelocityConstraintMode=Enum.VelocityConstraintMode.Plane
mover.ForceLimitMode=Enum.ForceLimitMode.PerAxis
mover.PlaneVelocity=Vector2.new(3,4);mover.MaxPlanarAxesForce=Vector2.new(5,6)
assert(mover.PlaneVelocity==Vector2.new(3,4) and mover.MaxPlanarAxesForce==Vector2.new(5,6))
assert(mover.VelocityConstraintMode==Enum.VelocityConstraintMode.Plane)
assert(mover.ForceLimitMode==Enum.ForceLimitMode.PerAxis)
assert(not pcall(function()mover.VelocityConstraintMode=Enum.PartType.Block end))
assert(not pcall(function()mover.ForceLimitMode=0 end))
assert(not pcall(function()mover.PlaneVelocity=Vector3.zero end))
''')

    def test_persistent_luau_create_switch_modes_disable_destroy(self):
        import test_cframe_transforms as transforms
        import test_joint_poses as poses
        data=copy.deepcopy(transforms.SNAPSHOT)
        root=next(n for n in data['nodes'] if n.get('name')=='HumanoidRootPart')
        root['properties'].update(AssemblyMass=2,Anchored=False)
        root.setdefault('origins',{}).update(AssemblyMass='received',Anchored='received',CFrame='received')
        host=poses.JointPoseTests.host(self,data)
        def execute(code):return poses.JointPoseTests.execute(self,host,code)
        result=execute('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
local attachment=Instance.new("Attachment",root)
getgenv().mover=Instance.new("LinearVelocity",root)
mover.Attachment0=attachment;mover.ForceLimitsEnabled=false;mover.VectorVelocity=Vector3.new(10,0,0)
''')
        b=bridge(data);b.apply_mutations(result['mutations']);b.controller.step(.1)
        self.assertAlmostEqual(b.controller.linear_velocity[0],10)
        result=execute('''mover.VelocityConstraintMode=Enum.VelocityConstraintMode.Plane
mover.PrimaryTangentAxis=Vector3.xAxis;mover.SecondaryTangentAxis=Vector3.zAxis
mover.PlaneVelocity=Vector2.new(-5,8)''')
        b.apply_mutations(result['mutations']);b.controller.step(.1)
        self.assertAlmostEqual(b.controller.linear_velocity[0],-5)
        self.assertAlmostEqual(b.controller.linear_velocity[2],8)
        for code in ['mover.Enabled=false','mover:Destroy()']:
            result=execute(code);b.apply_mutations(result['mutations'])
            self.assertEqual(b.controller._linear_velocity_sources,())


if __name__=='__main__':unittest.main()
