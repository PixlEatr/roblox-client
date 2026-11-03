import copy
import unittest

from animation_native_updates import NativeAnimationUpdates
from test_animation_replication import animation_world
from world_replication import World


def command(sequence, kind, *, playing=True, **parameters):
    return {'sequence': sequence, 'kind': kind, 'animator_id': '9:6',
        'track_id': 'local-track-1', 'animation_reference': 'rbxassetid://123456',
        'parameters': {**parameters, 'effective_state': {'playing': playing,
            'priority': 1000, 'looped': True, 'time_position': .3, 'speed': 1.25,
            'weight_current': .7, 'weight_target': .9}}}


class NativeAnimationUpdateTests(unittest.TestCase):
    def setUp(self):
        self.world,self.keys=animation_world()
        self.producer=NativeAnimationUpdates()

    def prepare(self, entries):
        return self.producer.prepare(entries,world=self.world,user_id=4242,
                                     capability_bits=0x100000)

    def decode(self, message):
        received=World(self.world.schema)
        received.packet(message['payload'])
        return received.events[-1]['arguments']

    def test_normal_luau_setup_play_adjust_stop_has_native_masks_and_arguments(self):
        before=copy.deepcopy(self.world.instances)
        messages=self.prepare([
            command(1,'Looped',playing=False,looped=True),
            command(2,'Priority',playing=False,priority=1000),
            command(3,'Play',fade_time=.125,weight=.75,speed=1.5),
            command(4,'AdjustSpeed',speed=2.),
            command(5,'TimePosition',position=.25),
            command(6,'Stop',playing=False,fade_time=.125)])
        self.assertEqual([m['mask'] for m in messages],[64,16,0x51,4,8,1])
        self.assertTrue(self.decode(messages[0])[1])
        self.assertEqual(self.decode(messages[2]),[
            b'rbxassetid://123456',True,.125,.75,1.5,0.,1000,True,0x51])
        self.assertEqual(self.decode(messages[3])[4],2.)
        self.assertEqual(self.decode(messages[4])[5],.25)
        self.assertEqual(self.decode(messages[5])[1:3],[False,.125])
        self.assertEqual(self.world.instances,before)
        self.assertEqual(self.prepare([command(6,'Stop',playing=False,fade_time=.125)]),[])

    def test_failed_batch_is_atomic_and_received_animator_is_required(self):
        self.prepare([command(1,'Play',fade_time=0.,weight=1.,speed=1.)])
        bad=command(3,'AdjustSpeed',speed=2.)
        bad['animator_id']='9:999'
        with self.assertRaises(ValueError):
            self.prepare([command(2,'AdjustWeight',weight=.5,fade_time=.25),bad])
        messages=self.prepare([command(2,'AdjustWeight',weight=.5,fade_time=.25)])
        self.assertEqual([m['mask'] for m in messages],[2])

    def test_character_change_drops_previous_track_setup(self):
        self.prepare([command(1,'Looped',playing=False,looped=True),
                      command(2,'Play',fade_time=0.,weight=1.,speed=1.)])
        self.world.instances[self.keys['player']]['properties']['Character']=None
        with self.assertRaises(ValueError):
            self.prepare([command(3,'AdjustSpeed',speed=2.)])


if __name__=='__main__':unittest.main()
