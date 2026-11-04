import unittest

from isr_clock import IsrClockEstimator


class IsrClockTests(unittest.TestCase):
    def test_unknown_until_both_remote_step_and_real_delivery_input_exist(self):
        clock=IsrClockEstimator()
        clock.observe_remote(1200)
        self.assertIsNone(clock.update(1000))
        clock.observe_delivery('lost',1000)
        self.assertIsNone(clock.update(1000))
        clock.observe_delivery('acknowledged',1000)
        self.assertEqual(clock.update(1010),200)
        self.assertEqual(clock.report()['losses'],1)

    def test_each_update_requires_fresh_inputs_and_reordering_cannot_rewind(self):
        c=IsrClockEstimator();c.observe_remote(1200);c.observe_delivery('acknowledged',1000)
        c.update(1010)
        c.observe_remote(1300);self.assertEqual(c.update(1100),200)
        c.observe_delivery('acknowledged',999);self.assertEqual(c.update(1100),200)
        c.observe_delivery('acknowledged',1090);self.assertEqual(c.update(1100),201)
        c.observe_remote(1250);c.observe_delivery('acknowledged',1100)
        self.assertEqual(c.update(1110),201)
        self.assertEqual(c.updates,2)

    def test_stabilization_uses_current_local_step_after_twenty_updates(self):
        c=IsrClockEstimator()
        for i in range(20):
            c.observe_remote(1200+i*10);c.observe_delivery('acknowledged',1000+i*10)
            c.update(1005+i*10)
        self.assertEqual(c.offset,200);self.assertFalse(c.stabilized)
        c.observe_remote(1400);c.observe_delivery('acknowledged',1200)
        self.assertEqual(c.update(1205),195);self.assertTrue(c.stabilized)

    def test_step_rollover_keeps_a_small_offset(self):
        c=IsrClockEstimator();c.observe_remote(5);c.observe_delivery('acknowledged',0xfffffff0)
        self.assertEqual(c.update(0xfffffff8),21)
        c.observe_remote(20);c.observe_delivery('acknowledged',5)
        self.assertEqual(c.update(10),20)


if __name__=='__main__':unittest.main()
