import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

from physics_forces import (apply_impulse, collect_received_force_sources,
                            force_impulse, received_assembly_mass)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from world_snapshot import _node_snapshot


class PhysicsForceTests(unittest.TestCase):
    def test_mass_must_be_received_and_positive(self):
        self.assertEqual(received_assembly_mass({'properties': {'AssemblyMass': 4},
                                                 'origins': {'AssemblyMass': 'received'}}),
                         (4, 'received_assembly_mass'))
        self.assertEqual(received_assembly_mass({'properties': {'AssemblyMass': 4},
                                                 'origins': {'AssemblyMass': 'class_default'}}),
                         (None, None))
        self.assertEqual(received_assembly_mass({'properties': {'AssemblyMass': 0},
                                                 'origins': {'AssemblyMass': 'received'}}),
                         (None, None))

    def test_geometry_density_mass_requires_received_complete_membership(self):
        snapshot = {'initial_replication_complete': True, 'nodes': [
            {'id': 'root', 'class_name': 'Part', 'parent': 'char',
             'properties': {'AssemblyMass': 0, 'AssemblyRootPart': {'$ref': 'root'},
                            'Massless': False, 'Size': {'$vector3': [2, 3, 4]},
                            'Material': 256, 'CustomPhysicalProperties': None},
             'origins': {'AssemblyMass': 'class_default', 'AssemblyRootPart': 'received',
                         'Massless': 'received', 'Size': 'received',
                         'Material': 'received', 'CustomPhysicalProperties': 'received'}},
        ]}
        mass, source = received_assembly_mass(snapshot, 'root')
        self.assertAlmostEqual(mass, 16.8)
        self.assertEqual(source, 'received_geometry_density_approximate')
        snapshot['nodes'][0]['origins'].pop('Massless')
        self.assertEqual(received_assembly_mass(snapshot, 'root'), (None, None))

    def test_mass_derivation_uses_snapshot_schema_conversion(self):
        properties = [{'name': 'Character', 'type': 28}, {'name': 'Name', 'type': 1},
                      {'name': 'Size', 'type': 22}, {'name': 'Material', 'type': 7},
                      {'name': 'CustomPhysicalProperties', 'type': 41},
                      {'name': 'Massless', 'type': 1}, {'name': 'Part0', 'type': 28},
                      {'name': 'Part1', 'type': 28}]
        schema = {'properties': properties, 'classes': [
            {'name': 'Player', 'properties': [0, 1]},
            {'name': 'Model', 'properties': [1]},
            {'name': 'Part', 'properties': [1, 2, 3, 4, 5]},
            {'name': 'Motor6D', 'properties': [1, 6, 7]},
        ]}
        world = SimpleNamespace(schema=schema, creation={}, property_versions={}, top_containers=set(), shared={})
        records = [
            _node_snapshot(world, (1, 0), {'class': 0, 'parent': None,
                'properties': {'Name': 'Player', 'Character': (1, 3)}}),
            _node_snapshot(world, (1, 3), {'class': 1, 'parent': None,
                'properties': {'Name': 'Character'}}),
            _node_snapshot(world, (1, 1), {'class': 2, 'parent': (1, 3),
                'properties': {'Name': 'HumanoidRootPart', 'Size': [2, 3, 4],
                              'Material': 256, 'CustomPhysicalProperties': None,
                              'Massless': False}}),
            _node_snapshot(world, (1, 2), {'class': 2, 'parent': (1, 3),
                'properties': {'Name': 'Torso', 'Size': [1, 2, 3],
                              'Material': 256, 'CustomPhysicalProperties': None,
                              'Massless': False}}),
            _node_snapshot(world, (1, 4), {'class': 3, 'parent': (1, 3),
                'properties': {'Name': 'RootJoint', 'Part0': (1, 1), 'Part1': (1, 2)}}),
        ]
        snapshot = {'local_player': '1:0', 'initial_replication_complete': True,
                    'nodes': records}
        mass, source = received_assembly_mass(snapshot, '1:1')
        self.assertAlmostEqual(mass, 21.)
        self.assertEqual(source, 'received_geometry_density_approximate')

    def test_impulse_uses_mass_times_delta_velocity(self):
        self.assertEqual(apply_impulse((1, 2, 3), (8, 0, -4), 4), (3, 2, 2))

    def test_dead_and_anchored_targets_reject_impulse(self):
        for kwargs, expected in (({'anchored': True}, 'anchored'), ({'alive': False}, 'dead')):
            with self.assertRaisesRegex(ValueError, expected):
                apply_impulse((0, 0, 0), (1, 0, 0), 1, **kwargs)
        with self.assertRaisesRegex(ValueError, 'mass_unavailable'):
            apply_impulse((0, 0, 0), (1, 0, 0), None)

    def test_constant_force_intake_is_explicit_and_bounded(self):
        self.assertEqual(force_impulse((0, 10, 0), .25), (0, 2.5, 0))
        with self.assertRaises(ValueError):
            force_impulse((0, 10, 0), 11)

    def test_force_sources_require_received_values_and_owned_root_attachment(self):
        snapshot = {'nodes': [
            {'id': 'root', 'class_name': 'Part', 'parent': 'char'},
            {'id': 'bf', 'class_name': 'BodyForce', 'parent': 'root',
             'properties': {'Force': {'$vector3': [0, 12, 0]}},
             'origins': {'Force': 'received'}},
            {'id': 'att', 'class_name': 'Attachment', 'parent': 'root'},
            {'id': 'vf', 'class_name': 'VectorForce', 'parent': 'char',
             'properties': {'Force': {'$vector3': [4, 0, 0]}, 'Enabled': True,
                            'Attachment0': {'$ref': 'att'}, 'ApplyAtCenterOfMass': True,
                            'RelativeTo': 2},
             'origins': {'Force': 'received', 'Enabled': 'received',
                         'Attachment0': 'received', 'ApplyAtCenterOfMass': 'received',
                         'RelativeTo': 'received'}},
        ]}
        records = collect_received_force_sources(snapshot, 'root')
        self.assertEqual([record['id'] for record in records], ['bf', 'vf'])
        snapshot['nodes'][1]['origins']['Force'] = 'class_default'
        self.assertEqual([record['id'] for record in
                          collect_received_force_sources(snapshot, 'root')], ['vf'])


if __name__ == '__main__':
    unittest.main()
