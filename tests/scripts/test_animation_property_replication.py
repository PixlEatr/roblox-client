import unittest

from animation_property_replication import encode_animator_slot
from test_animation_replication import animation_world


class AnimationPropertyReplicationTests(unittest.TestCase):
    def setUp(self):
        self.world, self.keys = animation_world()
        self.world.schema['properties'] = []
        for slot in (0, 15):
            for base, kind in [('AnimationId',44), ('AnimTrackMetadata',66),
                               ('AnimTrackWeight',67), ('AnimTrackPlayState',65)]:
                index = len(self.world.schema['properties'])
                self.world.schema['properties'].append({'id':index,'class':4,
                    'name':base+str(slot),'type':kind})
                self.world.schema['classes'][4]['properties'].append(index)
        self.world.schema['parent_property_id'] = 65535

    def packet(self, **overrides):
        fields = dict(capability_bits=0x100000, slot=0, animation_id=11600249883,
            playing=True, playback_speed=1., looped=True, priority=1000,
            weight_target=1., weighted_fade_time=0.,
            ref_step_id_for_time_position=123., ref_step_id_for_weight=124.)
        fields.update(overrides)
        return encode_animator_slot(self.world,4242,self.keys['animator'],**fields)

    def test_received_slot_changes_roundtrip_without_altering_source_world(self):
        import copy
        from world_replication import World
        original = copy.deepcopy(self.world.instances)
        for slot in (0, 15):
            packet = self.packet(slot=slot)
            self.assertEqual(self.world.instances, original)
            receiver = World(self.world.schema)
            receiver.instances = copy.deepcopy(original)
            receiver.packet(packet)
            props = receiver.instances[self.keys['animator']]['properties']
            self.assertEqual(props[f'AnimationId{slot}'],11600249883)
            self.assertTrue(props[f'AnimTrackMetadata{slot}']['anim_track_metadata']['looped'])
            self.assertEqual(props[f'AnimTrackPlayState{slot}']['anim_track_play_state']['ref_step_id_for_time_position'],123.)
            self.assertEqual(receiver.items[3],4)

    def test_missing_schema_foreign_owner_and_invalid_slot_are_rejected(self):
        for invalid in (-1,16,True):
            with self.subTest(slot=invalid), self.assertRaises(ValueError):
                self.packet(slot=invalid)
        self.world.instances[self.keys['player']]['properties']['Character'] = None
        with self.assertRaises(ValueError):self.packet()


if __name__ == '__main__':unittest.main()
