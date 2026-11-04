import unittest

from character_animations import own_character_animations
from test_world_snapshot import fixture


class CharacterAnimationTests(unittest.TestCase):
    def setUp(self):
        self.world, _ = fixture()
        self.ids = {}
        for name in ('Animator', 'LocalScript', 'Folder', 'Animation', 'NumberValue', 'HumanoidDescription'):
            self.ids[name] = len(self.world.schema['classes'])
            self.world.schema['classes'].append({'name': name, 'properties': []})
        self.add((1, 10), 'Animator', (1, 5))
        self.add((1, 11), 'HumanoidDescription', (1, 5), WalkAnimation=999, IdleAnimation=888)

    def add(self, key, cls, parent, **props):
        self.world.instances[key] = {'class': self.ids[cls], 'parent': parent, 'properties': props}

    def test_received_animate_asset_wins_and_description_fills_missing_roles(self):
        self.add((1, 20), 'LocalScript', (1, 3), Name=b'Animate')
        self.add((1, 21), 'Folder', (1, 20), Name=b'walk')
        self.add((1, 22), 'Animation', (1, 21), AnimationContent=b'http://www.roblox.com/asset/?id=123')
        result = own_character_animations(self.world, 123)
        self.assertEqual(result['animator_key'], (1, 10))
        self.assertEqual(result['references'], {'walk': 'rbxassetid://123', 'idle': 'rbxassetid://888'})
        self.assertEqual(result['sources']['walk'], 'received_animate_configuration')

    def test_foreign_asset_and_untrusted_reference_never_replace_avatar_configuration(self):
        self.add((1, 20), 'LocalScript', (1, 6), Name=b'Animate')
        self.add((1, 21), 'Folder', (1, 20), Name=b'walk')
        self.add((1, 22), 'Animation', (1, 21), AnimationContent=b'rbxassetid://123')
        self.add((1, 30), 'LocalScript', (1, 3), Name=b'Animate')
        self.add((1, 31), 'Folder', (1, 30), Name=b'walk')
        self.add((1, 32), 'Animation', (1, 31), AnimationContent=b'https://example.com/asset?id=321')
        result = own_character_animations(self.world, 123)
        self.assertEqual(result['references']['walk'], 'rbxassetid://999')

    def test_ambiguous_animator_and_despawn_do_not_select_a_stale_target(self):
        self.add((1, 12), 'Animator', (1, 5))
        self.assertIsNone(own_character_animations(self.world, 123)['animator_key'])
        self.world.instances[(1, 2)]['properties']['Character'] = None
        self.assertEqual(own_character_animations(self.world, 123)['references'], {})

    def test_weighted_idle_and_cyclic_unrelated_hierarchy_are_bounded(self):
        self.add((1, 20), 'LocalScript', (1, 3), Name=b'Animate')
        self.add((1, 21), 'Folder', (1, 20), Name=b'idle')
        self.add((1, 22), 'Animation', (1, 21), AnimationContent=b'rbxassetid://123')
        self.add((1, 23), 'Animation', (1, 21), AnimationContent=b'rbxassetid://456')
        self.add((1, 24), 'NumberValue', (1, 22), Name=b'Weight', Value=10.)
        self.add((1, 25), 'NumberValue', (1, 23), Name=b'Weight', Value=1.)
        self.add((1, 40), 'Folder', (1, 41))
        self.add((1, 41), 'Folder', (1, 40))
        self.assertEqual(own_character_animations(self.world, 123)['references']['idle'], 'rbxassetid://123')

    def test_cached_catalog_updates_on_relevant_asset_create_and_property_change(self):
        first = own_character_animations(self.world, 123)
        self.assertEqual(first['references']['walk'], 'rbxassetid://999')
        self.add((1, 20), 'LocalScript', (1, 3), Name=b'Animate')
        self.world.touch((1, 20), created=True)
        self.add((1, 21), 'Folder', (1, 20), Name=b'jump')
        self.world.touch((1, 21), created=True)
        self.add((1, 22), 'Animation', (1, 21), AnimationContent=b'rbxassetid://123')
        self.world.touch((1, 22), created=True)
        self.assertEqual(own_character_animations(self.world, 123)['references']['jump'], 'rbxassetid://123')
        self.world.instances[(1, 22)]['properties']['AnimationContent'] = b'rbxassetid://456'
        self.world.touch((1, 22), ['AnimationContent'])
        self.assertEqual(own_character_animations(self.world, 123)['references']['jump'], 'rbxassetid://456')

    def test_unrelated_world_changes_reuse_owned_character_catalog(self):
        first = own_character_animations(self.world, 123)
        self.add((1, 80), 'Folder', None, Name=b'other')
        self.world.touch((1, 80), created=True)
        self.assertIs(own_character_animations(self.world, 123), first)

    def test_role_container_rename_and_asset_reparent_remove_invalidate_catalog(self):
        self.add((1, 20), 'LocalScript', (1, 3), Name=b'Animate')
        self.add((1, 21), 'Folder', (1, 20), Name=b'jump')
        self.add((1, 22), 'Animation', (1, 21), AnimationContent=b'rbxassetid://123')
        self.assertEqual(own_character_animations(self.world, 123)['references']['jump'], 'rbxassetid://123')
        self.world.instances[(1, 21)]['properties']['Name'] = b'fall'
        self.world.touch((1, 21), ['Name'])
        updated = own_character_animations(self.world, 123)['references']
        self.assertNotIn('jump', updated)
        self.assertEqual(updated['fall'], 'rbxassetid://123')
        self.world.instances[(1, 22)]['parent'] = (1, 3)
        self.world.touch((1, 22), ['Parent'])
        self.assertNotIn('fall', own_character_animations(self.world, 123)['references'])
        self.world.instances.pop((1, 22))
        self.world.touch((1, 22), deleted=True)
        self.assertNotIn('fall', own_character_animations(self.world, 123)['references'])


if __name__ == '__main__':
    unittest.main()
