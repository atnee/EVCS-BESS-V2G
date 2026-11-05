import unittest
import numpy as np
from bess.model import Battery, simulate
from bess.dispatch import peak_shave

class TestBattery(unittest.TestCase):
    def test_energy_conservation_with_losses(self):
        b=Battery(capacity_kwh=100,power_kw=30)
        d=simulate(b,[-20,10,5],.5)
        self.assertAlmostEqual(d.soc[-1]*100,50+20*.5*.95-15*.5/.95)
    def test_limits_and_rejected_commands(self):
        d=simulate(Battery(),[1000]*10,1)
        self.assertTrue((d.injection_kw<=30).all())
        self.assertAlmostEqual(d.soc[-1],.1)
        self.assertGreater(d.rejected_kw.sum(),0)
    def test_final_state_is_enforced(self):
        with self.assertRaises(ValueError): simulate(Battery(),[10],1,final_soc=.5)
    def test_peak_shaving_terminal_energy(self):
        d=peak_shave(Battery(),[20,100,100,10,10,10],70,1)
        self.assertAlmostEqual(d.soc[-1],.5)
        self.assertGreater(d.injection_kw[1],0)
    def test_invalid_inputs(self):
        for dt in (0,-1,float('nan')):
            with self.assertRaises(ValueError): simulate(Battery(),[0],dt)
        with self.assertRaises(ValueError): Battery(eta_charge=1.1)


class TestLineLimit(unittest.TestCase):
    """bess.strategies.line_limit on a one-line case solvable by hand."""
    def setUp(self):
        self.battery = Battery(capacity_kwh=1000.,power_kw=500.,soc_initial=.5,soc_min=.1,soc_max=.9,
                               eta_charge=.95,eta_discharge=.95)
        # Loading 90 % + 0.1 %/kW x demand: 110 % at t=2, 90 % elsewhere; the BESS moves it 0.1 %/kW.
        self.config = {"line_limit": {"target_pct": 100.,"overload_weight": 1000.,"use_weight": .001,
                                      "lines": {"x": {"a": 90.,"b": .1,"s": .1}}}}
        self.demand = np.array([0.,0.,200.,0.])

    def test_discharges_exactly_down_to_target_and_recharges_within_headroom(self):
        from bess.strategies import line_limit
        d = line_limit(self.battery,self.demand,self.config,1.)
        self.assertAlmostEqual(d.injection_kw[2],100.,places=4)  # (110 - 100) / 0.1
        after = 90+.1*self.demand-.1*d.injection_kw
        self.assertLessEqual(after.max(),100.+1e-6)
        self.assertAlmostEqual(-d.injection_kw[d.injection_kw < 0].sum(),100/.95/.95,places=4)  # energy balance
        self.assertAlmostEqual(d.soc[-1],self.battery.soc_initial)
        self.assertLess(np.abs(d.rejected_kw).max(),1e-6)

    def test_margin_lowers_the_line_limit(self):
        from bess.strategies import line_limit
        config = {"line_limit": {**self.config["line_limit"],"lines": {"x": {"a": 90.,"b": .1,"s": .1,"margin": 2.}}}}
        self.assertAlmostEqual(line_limit(self.battery,self.demand,config,1.).injection_kw[2],120.,places=4)  # (110-98)/0.1

    def test_idle_without_monitored_lines(self):
        from bess.strategies import line_limit
        config = {"line_limit": {**self.config["line_limit"],"lines": {}}}
        np.testing.assert_allclose(line_limit(self.battery,self.demand,config,1.).injection_kw,0.)
