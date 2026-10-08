import unittest
import numpy as np
from core.schemas import Bus,Line,Network,TimeGrid,make_profile
from integration.scenarios import synthetic_network, baseline
from integration.coordinator import combine
from network.power_flow import SyntheticRadialSolver
from network.ieee123_loader import load_ieee123

class TestNetwork(unittest.TestCase):
    def test_power_balance(self):
        n=synthetic_network();g=TimeGrid(steps=3)
        p=combine(baseline(g),n);f=SyntheticRadialSolver().solve(n,p)
        losses=f["branches"].groupby("time").loss_kw.sum().to_numpy()
        np.testing.assert_allclose(f["source"].p_kw.to_numpy(),-p.total_injection()+losses,atol=1e-6)
        self.assertLess(f["voltages"].v_pu.min(),1)
    def test_single_branch_analytical(self):
        n=Network("analytic",[Bus("s","A",1000),Bus("l","A",1000)],[Line("e","s","l","A",1,0,100)],"s",True)
        p=make_profile(TimeGrid(steps=1),"load","l","A",[-10])
        f=SyntheticRadialSolver().solve(n,p)
        expected=(1000+np.sqrt(1000**2-4*10000))/2/1000
        self.assertAlmostEqual(f["voltages"].query("bus=='l'").v_pu.iloc[0],expected,places=8)
    def test_reverse_flow(self):
        n=synthetic_network();p=make_profile(TimeGrid(steps=1),"gen","n2","ABC",[100])
        f=SyntheticRadialSolver().solve(n,p)
        self.assertLess(f["source"].p_kw.iloc[0],0)
        self.assertGreater(f["voltages"].v_pu.max(),1)
    def test_ieee_never_silently_substituted(self):
        self.assertFalse(load_ieee123().synthetic)
        n=synthetic_network();n.synthetic=False
        with self.assertRaises(NotImplementedError): SyntheticRadialSolver().solve(n,baseline(TimeGrid())[0])
    def test_bad_phase(self):
        p=make_profile(TimeGrid(steps=1),"x","n3","B",[-2])
        with self.assertRaises(ValueError): p.validate(synthetic_network())
    def test_disconnected(self):
        n=synthetic_network();n.lines.pop()
        with self.assertRaises(ValueError): SyntheticRadialSolver().solve(n,baseline(TimeGrid())[0])
