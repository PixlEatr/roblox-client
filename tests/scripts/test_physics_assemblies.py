import unittest

from test_world_snapshot import fixture
from physics_assemblies import relative_cframe, resolve_own_assembly


def add_joint_support(world):
    props = world.schema['properties']
    part0, part1 = len(props), len(props) + 1
    props.extend([{'name': 'Part0', 'type': 28}, {'name': 'Part1', 'type': 28}])
    ci = len(world.schema['classes'])
    world.schema['classes'].append({'name': 'Motor6D', 'properties': [part0, part1]})
    return ci


class PhysicsAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.world, self.root_key = fixture()
        self.joint_class = add_joint_support(self.world)
        self.character_key = (1, 3)
        self.other_part = (1, 20)
        self.joint_key = (1, 21)
        self.world.instances[self.other_part] = {
            'class': 3, 'parent': self.character_key,
            'properties': {'Name': b'UpperTorso'}}
        self.world.instances[self.joint_key] = {
            'class': self.joint_class, 'parent': self.character_key,
            'properties': {'Part0': self.root_key, 'Part1': self.other_part, 'Enabled': True}}

    def test_resolves_connected_received_joint_assembly(self):
        result = resolve_own_assembly(self.world, 123)
        self.assertEqual(result['root_key'], self.root_key)
        self.assertEqual(set(result['connected_part_keys']), {self.root_key, self.other_part})
        self.assertEqual(result['evidence']['network_ownership'], 'unknown')
        self.assertTrue(result['evidence']['assembly_complete'])

    def test_does_not_connect_parts_by_character_parentage(self):
        self.world.instances[(1, 22)] = {
            'class': 3, 'parent': self.character_key,
            'properties': {'Name': b'Disconnected'}}
        result = resolve_own_assembly(self.world, 123)
        self.assertNotIn((1, 22), result['connected_part_keys'])

    def test_missing_joint_endpoint_preserves_uncertainty(self):
        del self.world.instances[self.joint_key]['properties']['Part1']
        result = resolve_own_assembly(self.world, 123)
        self.assertEqual(result['connected_part_keys'], [self.root_key])
        self.assertEqual(result['evidence']['incomplete_or_unresolved_joints'], 1)
        self.assertFalse(result['evidence']['assembly_complete'])
        self.assertIn('Part1_not_received', result['unresolved_joints'][0]['reasons'])

    def test_unresolved_forward_or_streamed_reference_is_not_bridged(self):
        self.world.instances[self.joint_key]['properties']['Part1'] = (9, 99)
        result = resolve_own_assembly(self.world, 123)
        self.assertEqual(result['connected_part_keys'], [self.root_key])
        self.assertTrue(result['evidence']['streaming_or_missing_data_possible'])
        self.assertIn('Part1_unresolved_or_streamed_out', result['unresolved_joints'][0]['reasons'])

    def test_missing_character_does_not_turn_world_roots_into_descendants(self):
        result = resolve_own_assembly(self.world, 999)
        self.assertIsNone(result['root_key'])
        self.assertEqual(result['connected_part_keys'], [])
        self.assertEqual(result['evidence']['network_ownership'], 'unknown')

    def test_fallback_does_not_select_a_nested_same_named_part(self):
        nested_model = (1, 23)
        self.world.instances[nested_model] = {
            'class': 2, 'parent': self.character_key, 'properties': {'Name': b'Nested'}}
        self.world.instances[self.root_key]['parent'] = nested_model
        self.world.instances[self.character_key]['properties']['PrimaryPart'] = None
        result = resolve_own_assembly(self.world, 123)
        self.assertIsNone(result['root_key'])
        self.assertIsNone(result['evidence']['root_source'])

    def test_disabled_and_explicitly_inactive_joints_do_not_connect_parts(self):
        for field in ('Enabled', 'Active'):
            with self.subTest(field=field):
                props=self.world.instances[self.joint_key]['properties']
                props['Enabled']=True;props.pop('Active',None);props[field]=False
                result=resolve_own_assembly(self.world,123)
                self.assertEqual(result['connected_part_keys'],[self.root_key])
                self.assertEqual(result['evidence']['inactive_joint_count'],1)
                self.assertTrue(result['evidence']['structural_connectivity_complete'])

    def test_nonrigid_jointinstance_is_not_an_assembly_edge(self):
        self.world.schema['classes'][self.joint_class]['name']='Glue'
        result=resolve_own_assembly(self.world,123)
        self.assertEqual(result['connected_part_keys'],[self.root_key])
        self.assertEqual(result['evidence']['recognized_joint_count'],0)

    def test_missing_enabled_state_is_not_assumed_true(self):
        del self.world.instances[self.joint_key]['properties']['Enabled']
        result=resolve_own_assembly(self.world,123)
        self.assertEqual(result['connected_part_keys'],[self.root_key])
        self.assertFalse(result['evidence']['structural_connectivity_complete'])
        self.assertIn('Enabled_not_received',result['unresolved_joints'][0]['reasons'])

    def test_malformed_endpoint_is_reported_instead_of_used_as_a_set_key(self):
        for invalid in ([1,20],{'scope':1},('bad',20),True):
            with self.subTest(invalid=invalid):
                self.world.instances[self.joint_key]['properties']['Part1']=invalid
                result=resolve_own_assembly(self.world,123)
                self.assertIn('Part1_invalid_reference',result['unresolved_joints'][0]['reasons'])
                self.assertEqual(result['connected_part_keys'],[self.root_key])

    def test_external_joint_chain_is_followed_beyond_character_descendants(self):
        workspace=(1,6)
        self.world.instances[self.other_part]['parent']=workspace
        external=(1,30)
        self.world.instances[external]={'class':3,'parent':workspace,'properties':{}}
        self.world.instances[(1,31)]={'class':self.joint_class,'parent':workspace,
            'properties':{'Part0':self.other_part,'Part1':external,'Enabled':True}}
        result=resolve_own_assembly(self.world,123)
        self.assertEqual(set(result['connected_part_keys']),{self.root_key,self.other_part,external})
        self.assertEqual(result['evidence']['recognized_joint_count'],2)
        self.assertEqual(result['evidence']['simulation_activity'],'unknown')

    def test_unresolved_joint_on_external_component_keeps_uncertainty(self):
        self.world.instances[self.other_part]['parent']=(1,6)
        self.world.instances[(1,31)]={'class':self.joint_class,'parent':(1,6),
            'properties':{'Part0':self.other_part,'Part1':(9,999),'Enabled':True}}
        result=resolve_own_assembly(self.world,123)
        self.assertFalse(result['evidence']['structural_connectivity_complete'])
        self.assertEqual(result['evidence']['incomplete_or_unresolved_joints'],1)

    def test_relative_cframe_matches_native_object_space_composition(self):
        reference = {
            'position': [10., 20., 30.],
            'rotation': [0., 0., 1., 0., 1., 0., -1., 0., 0.],
        }
        target = {
            'position': [2., 3., 4.],
            'rotation': [1., 0., 0., 0., 1., 0., 0., 0., 1.],
        }
        self.assertEqual(relative_cframe(reference, target), {
            'position': [26., -17., -8.],
            'rotation': [0., 0., -1., 0., 1., 0., 1., 0., 0.],
        })

    def test_relative_cframe_composes_both_rotations(self):
        reference = {
            'position': [4., -2., 7.],
            'rotation': [0., -1., 0., 1., 0., 0., 0., 0., 1.],
        }
        result = relative_cframe(reference, reference)
        self.assertEqual(result['position'], [0., 0., 0.])
        self.assertEqual(result['rotation'], [1., 0., 0., 0., 1., 0., 0., 0., 1.])

    def test_relative_cframe_rejects_unknown_or_invalid_pose_data(self):
        valid = {'position': [0., 0., 0.],
                 'rotation': [1., 0., 0., 0., 1., 0., 0., 0., 1.]}
        invalid = (
            None,
            {},
            {'position': [0., 0., 0.], 'rotation_id': 17},
            {'position': [0., 0.], 'rotation': valid['rotation']},
            {'position': [0., 0., 0.], 'rotation': [1., 0., 0.]},
            {'position': [float('nan'), 0., 0.], 'rotation': valid['rotation']},
            {'position': [0., 0., 0.],
             'rotation': [1., 0., 0., 0., 1., 0., 0., 0., float('inf')]},
            {'position': [True, 0., 0.], 'rotation': valid['rotation']},
        )
        for frame in invalid:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                relative_cframe(frame, valid)
        with self.assertRaises(ValueError):
            relative_cframe(valid, {'position': [0., 0., 0.]})

    def test_null_endpoint_is_known_inactive_and_scan_does_not_mutate_world(self):
        import copy
        self.world.instances[self.joint_key]['properties']['Part1']=None
        before=copy.deepcopy(self.world.instances)
        result=resolve_own_assembly(self.world,123)
        self.assertEqual(result['connected_part_keys'],[self.root_key])
        self.assertEqual(result['evidence']['inactive_joint_count'],1)
        self.assertEqual(result['evidence']['incomplete_or_unresolved_joints'],0)
        self.assertEqual(self.world.instances,before)


if __name__ == '__main__':
    unittest.main()
