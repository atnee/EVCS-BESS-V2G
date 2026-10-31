"""Confirm trial 9 isolates bus location from the trial-7 BESS dispatch."""
import unittest

import numpy as np

from bess.model import Battery
from bess.peak_reserve_trial import fixed_peak_reserve_trial, reference_dispatch
from bess.south_bus_trial9 import REFERENCE, experiment_config
from core.schemas import read_parameters


class TestSouthBusTrial9(unittest.TestCase):
    def test_only_connection_location_changes(self):
        prior = read_parameters(REFERENCE / "experiment_configs" / "bess.yaml")
        config = experiment_config()
        self.assertEqual(prior["connection"]["bus"], "l3029502")
        self.assertEqual(config["connection"]["bus"], "m1069504")
        self.assertEqual(config["battery"], prior["battery"])
        self.assertEqual(config["connection"]["phases"], prior["connection"]["phases"])
        self.assertEqual(config["peak_reserve_trial"]["commands_kw"],
                         prior["peak_reserve_trial"]["commands_kw"])
        battery = Battery(**config["battery"])
        for scenario in ("S2", "S4"):
            with self.subTest(scenario=scenario):
                demand, commands = reference_dispatch(REFERENCE, scenario)
                result = fixed_peak_reserve_trial(battery, demand, config, 1.0)
                np.testing.assert_allclose(result.injection_kw, commands, atol=1e-8)
                self.assertAlmostEqual(result.soc[-1], battery.soc_initial)
                self.assertLess(np.abs(result.rejected_kw).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()
