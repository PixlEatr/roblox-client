import unittest

from physics_character_states import (classify_state, transition_events,
                                      explicit_seat_evidence, extract_environment,
                                      water_regions_from_voxel_materials,
                                      state_transition_records, state_signal_events)


class CharacterStateTests(unittest.TestCase):
    def classify(self, **kwargs):
        kwargs.setdefault('grounded', False)
        kwargs.setdefault('vertical_velocity', 0.)
        return classify_state(**kwargs)

    def test_supported_states_have_source_and_value(self):
        self.assertEqual(self.classify(jumping=True)['name'], 'Jumping')
        self.assertEqual(self.classify(swimming=True)['name'], 'Swimming')
        self.assertEqual(self.classify(climbing=True)['name'], 'Climbing')
        self.assertEqual(self.classify(seated=True)['name'], 'Seated')
        self.assertEqual(self.classify(dead=True)['name'], 'Dead')
        self.assertEqual(self.classify()['name'], 'Freefall')
        self.assertEqual(self.classify(grounded=True)['name'], 'Running')
        self.assertEqual(self.classify(swimming=True)['source'], 'local_physics_model')

    def test_precedence_and_landed_edge(self):
        self.assertEqual(self.classify(dead=True, seated=True)['name'], 'Dead')
        self.assertEqual(transition_events('Freefall', 'Running'), ('Landed', 'Running'))
        self.assertEqual(transition_events('Jumping', 'Running'), ('Landed', 'Running'))
        self.assertEqual(transition_events('Running', 'Running'), ())
        self.assertEqual(state_transition_records(None, 'Running'), [])
        self.assertEqual(state_transition_records('Freefall', 'Running'), [
            {'name': 'Landed', 'value': 7}, {'name': 'Running', 'value': 8}])
        self.assertEqual(state_signal_events('Jumping', 'Freefall'), [
            {'name': 'Jumping', 'active': False},
            {'name': 'FreeFalling', 'active': True}])
        self.assertEqual(state_signal_events('Freefall', 'Running'), [
            {'name': 'FreeFalling', 'active': False}])

    def test_seat_requires_sit_and_explicit_seatpart(self):
        seat = {'id': 'seat', 'class_name': 'Seat'}
        self.assertIsNone(explicit_seat_evidence({'Sit': True}, {'seat': seat}))
        self.assertIsNone(explicit_seat_evidence(
            {'Sit': False, 'SeatPart': {'$ref': 'seat'}}, {'seat': seat}))
        self.assertEqual(explicit_seat_evidence(
            {'Sit': True, 'SeatPart': {'$ref': 'seat'}}, {'seat': seat},
            humanoid_origins={'Sit': 'received', 'SeatPart': 'received'})['id'], 'seat')

    def test_rejects_ambiguous_inputs(self):
        with self.assertRaises(ValueError):
            classify_state(grounded=1, vertical_velocity=0.)
        with self.assertRaises(ValueError):
            classify_state(grounded=False, vertical_velocity=float('nan'))

    def test_environment_requires_received_geometry_and_does_not_guess_water(self):
        snapshot = {'local_player': 'player', 'nodes': [
            {'id': 'player', 'class_name': 'Player', 'properties': {'Character': {'$ref': 'char'}}},
            {'id': 'char', 'class_name': 'Model'},
            {'id': 'hum', 'class_name': 'Humanoid', 'parent': 'char',
             'properties': {'Sit': True, 'SeatPart': {'$ref': 'seat'}},
             'origins': {'Sit': 'received', 'SeatPart': 'received'}},
            {'id': 'seat', 'class_name': 'VehicleSeat',
             'properties': {'Position': {'$vector3': [2, 3, 4]}},
             'origins': {'Position': 'received'}},
            {'id': 'ladder', 'class_name': 'TrussPart',
             'properties': {'Size': {'$vector3': [2, 8, 1]}, 'Position': {'$vector3': [4, 5, 6]}},
             'origins': {'Size': 'received', 'Position': 'received'}},
            {'id': 'guess', 'class_name': 'TrussPart',
             'properties': {'Size': {'$vector3': [2, 8, 1]}, 'Position': {'$vector3': [4, 5, 6]}},
             'origins': {'Size': 'class_default', 'Position': 'class_default'}},
        ]}
        environment = extract_environment(snapshot)
        self.assertEqual([r['id'] for r in environment['ladder_regions']], ['ladder'])
        self.assertEqual(environment['ladder_regions'][0]['min'], [3., 1., 5.5])
        self.assertEqual(environment['seat']['id'], 'seat')
        self.assertEqual(environment['seat']['position'], [2., 3., 4.])
        self.assertEqual(environment['water_regions'], [])
        self.assertFalse(environment['water_supported'])

    def test_received_occupant_and_weld_links_are_seat_evidence(self):
        occupant = explicit_seat_evidence(
            {'Sit': False},
            {'seat': {'id': 'seat', 'class_name': 'Seat',
                      'properties': {'Occupant': {'$ref': 'hum'}},
                      'origins': {'Occupant': 'received'}}},
            humanoid_id='hum')
        self.assertEqual(occupant['id'], 'seat')
        welded = explicit_seat_evidence(
            {'Sit': False},
            {'seat': {'id': 'seat', 'class_name': 'Seat'},
             'weld': {'class_name': 'Weld', 'properties': {
                 'Part0': {'$ref': 'seat'}, 'Part1': {'$ref': 'torso'}},
                 'origins': {'Part0': 'received', 'Part1': 'received'}}},
            humanoid_id='hum', character_part_ids={'torso'})
        self.assertEqual(welded['id'], 'seat')

    def test_water_voxels_require_explicit_water_material_metadata(self):
        class Grid:
            origin = (10., 20., 30.)
            cell_size = (2., 3., 4.)
            dimensions = (2, 1, 2)
        regions = water_regions_from_voxel_materials(
            Grid(), ['Water', 'Rock', 'Water', 'Water'], 'Water', source_id='terrain')
        self.assertEqual(len(regions), 3)
        self.assertEqual(regions[1]['min'], [12., 20., 30.])
        self.assertEqual(regions[1]['max'], [14., 23., 34.])
        self.assertEqual(regions[2]['min'], [12., 20., 34.])
        self.assertTrue(all(region['source'] == 'received_geometry' for region in regions))
        with self.assertRaises(ValueError):
            water_regions_from_voxel_materials(Grid(), [True, False], True)

    def test_environment_imports_only_received_decoded_water_materials(self):
        base = {'nodes': [], 'decoded_terrain_materials': {
            'id': 'terrain', 'source': 'received_geometry',
            'materials_origin': 'decoded_received', 'origin': [0, 0, 0],
            'cell_size': [4, 4, 4], 'dimensions': [1, 1, 2],
            'materials': ['Water', 'Rock'], 'water_material_id': 'Water'}}
        result = extract_environment(base)
        self.assertEqual(len(result['water_regions']), 1)
        self.assertEqual(result['water_regions'][0]['max'], [4., 4., 4.])
        self.assertTrue(result['water_supported'])
        base['decoded_terrain_materials']['materials_origin'] = 'local'
        self.assertEqual(extract_environment(base)['water_regions'], [])


if __name__ == '__main__':
    unittest.main()
