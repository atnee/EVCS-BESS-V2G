import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import networkx as nx
import numpy as np
import yaml
from network.ieee123_loader import create_pandapower_network
from network.topology import feeder_graph, energized, graph_metrics, line_table, linecode_table, electrical_inventory

ROOT = Path(__file__).resolve().parents[1]


class TestS0Topology(unittest.TestCase):
    def test_graph_is_radial_with_two_tie_switches(self):
        g = feeder_graph()
        self.assertEqual((g.number_of_nodes(),g.number_of_edges()),(130,131))
        self.assertTrue(nx.is_tree(energized(g)))
        m = graph_metrics(g)
        self.assertEqual((m["open_switches"],m["loops_if_open_switches_closed"]),(2,2))
        self.assertEqual(m["main_path"][0],"150")

    def test_line_parameters_match_pandapower_model(self):
        lines = line_table().set_index("line")
        pp_lines = create_pandapower_network().line.set_index("name").loc[lines.index]
        for ours,theirs in (("r1_ohm_km","r_ohm_per_km"),("x1_ohm_km","x_ohm_per_km"),("r0_ohm_km","r0_ohm_per_km"),
                            ("x0_ohm_km","x0_ohm_per_km"),("c1_nf_km","c_nf_per_km")):
            np.testing.assert_allclose(lines[ours],pp_lines[theirs],err_msg=ours)

    def test_inductance_is_reactance_over_omega(self):
        t = linecode_table()
        np.testing.assert_allclose(t.l1_mh_km,t.x1_ohm_km/(2*np.pi*60)*1e3)
        self.assertEqual(t.lines.sum(),118)

    def test_inventory_totals(self):
        totals = electrical_inventory()["totals"]
        self.assertEqual((totals.load_kw,totals.load_kvar,totals.capacitor_kvar),(3490.,1920.,750.))


class TestS0Study(unittest.TestCase):
    def test_export_short_horizon(self):
        from integration.s0_study import export_s0
        with TemporaryDirectory() as tmp:
            configs = Path(tmp)/"configs"; shutil.copytree(ROOT/"configs",configs)
            for name in ("evcs","bess","v2g"):
                path = configs/f"{name}.yaml"; data = yaml.safe_load(path.read_text())
                data["time"]["steps"] = 2; path.write_text(yaml.safe_dump(data))
            path = configs/"ieee123.yaml"; data = yaml.safe_load(path.read_text())
            data["load_multipliers"] = [.55,1.]; path.write_text(yaml.safe_dump(data))
            summary = export_s0(Path(tmp)/"s0",configs)
            self.assertTrue(.95 < summary["peak_vmin_pu"] < 1.)
            self.assertGreater(summary["peak_losses_kw"],0)
            for name in ("topology","voltage_map","voltage_profile","daily","linecodes"):
                self.assertTrue((Path(tmp)/"s0"/f"{name}.png").stat().st_size > 10_000)
            self.assertEqual(json.loads((Path(tmp)/"s0"/"summary.json").read_text())["graph"]["buses"],130)
