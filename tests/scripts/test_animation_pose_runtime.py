import unittest

from animation_pose_runtime import AnimationPoseRuntime


PATH = ('HumanoidRootPart','Arm')
IDENTITY = [1.,0.,0.,0.,1.,0.,0.,0.,1.]


def clip(start=0., end=4.):
    return {'version':1,'kind':'KeyframeSequence','length':1.,'looped':True,
            'keyframes':[{'time':t,'poses':[{'path':list(PATH),'position':[x,0.,0.],
                          'rotation':IDENTITY,'weight':1.}]} for t,x in ((0.,start),(1.,end))]}


def operation(sequence,kind='Play',track='track1',ref='rbxassetid://1',
              actor='1:10',playing=True,position=0.,speed=1.,priority=1000,
              weight=1.,target=1.,**parameters):
    if kind == 'AdjustSpeed':parameters={'speed':speed}
    elif kind == 'Priority':parameters={'priority':priority}
    return {'sequence':sequence,'kind':kind,'track_id':track,'animator_id':actor,
            'animation_reference':ref,'parameters':{
                **({'fade_time':0.,'weight':target,'speed':speed} if kind=='Play' else parameters),
                'effective_state':{'playing':playing,'looped':True,'priority':priority,
                    'time_position':position,'speed':speed,
                    'weight_current':weight,'weight_target':target}}}


class AnimationPoseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clips={'rbxassetid://1':clip()}
        self.runtime=AnimationPoseRuntime(self.clips.get)
        self.runtime.bind('1:10',generation='character1')

    def test_real_clip_clock_speed_change_and_stop_fade(self):
        self.runtime.consume([operation(1)],now=0.)
        self.runtime.sample(0.)
        self.assertAlmostEqual(self.runtime.sample(.25)['poses'][PATH]['position'][0],1.)
        self.runtime.consume([operation(2,'AdjustSpeed',position=.25,speed=2.)],now=.25)
        self.assertAlmostEqual(self.runtime.sample(.5)['poses'][PATH]['position'][0],3.)
        self.runtime.consume([operation(3,'Stop',playing=False,position=.75,target=0.,fade_time=.2)],now=.5)
        self.assertAlmostEqual(self.runtime.sample(.6)['poses'][PATH]['position'][0],1.5)
        self.assertEqual(self.runtime.sample(.71)['status'],'no_active_pose')

    def test_higher_priority_track_overrides_core_per_joint(self):
        self.clips['rbxassetid://2']=clip(10.,10.)
        self.runtime.consume([operation(1),operation(2,track='action',ref='rbxassetid://2',priority=2)],now=0.)
        self.assertEqual(self.runtime.sample(0.)['poses'][PATH]['position'],[10.,0.,0.])
        self.assertFalse(self.runtime.sample(.25)['server_animation_confirmed'])

    def test_missing_asset_waits_for_the_real_clip_and_new_actor_cannot_reuse_old_pose(self):
        self.clips.clear()
        self.runtime.consume([operation(1)],now=0.)
        self.assertEqual(self.runtime.sample(0.)['status'],'waiting_for_clips')
        self.clips['rbxassetid://1']=clip()
        self.assertEqual(self.runtime.sample(10.)['poses'][PATH]['position'],[0.,0.,0.])
        self.assertAlmostEqual(self.runtime.sample(10.25)['poses'][PATH]['position'][0],1.)
        self.runtime.bind('1:20',generation='character2')
        self.runtime.consume([operation(2)],now=11.)
        result=self.runtime.sample(11.)
        self.assertEqual(result['poses'],{})
        self.assertEqual(result['rejected_actor_operations'],1)

    def test_clock_cannot_run_backward(self):
        self.runtime.sample(2.)
        with self.assertRaises(ValueError):self.runtime.sample(1.)


if __name__ == '__main__':unittest.main()
