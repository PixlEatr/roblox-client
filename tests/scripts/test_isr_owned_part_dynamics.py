"""ISR-owned body state advances even when no Luau transaction completes."""
import unittest

from isr_replication import decode_packet
from isr_sender import OwnedIsrSender
from owned_part_poses import OwnedPartPoses
from test_isr_sender import prepared_world


class IsrOwnedDynamicsTests(unittest.TestCase):
    def test_network_tick_advances_owned_queue_without_character_sender_or_luau_poll(self):
        world, key = prepared_world()
        world.instances[(1, 6)]['properties']['Gravity'] = 10.
        world.instances[key]['properties'].update({'Size': [1.,1.,1.], 'Shape': 1,
                                                  'CanCollide': False})
        queue = OwnedPartPoses(simulate=True)
        queue.advance(world, 0.)
        self.assertFalse(queue.pending)
        sender = OwnedIsrSender(queue)
        sent = []
        sender.tick_prediction(world, {'simulation_step_clock': {'step': 24}},
            legacy_pv_enabled=True, now=.1,
            send_unreliable=lambda channel, payload, **kw: sent.append(payload))
        self.assertEqual(len(sent), 1)
        record = decode_packet(sent[0], world.schema, legacy_pv_enabled=True)['records'][0]
        self.assertEqual(record['key'], key)
        self.assertAlmostEqual(record['fields']['ReplicationPV']['position'][1], 9.95, delta=.01)
        self.assertAlmostEqual(record['fields']['ReplicationPV']['decoded_velocities'][0][1], -1., delta=.01)


if __name__ == '__main__':
    unittest.main()
