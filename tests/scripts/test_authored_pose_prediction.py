"""Authored part transforms reach prediction exactly once per native write."""
import copy
import math
import unittest
from test_body_velocity import world,bridge
from test_direct_velocity import velocity,native_world
from test_authored_forces import write
from test_authored_anchoring import pose
from test_part_transforms import pose as frame


class AuthoredPosePredictionTests(unittest.TestCase):
    def test_teleport_preserves_motion_and_updates_position_and_heading(self):
        data=world();before=copy.deepcopy(data);b=bridge(data)
        b.consume([velocity(1,(5,2,0))]);b.step(.1)
        b.apply_mutations([write('root','CFrame',frame((10,30,40),(0,0,1,0,1,0,-1,0,0)))])
        c=b.controller;self.assertEqual(pose(c),(10,30,40));self.assertAlmostEqual(c.yaw,math.pi/2)
        self.assertEqual(c.yaw_origin,'authored_cframe');self.assertEqual(c.vertical_velocity,2)
        b.step(.1);self.assertEqual(pose(c),(10.5,30.2,40))
        self.assertEqual(data,before)

    def test_receipt_refresh_does_not_repeat_teleport_but_same_value_write_does(self):
        data=world();b=bridge(data);edit=write('root','CFrame',frame((10,20,0)))
        b.apply_mutations([edit]);b.consume([velocity(1,(10,0,0))]);b.step(.1)
        self.assertEqual(pose(b.controller),(11,20,0))
        b.update(copy.deepcopy(data));self.assertEqual(pose(b.controller),(11,20,0))
        b.apply_mutations([write('root','Size',{'$vector3':[2,2,2]})])
        self.assertEqual(pose(b.controller),(11,20,0))
        b.apply_mutations([edit]);self.assertEqual(pose(b.controller),(10,20,0))

    def test_position_and_cframe_aliases_follow_last_write_order(self):
        b=bridge(world())
        b.apply_mutations([write('root','CFrame',frame((10,20,30))),
                           write('root','Position',{'$vector3':[40,50,60]})])
        self.assertEqual(pose(b.controller),(40,50,60))
        b.apply_mutations([write('root','CFrame',frame((1,2,3)))])
        self.assertEqual(pose(b.controller),(1,2,3))
        b.apply_mutations([write('root','Position',{'$vector3':[7,8,9]})])
        self.assertEqual(pose(b.controller),(7,8,9))

    def test_position_only_write_preserves_predicted_heading(self):
        data,root=native_world();b=bridge(data);b.controller.yaw=.7
        b.apply_mutations([write(root['id'],'Position',{'$vector3':[5,6,7]})])
        self.assertEqual(pose(b.controller),(5,6,7));self.assertEqual(b.controller.yaw,.7)

    def test_teleport_while_anchored_survives_unanchor(self):
        for initially in (False,True):
            data=world();data['nodes'][-1]['properties']['Anchored']=initially;b=bridge(data)
            b.apply_mutations([write('root','Anchored',True),write('root','CFrame',frame((50,60,70)))])
            self.assertIsNone(b.step(.1)['predicted_state'])
            b.apply_mutations([write('root','Anchored',False)])
            self.assertEqual(pose(b.controller),(50,60,70))
            self.assertFalse(b.controller.grounded)

    def test_teleport_forgets_old_contact_and_platform_carry(self):
        b=bridge(world(),gravity=100);c=b.controller
        c.grounded=True;c._ground_source='old-platform';c._platform_carry_pending=True
        c._seat_carry_pending=True;c._slope_velocity=(1,0,0)
        b.apply_mutations([write('root','Position',{'$vector3':[20,40,60]})])
        self.assertFalse(c.grounded);self.assertIsNone(c._ground_source)
        self.assertFalse(c._platform_carry_pending);self.assertFalse(c._seat_carry_pending)
        self.assertEqual(c._slope_velocity,(0,0,0))
        b.step(.1);self.assertLess(c.vertical_position,40)

    def test_new_received_pose_invalidates_all_alias_overrides_without_replay(self):
        data,root=native_world();b=bridge(data)
        b.apply_mutations([write(root['id'],'CFrame',frame((10,20,30))),
                          write(root['id'],'Position',{'$vector3':[40,50,60]})])
        b.consume([velocity(1,(10,0,0),root['id'])]);b.step(.1);before=pose(b.controller)
        root['property_revisions']={'CFrame':10};b.update(data)
        effective=b._effective_snapshot();node=next(n for n in effective['nodes'] if n['id']==root['id'])
        self.assertNotIn('authored_pose_generation',node)
        self.assertEqual(pose(b.controller),before)
        b.apply_mutations([write(root['id'],'Position',{'$vector3':[1,2,3]})],observed_revision=9)
        self.assertEqual(pose(b.controller),before)
        b.apply_mutations([write(root['id'],'Position',{'$vector3':[1,2,3]})],observed_revision=10)
        self.assertEqual(pose(b.controller),(1,2,3))

    def test_invalid_pose_deactivates_prediction_and_valid_write_repairs_it(self):
        for value in (frame((float('nan'),0,0)),frame((1,2,3),(0,)*9),{'$cframe':{}},None):
            b=bridge(world());b.apply_mutations([write('root','CFrame',value)])
            self.assertIsNone(b.step(.1)['predicted_state'])
            b.apply_mutations([write('root','CFrame',frame((10,20,30)))])
            self.assertEqual(pose(b.controller),(10,20,30))

    def test_unrelated_parts_and_replacement_do_not_teleport_own_root(self):
        data=world();other=copy.deepcopy(data['nodes'][-1]);other['id']='other';other['name']='Other'
        data['nodes'].append(other);b=bridge(data);before=pose(b.controller)
        b.apply_mutations([write('other','CFrame',frame((100,100,100)))])
        self.assertEqual(pose(b.controller),before)
        b.apply_mutations([write('root','CFrame',frame((10,20,30)))])
        root=next(n for n in data['nodes'] if n['id']=='root');root['id']='replacement'
        b.update(data);self.assertEqual(pose(b.controller),before)

    def test_actual_luau_pivot_and_position_aliases_reach_controller(self):
        import test_cframe_transforms as transforms
        data,root=native_world()
        root['properties']['Size']={'$vector3':[2,2,1]};root['origins']['Size']='received'
        source='''
local char=game:GetService("Players").LocalPlayer.Character
local root=char.HumanoidRootPart
root.PivotOffset=CFrame.identity
char.PrimaryPart=root
char:PivotTo(CFrame.new(5,35,25))
root.CFrame=CFrame.new(10,40,30)*CFrame.Angles(0,math.pi/2,0)
root.Position=Vector3.new(20,50,40)
root:PivotTo(CFrame.new(30,60,50)*CFrame.Angles(0,-math.pi/2,0))
root:ApplyImpulse(Vector3.new(10,0,0))
'''
        for result in transforms.CFrameTransformTests.run_script(self,source,data):
            b=bridge(data);out=b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertTrue(out[0]['accepted'],out)
            self.assertEqual(pose(b.controller),(30,60,50));self.assertAlmostEqual(b.controller.yaw,-math.pi/2)
            b.step(.1);self.assertAlmostEqual(pose(b.controller)[0],30.5)

    def test_real_runtime_anchored_teleport_resumes_from_scripted_pose(self):
        import os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,_=ready_world();original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        prefix='local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart\n'
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                result=runtime.execute(prefix+'root.Anchored=true;root.CFrame=CFrame.new(50,60,70)')
                self.assertTrue(result['ok'],result)
                b=runtime.movement_preview.bridge
                self.assertIsNone(b.step(.1)['predicted_state'])
                self.assertTrue(runtime.execute(prefix+'root.Anchored=false')['ok'])
                self.assertEqual(pose(b.controller),(50,60,70))
                b.step(.1);self.assertLess(b.controller.vertical_position,60)
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
