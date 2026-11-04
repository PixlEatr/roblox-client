import math
import unittest

from animation_pose_sampler import AnimationPoseError, AnimationPoseSampler


def rz(angle):
    c,s=math.cos(angle),math.sin(angle)
    return [c,-s,0,s,c,0,0,0,1]


def pose(path, x, angle=0, **extra):
    return {'path':list(path),'position':[x,0,0],'rotation':rz(angle),
            'weight':1,'easing_style':None,'easing_direction':None,**extra}


def clip(frames, *, looped=False, length=1):
    return {'version':1,'kind':'KeyframeSequence','looped':looped,'length':length,
            'keyframes':[{'time':t,'poses':p} for t,p in frames]}


class AnimationPoseSamplerTests(unittest.TestCase):
    def test_sparse_joint_samples_lerp_translation_slerp_rotation_and_weight(self):
        decoded=clip([(0,[pose(('HumanoidRootPart',),0),pose(('Torso','Left Arm'),2)]),
                      (.5,[pose(('HumanoidRootPart',),4,math.pi/2,
                                weight=.5)]),
                      (1,[pose(('HumanoidRootPart',),8,math.pi)])])
        result=AnimationPoseSampler(decoded).sample(.25)
        self.assertEqual(result['status'],'ok')
        root=result['poses'][('HumanoidRootPart',)]
        self.assertAlmostEqual(root['position'][0],2)
        self.assertAlmostEqual(root['weight'],.75)
        self.assertAlmostEqual(root['rotation'][0],math.cos(math.pi/4),places=6)
        self.assertAlmostEqual(root['rotation'][1],-math.sin(math.pi/4),places=6)
        # A sparse limb channel holds its own latest authored pose.
        arm=result['poses'][('Torso','Left Arm')]
        self.assertEqual(arm['position'],[2,0,0])

    def test_loop_wraps_sparse_channels_and_nonlooped_clamps(self):
        decoded=clip([(.2,[pose(('Joint',),2)]),(.8,[pose(('Joint',),8)])],looped=True,length=1)
        sampler=AnimationPoseSampler(decoded)
        self.assertAlmostEqual(sampler.sample(1.1)['poses'][('Joint',)]['position'][0],3.5)
        self.assertAlmostEqual(sampler.sample(-.1)['poses'][('Joint',)]['position'][0],6.5)
        clamp=AnimationPoseSampler(clip([(.2,[pose(('Joint',),2)]),(.8,[pose(('Joint',),8)])],length=1))
        self.assertEqual(clamp.sample(-10)['poses'][('Joint',)]['position'][0],2)
        self.assertEqual(clamp.sample(10)['poses'][('Joint',)]['position'][0],8)

    def test_quaternion_shortest_arc_handles_equivalent_signs(self):
        # A full-turn-equivalent pair should remain identity instead of spinning.
        decoded=clip([(0,[pose(('Joint',),0)]),(1,[pose(('Joint',),0,2*math.pi)])])
        matrix=AnimationPoseSampler(decoded).sample(.5)['poses'][('Joint',)]['rotation']
        self.assertAlmostEqual(matrix[0],1,places=6)
        self.assertAlmostEqual(matrix[4],1,places=6)
        self.assertAlmostEqual(matrix[8],1,places=6)

    def test_bad_rotation_duplicate_paths_and_unknown_easing_fail_explicitly(self):
        bad=clip([(0,[pose(('Joint',),0)])])
        bad['keyframes'][0]['poses'][0]['rotation'][0]=2
        with self.assertRaisesRegex(AnimationPoseError,'invalid_pose_rotation'):
            AnimationPoseSampler(bad)
        duplicate=clip([(0,[pose(('Joint',),0),pose(('Joint',),1)])])
        with self.assertRaisesRegex(AnimationPoseError,'duplicate_pose_path'):
            AnimationPoseSampler(duplicate)
        easing=clip([(0,[pose(('Joint',),0,easing_style=7)])])
        with self.assertRaisesRegex(AnimationPoseError,'unsupported_easing_style'):
            AnimationPoseSampler(easing)

    def test_invalid_sample_returns_fixed_status_without_nan(self):
        result=AnimationPoseSampler(clip([(0,[pose(('Joint',),0)])])).sample(float('nan'))
        self.assertEqual(result,{'status':'invalid_sample_time','poses':{},'time':None})


if __name__ == '__main__':
    unittest.main()
