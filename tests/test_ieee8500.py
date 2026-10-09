"""IEEE 8500 medium-voltage reduction: data integrity and electrical invariants (not an IEEE certification)."""
from pathlib import Path
import hashlib
import unittest
import networkx as nx
import numpy as np
import pandas as pd
from network.feeder_loader import load_feeder, read_feeder_data, create_pandapower_network, feeder_info
from network.pandapower_solver import solve_snapshot, POWER_COLUMNS
from network.topology import feeder_graph, energized, bus_table
from evcs.planning import thin_candidates, distances_m


class TestIEEE8500(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = read_feeder_data("ieee8500")
        cls.network = load_feeder("ieee8500")
        cls.net = create_pandapower_network(cls.network)
        cls.nominal = cls.net.asymmetric_load[POWER_COLUMNS].copy()
        solve_snapshot(cls.net,cls.nominal)

    def test_dataset_provenance_and_counts(self):
        d = self.data
        self.assertEqual(len(d["buses"]),2521)
        self.assertEqual(len(d["lines"]),2483)
        self.assertEqual(len(d["loads"]),1177)
        self.assertAlmostEqual(sum(x["p_kw"] for x in d["loads"]),10773.17,places=2)
        self.assertEqual(sum(x["q_kvar"] for x in d["capacitors"]),3*(300+300+400)+900)
        self.assertEqual(sum(not s["closed"] for s in d["switches"]),5)
        self.assertEqual([r["id"] for r in d["regulators"]][0],"feeder_reg")  # upstream first
        self.assertTrue(all(b["xy"] for b in d["buses"]))
        root = Path(__file__).resolve().parents[1]/"data/ieee8500/source"
        for name,digest in d["provenance"]["sha256"].items():
            self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)

    def test_study_adjustments_are_explicit(self):
        d = self.data
        self.assertEqual(d["source_pu"],1.0)
        self.assertTrue(all(r["vreg_pu"] == 1.03 and r["vreg_reference_pu"] > 1.04 for r in d["regulators"]))
        self.assertEqual(d["linecodes"]["3ph_h-397_acsr397_acsr397_acsr397_acsr"]["ampacity_a"],587.)
        self.assertEqual(d["linecodes"]["1ph-x4_acsrx4_acsr"]["ampacity_a"],140.)
        self.assertEqual(d["linecodes"]["1p_1/0_axnj_db"]["ampacity_a"],220.)  # source normamps kept
        self.assertEqual([a["item"] for a in d["provenance"]["adjustments"]],["line ampacity","voltage level"])

    def test_radial_and_connected(self):
        graph = feeder_graph(self.data)
        self.assertTrue(nx.is_tree(energized(graph)))
        km = bus_table(graph).distance_km.max()
        self.assertGreater(km,15.)  # much longer than the IEEE123 (~2 km)

    def test_nominal_power_flow(self):
        v = self.net.res_bus_3ph[[f"vm_{p}_pu" for p in "abc"]].to_numpy()
        mask = np.array([[ph in p for ph in "ABC"] for p in self.net.bus.phases])
        v = v[mask]
        self.assertGreater(v.min(),.95)
        self.assertLessEqual(v.max(),1.05)  # adjusted voltage level: ANSI range A in the base case
        p_kw = self.net.res_ext_grid_3ph[[f"p_{p}_mw" for p in "abc"]].sum().sum()*1000
        self.assertGreater(p_kw,10773.)  # load plus losses
        self.assertLess(p_kw,10773.*1.15)

    def test_regulators_hold_their_band(self):
        for r in self.net.regulator_control:
            res = self.net.res_bus_3ph.loc[r["lv_bus"]]
            v = np.mean([res[f"vm_{ph.lower()}_pu"] for ph in r["phases"]])
            tap = self.net.trafo.at[r["trafo"],"tap_pos"]
            self.assertTrue(abs(v-r["vreg_pu"]) <= r["band_pu"]/2 or abs(tap) == 16,r["name"])

    def test_light_load_moves_taps_down(self):
        net = create_pandapower_network(self.network)
        light = self.nominal.copy()
        light.iloc[:len(self.data["loads"])] *= .55
        solve_snapshot(net,light)
        self.assertLess(net.trafo.tap_pos[:4].sum(),self.net.trafo.tap_pos[:4].sum())
        v = net.res_bus_3ph[[f"vm_{p}_pu" for p in "abc"]].to_numpy()
        self.assertLess(np.nanmax(v),1.07)

    def test_unknown_feeder(self):
        with self.assertRaises(ValueError):
            feeder_info("ieee13")


class TestCandidateThinning(unittest.TestCase):
    def test_spacing_and_preference(self):
        # Coordinates in feet, spacing in metres: 500 m = 1640 ft.
        c = pd.DataFrame(dict(bus=list("abcd"),x=[0,200,5000,10000],y=[0]*4))
        demand = pd.DataFrame(dict(bus=["p","q"],x=[-1500,1700],y=[0,0],weight=[1.,5.]))
        kept = thin_candidates(c,demand,500.)
        d = distances_m(kept[["x","y"]],kept[["x","y"]])
        self.assertTrue((d[~np.eye(len(kept),dtype=bool)] >= 500.).all())
        self.assertEqual(list(kept.bus),["b","c","d"])  # b has more demand within 500 m than a
        self.assertEqual(len(thin_candidates(c,demand,0)),4)


if __name__ == "__main__":
    unittest.main()
