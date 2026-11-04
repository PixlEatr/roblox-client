import unittest

from physics_owner_mapping import map_network_owner_v3


class NetworkOwnerMappingTests(unittest.TestCase):
    def test_schema_value_alone_is_unknown(self):
        self.assertIsNone(map_network_owner_v3(1234, 1234))

    def test_account_user_id_is_not_assumed_to_be_peer_id(self):
        evidence = {
            'source': 'native_trace',
            'owner_domain': 'account_user_id',
            'local_identity_domain': 'transport_peer_id',
            'comparison': 'numeric_equality',
        }
        self.assertIsNone(map_network_owner_v3(1234, 1234, evidence))

    def test_requires_native_trace_and_same_domain(self):
        evidence = {
            'source': 'native_trace',
            'owner_domain': 'transport_peer_id',
            'local_identity_domain': 'transport_peer_id',
            'comparison': 'numeric_equality',
        }
        self.assertTrue(map_network_owner_v3(12, 12, evidence))
        self.assertFalse(map_network_owner_v3(12, 13, evidence))
        evidence['source'] = 'schema_guess'
        self.assertIsNone(map_network_owner_v3(12, 12, evidence))

    def test_documented_unowned_sentinel_takes_precedence_over_equality(self):
        evidence = {
            'source': 'native_trace',
            'owner_domain': 'transport_peer_id',
            'local_identity_domain': 'transport_peer_id',
            'comparison': 'numeric_equality',
        }
        self.assertTrue(map_network_owner_v3(0, 0, evidence))

        evidence['unowned_value'] = 0
        self.assertFalse(map_network_owner_v3(0, 0, evidence))
        self.assertFalse(map_network_owner_v3(0, 12, evidence))
        self.assertTrue(map_network_owner_v3(12, 12, evidence))

    def test_invalid_unowned_sentinel_remains_unknown(self):
        evidence = {
            'source': 'native_trace',
            'owner_domain': 'transport_peer_id',
            'local_identity_domain': 'transport_peer_id',
            'comparison': 'numeric_equality',
        }
        for sentinel in (True, -1, 1.0, None):
            with self.subTest(sentinel=sentinel):
                evidence['unowned_value'] = sentinel
                self.assertIsNone(map_network_owner_v3(1, 1, evidence))

    def test_invalid_numeric_values_remain_unknown(self):
        evidence = {
            'source': 'native_trace',
            'owner_domain': 'transport_peer_id',
            'local_identity_domain': 'transport_peer_id',
            'comparison': 'numeric_equality',
        }
        for owner, local in ((True, 1), (-1, 1), (1, None), (1.0, 1)):
            with self.subTest(owner=owner, local=local):
                self.assertIsNone(map_network_owner_v3(owner, local, evidence))


if __name__ == '__main__':
    unittest.main()
