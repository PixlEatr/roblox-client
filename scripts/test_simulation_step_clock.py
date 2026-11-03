import math
import unittest

from simulation_step_clock import SimulationStepClock


class SimulationStepClockTests(unittest.TestCase):
    def test_only_completed_simulated_intervals_advance_step(self):
        clock=SimulationStepClock(units_per_second=240)
        clock.advance(0.)
        self.assertEqual(clock.step,0)
        for _ in range(60):clock.advance(1/60)
        self.assertEqual(clock.step,240)
        self.assertFalse(clock.report()['native_scheduler_parity_verified'])
        self.assertEqual(clock.report()['completed_intervals'],61)

    def test_partial_units_survive_between_intervals(self):
        clock=SimulationStepClock(units_per_second=240)
        clock.advance(.001);self.assertEqual(clock.step,0)
        clock.advance(.004);self.assertEqual(clock.step,1)
        self.assertAlmostEqual(clock.fractional_units,.2)

    def test_invalid_intervals_do_not_mutate_clock(self):
        clock=SimulationStepClock(units_per_second=240)
        clock.advance(.1);before=clock.report()
        for value in (True,-.1,.251,math.nan,math.inf):
            with self.assertRaises(ValueError):clock.advance(value)
            self.assertEqual(clock.report(),before)

    def test_wire_rollover_keeps_unbounded_local_count(self):
        clock=SimulationStepClock(units_per_second=240)
        clock.whole_units=0xfffffffe
        clock.advance(1/60)
        self.assertEqual(clock.step,2)
        self.assertEqual(clock.whole_units,0x100000002)


if __name__=='__main__':unittest.main()
