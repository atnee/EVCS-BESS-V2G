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
