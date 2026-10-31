"""Check trial-7 energy balance and schedule feasibility."""
import unittest

import numpy as np

from bess.model import Battery
from bess.peak_reserve_trial import (ROOT, experiment_config, fixed_peak_reserve_trial,
                                     reference_dispatch)


class TestPeakReserveTrial(unittest.TestCase):
    def test_schedule_meets_energy_and_power_limits(self):
        config = experiment_config()
        battery = Battery(**config["battery"])
        reference = ROOT / config["peak_reserve_trial"]["reference"]
        for scenario in ("S2", "S4"):
            with self.subTest(scenario=scenario):
                demand, prior = reference_dispatch(reference, scenario)
                result = fixed_peak_reserve_trial(battery, demand, config, 1.0)
                self.assertAlmostEqual(result.soc[-1], .3)
                self.assertGreaterEqual(result.soc.min(), battery.soc_min)
                self.assertLessEqual(result.soc.max(), battery.soc_max)
                self.assertLessEqual(np.abs(result.injection_kw).max(), battery.power_kw)
                self.assertLess(np.abs(result.rejected_kw).max(), 1e-6)
                np.testing.assert_allclose(result.injection_kw, prior, atol=1e-8)

    def test_rejects_changed_demand(self):
        config = experiment_config()
        reference = ROOT / config["peak_reserve_trial"]["reference"]
        demand, _ = reference_dispatch(reference, "S2")
        demand[18] += 100
        with self.assertRaisesRegex(ValueError, "differs"):
            fixed_peak_reserve_trial(Battery(**config["battery"]), demand, config, 1.0)


if __name__ == "__main__":
    unittest.main()
