"""Data integrity and electrical invariants; not an IEEE reference certification."""
from pathlib import Path
import hashlib
import unittest
import numpy as np
import pandapower as pp
from core.schemas import TimeGrid, make_profile
from network.ieee123_loader import load_ieee123, create_pandapower_network, read_ieee123_data
from network.pandapower_solver import PandapowerSolver, solve_snapshot, POWER_COLUMNS


class TestIEEE123(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = load_ieee123()
        cls.grid = TimeGrid(steps=1)
        cls.zero = make_profile(cls.grid,"none","150","ABC",[0])
        cls.flow = PandapowerSolver().solve(cls.network,cls.zero)

    def test_dataset_provenance_and_topology(self):
        d = read_ieee123_data()
        self.assertEqual(len(d["lines"]),118)
        self.assertEqual(len(d["loads"]),91)
        self.assertEqual(len(d["buses"]),130)  # equipment terminals retained
        self.assertEqual(sum(x["p_kw"] for x in d["loads"]),3490.)
        self.assertEqual(sum(x["q_kvar"] for x in d["loads"]),1920.)
        self.assertEqual(sum(x["q_kvar"] for x in d["capacitors"]),750.)
        self.assertEqual(sum(x["closed"] for x in d["switches"]),6)
        root = Path(__file__).resolve().parents[1]/"data/ieee123/source"
        for name,digest in d["provenance"]["sha256"].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
        n = create_pandapower_network(self.network)
        self.assertEqual(len(n.trafo),5)
        self.assertEqual(n.asymmetric_load.type.eq("delta").sum(),7)
        self.assertEqual(set(n.asymmetric_load.source_model),{1,2,5})
        line = n.line.loc[n.line.name.eq("l1")].iloc[0]
        self.assertAlmostEqual(line.length_km,.175*.3048)
        self.assertAlmostEqual(line.r_ohm_per_km,.251742424/.3048)
        self.assertEqual(n.bus.loc[n.bus.name.eq("2"),"phases"].iloc[0],"B")
        self.assertFalse(self.network.synthetic)

    def test_balance_and_physical_phase_reporting(self):
        f = self.flow
        self.assertTrue(f["source"].converged.all())
        self.assertGreater(f["source"].p_kw.iloc[0],3490.)
        balance = f["source"].p_kw.sum()-f["native_loads"].p_kw.sum()-f["branches"].loss_kw.sum()
        self.assertLess(abs(balance),.001)
        self.assertEqual(set(f["voltages"].query("bus=='2'").phase),{"B"})
        self.assertGreater(f["voltages"].v_pu.min(),.9)
        self.assertLess(f["voltages"].v_pu.max(),1.1)
        self.assertEqual(set(f["branches"].element_type),{"line","trafo"})

    def test_injection_sign_and_no_scenario_state_leak(self):
        solver = PandapowerSolver()
        generation = solver.solve(self.network,make_profile(self.grid,"gen","67","ABC",[60]))
        consumption = solver.solve(self.network,make_profile(self.grid,"ev","67","ABC",[-60]))
        p0 = self.flow["source"].p_kw.iloc[0]
        self.assertLess(generation["source"].p_kw.iloc[0],p0-50)
        self.assertGreater(consumption["source"].p_kw.iloc[0],p0+50)
        for flow,p in ((generation,60),(consumption,-60)):
            residual = flow["source"].p_kw.sum()+p-flow["native_loads"].p_kw.sum()-flow["branches"].loss_kw.sum()
            self.assertLess(abs(residual),.001)
        np.testing.assert_allclose(solver.solve(self.network,self.zero)["source"].p_kw,self.flow["source"].p_kw,atol=1e-6)

    def test_zip_terminal_voltage_scaling(self):
        n = create_pandapower_network(self.network)
        nominal = n.asymmetric_load[POWER_COLUMNS].copy()
        solve_snapshot(n,nominal)
        for name,exponent in (("s1a",0),("s5c",1),("s6c",2),("s65b",2),("c88a",2)):
            idx = n.asymmetric_load.index[n.asymmetric_load.name.eq(name)][0]
            row = n.asymmetric_load.loc[idx]
            bus = n.res_bus_3ph.loc[row.bus]
            v = np.array([bus[f"vm_{p}_pu"]*np.exp(1j*np.deg2rad(bus[f"va_{p}_degree"])) for p in "abc"])*n.bus.at[row.bus,"vn_kv"]/np.sqrt(3)
            if row.type=="delta": v = v-np.roll(v,-1)
            scale = (abs(v)/row.nominal_kv)**exponent
            np.testing.assert_allclose(row[POWER_COLUMNS].to_numpy(float),nominal.loc[idx].to_numpy()*np.tile(scale,2),atol=1e-8)

    def test_fail_closed_on_bad_inputs_or_nonfinite_solver(self):
        from unittest.mock import patch
        with self.assertRaises(FileNotFoundError): load_ieee123("missing-ieee.json")
        with self.assertRaises(ValueError):
            PandapowerSolver([1,1]).solve(self.network,self.zero)
        with self.assertRaises(ValueError):
            PandapowerSolver().solve(self.network,make_profile(self.grid,"bad","2","A",[1]))
        n = create_pandapower_network(self.network)
        pp.runpp_3ph(n,numba=False,max_iteration=100)
        n.res_bus_3ph.iloc[0,0] = np.nan
        with patch("network.pandapower_solver.pp.runpp_3ph"):
            with self.assertRaisesRegex(RuntimeError,"non-finite"):
                solve_snapshot(n,n.asymmetric_load[POWER_COLUMNS].copy())
