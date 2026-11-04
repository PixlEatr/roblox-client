"""Avoid environment rescans for mutation prefixes with no effective writes."""
import unittest
from unittest.mock import patch

from test_authored_forces import write
from test_body_velocity import bridge, world
from test_physics_controller import intent


class MutationEnvironmentRefreshTests(unittest.TestCase):
    def run_batch_with_order(self, data, mutations, *, observed_revision=None):
        runtime = bridge(data)
        order = []
        original_consume = runtime.consume

        def consume(records):
            order.append('intent')
            return original_consume(records)

        with patch.object(runtime, 'consume', side_effect=consume):
            outcomes = runtime.consume_batch(
                mutations,
                [intent(1)],
                observed_revision=observed_revision,
                after_mutations=lambda: order.append('refresh'),
            )
        return runtime, order, outcomes

    def test_ignored_unrelated_mutation_does_not_refresh_environment(self):
        data = world()
        _, order, outcomes = self.run_batch_with_order(
            data, [write('root', 'CanTouch', False)])

        self.assertEqual(order, ['intent'])
        self.assertEqual(len(outcomes), 1)

    def test_stale_humanoid_sit_write_does_not_refresh_environment(self):
        data = world()
        humanoid = next(node for node in data['nodes'] if node['id'] == 'humanoid')
        humanoid['property_revisions'] = {'Sit': 5}
        _, order, outcomes = self.run_batch_with_order(
            data, [write('humanoid', 'Sit', True)], observed_revision=4)

        self.assertEqual(order, ['intent'])
        self.assertEqual(len(outcomes), 1)

    def test_effective_humanoid_sit_refreshes_before_intent(self):
        _, order, outcomes = self.run_batch_with_order(
            world(), [write('humanoid', 'Sit', True)])

        self.assertEqual(order, ['refresh', 'intent'])
        self.assertEqual(len(outcomes), 1)

    def test_effective_force_topology_create_refreshes_before_intent(self):
        create = {
            'kind': 'create',
            'node': {
                'id': 'local:weld',
                'class_name': 'WeldConstraint',
                'parent': 'root',
                'properties': {
                    'Part0': {'$ref': 'root'},
                    'Part1': {'$ref': 'limb'},
                    'Enabled': True,
                },
            },
        }
        # The fixture root needs one additional received part to link.
        data = world()
        data['nodes'].append({
            'id': 'limb', 'class_name': 'Part', 'name': 'limb', 'parent': 'char',
            'properties': {'Anchored': False, 'Position': {'$vector3': [1, 20, 0]}},
        })
        _, order, outcomes = self.run_batch_with_order(data, [create])

        self.assertEqual(order, ['refresh', 'intent'])
        self.assertEqual(len(outcomes), 1)


if __name__ == '__main__':
    unittest.main()
