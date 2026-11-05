"""BESS line_limit calibration (integration.bess_calibration): fit and sensitivity on known data."""
import unittest
import numpy as np
import pandas as pd
from integration.bess_calibration import fit_lines, sensitivity


class TestBessCalibration(unittest.TestCase):
    def test_fit_recovers_line_and_residual(self):
        demand = np.array([1000.,2000.,3000.,4000.])
        loading = pd.DataFrame({"x": 50+.01*demand+np.array([0.,1.,-1.,0.])})
        f = fit_lines(loading,demand).loc["x"]
        self.assertAlmostEqual(f.b,.01-.0002,places=6)  # least squares of the perturbed line
        self.assertAlmostEqual(f.max_abs_residual,np.abs(loading.x-(f.a+f.b*demand)).max())
        self.assertLess(f.r2,1.)

    def test_sensitivity_ignores_idle_steps(self):
        injection = np.array([0.,100.,-50.,200.])
        change = pd.DataFrame({"x": -.04*injection,"y": 0.*injection})
        change.loc[0,"x"] = 5.  # the BESS is idle here: not part of the slope
        s = sensitivity(change,injection)
        self.assertAlmostEqual(s["x"],.04)
        self.assertAlmostEqual(s["y"],0.)
