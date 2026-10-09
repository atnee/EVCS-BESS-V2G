"""Scenario impact tables (integration.impact) and the solver's fixed-tap mode."""
import unittest
import numpy as np
import pandas as pd
from integration.impact import voltage_change, impact_summary, tap_operations


def flow(v, taps, p=(100.,120.)):
    times = pd.date_range("2026-01-01",periods=2,freq="h",tz="UTC")
    volt = pd.DataFrame([dict(time=t,bus=b,phase="A",v_pu=v[b][i]) for i,t in enumerate(times) for b in v])
    source = pd.DataFrame({"time":times,"p_kw":list(p),"tap_reg":taps})
    branches = pd.DataFrame({"time":times,"loss_kw":[1.,2.]})
    return {"voltages":volt,"source":source,"branches":branches}


class TestImpact(unittest.TestCase):
    def setUp(self):
        self.buses = pd.DataFrame({"bus":["src","a","b"],"distance_km":[0.,1.,2.],"x":[0,1,2],"y":[0,0,0]})
        self.base = flow({"src":[1,1],"a":[1.,1.],"b":[.99,.98]},[0,0])
        self.case = flow({"src":[1,1],"a":[.995,.985],"b":[.98,.96]},[0,2],p=(110.,150.))

    def test_voltage_change_excludes_source(self):
        d = voltage_change(self.base,self.case,self.buses,"src")
        self.assertNotIn("src",set(d.bus))
        self.assertAlmostEqual(d.dv_pu.min(),-.02)

    def test_summary(self):
        s = impact_summary(self.base,self.case,self.buses,"src",frozen=self.case)
        self.assertAlmostEqual(s["regulated"]["max_drop_pct"],2.)
        self.assertEqual(s["regulated"]["buses_drop_over_1pct"],2)
        self.assertEqual(s["peak_source_kw"],150.)
        self.assertEqual(tap_operations(self.case),{"reg":2})
        self.assertEqual(s["losses_kwh"],3.)  # 1 + 2 kW over two 1 h steps

    def test_losses_are_energy_at_any_step(self):
        quarter = {k: v.copy() for k,v in self.case.items()}
        quarter["source"]["time"] = pd.date_range("2026-01-01",periods=2,freq="15min",tz="UTC")
        self.assertEqual(impact_summary(self.base,quarter,self.buses,"src")["losses_kwh"],.75)


class TestFixedTaps(unittest.TestCase):
    def test_taps_follow_the_table_on_ieee8500(self):
        from core.schemas import TimeGrid, make_profile
        from network.feeder_loader import load_feeder
        from network.pandapower_solver import PandapowerSolver
        network = load_feeder("ieee8500")
        grid = TimeGrid(steps=2)
        zero = make_profile(grid,"z",network.slack_bus,"ABC",np.zeros(2))
        taps = pd.DataFrame({"tap_feeder_reg":[3,4],"tap_vreg4":[5,6],"tap_vreg3":[5,6],"tap_vreg2":[2,3]})
        out = PandapowerSolver([.6,.6],fixed_taps=taps).solve(network,zero)
        np.testing.assert_array_equal(out["source"][taps.columns].to_numpy(),taps.to_numpy())


if __name__ == "__main__":
    unittest.main()
