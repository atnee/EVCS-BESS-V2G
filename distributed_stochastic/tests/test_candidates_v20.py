"""Testes reproduzíveis sem OpenDSS (a verificação AC real é feita no usuário)."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd
import yaml

from distributed_stochastic.candidates import (
    ScreenSettings, _energized_graph, _load_results, _allowed_gap,
    build_inventory, select_candidates, write_screen,
)
from distributed_stochastic.ac_probe import _metric, _matching_effect


class TestCandidatesV20(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root/"repo"
        self.ref = self.root/"baseline"
        (self.repo/"src/network/data").mkdir(parents=True)
        (self.repo/"configs").mkdir(parents=True)
        (self.ref/"S0").mkdir(parents=True)
        (self.ref/"S1").mkdir(parents=True)
        self.data = {
            "name":"IEEE 8500", "source_bus":"source",
            "buses":[
                {"id":"source","phases":"ABC","vn_kv":115,"xy":[0,0]},
                {"id":"txmv","phases":"ABC","vn_kv":12.47,"xy":[0,0]},
                {"id":"l3029502","phases":"ABC","vn_kv":12.47,"xy":[2000,0]},
                {"id":"m1069504","phases":"ABC","vn_kv":12.47,"xy":[5000,0]},
                {"id":"third","phases":"ABC","vn_kv":12.47,"xy":[8000,0]},
                {"id":"mono","phases":"A","vn_kv":12.47,"xy":[10000,0]},
                {"id":"open","phases":"ABC","vn_kv":12.47,"xy":[12000,0]},
            ],
            "lines":[
                {"id":"ln0","phases":"ABC","from_bus":"txmv","to_bus":"l3029502","length_km":0.8},
                {"id":"ln1","phases":"ABC","from_bus":"l3029502","to_bus":"m1069504","length_km":0.5},
                {"id":"ln2","phases":"ABC","from_bus":"m1069504","to_bus":"third","length_km":0.5},
                {"id":"ln3","phases":"A","from_bus":"third","to_bus":"mono","length_km":0.2},
            ],
            "transformer":{"from_bus":"source","to_bus":"txmv"},
            "regulators":[],"switches":[{"from_bus":"third","to_bus":"open","closed":False}],
            "capacitors":[],
            "loads":[{"bus":"third","p_kw":250},{"bus":"m1069504","p_kw":150}],
        }
        (self.repo/"src/network/data/ieee8500.json").write_text(json.dumps(self.data))
        (self.repo/"configs/evcs.yaml").write_text(yaml.safe_dump({"integration":{"sites":{"third":"ac"}},"station":{"bus":"m1069504"}}))
        (self.repo/"configs/bess.yaml").write_text(yaml.safe_dump({"connection":{"bus":"l3029502"}}))
        rows_volt,rows_branches=[],[]
        for h in range(24):
            for b in self.data["buses"]:
                for ph in b["phases"]:
                    if b["id"] != "open":
                        rows_volt.append({"hour":h,"bus":b["id"],"phase":ph,"v_pu":.96})
            for ln in self.data["lines"]:
                for ph in ln["phases"]:
                    rows_branches.append({"hour":h,"line":ln["id"],"phase":ph,"loading_pct":75.})
        for sc in ("S0","S1"):
            v=pd.DataFrame(rows_volt)
            l=pd.DataFrame(rows_branches)
            if sc=="S1":
                v.loc[v.bus=="m1069504","v_pu"] = .932
                l.loc[l.line=="ln1","loading_pct"] = 116.
            v.to_csv(self.ref/sc/"voltages.csv",index=False)
            l.to_csv(self.ref/sc/"branches.csv",index=False)

    def test_inventory_and_original_buses(self):
        s=ScreenSettings(bess_count=3,v2g_count=3,spacing_m=200,electrical_spacing_km=0)
        inv,meta=build_inventory(self.repo,self.ref,s)
        self.assertEqual(meta["new_line_count"],1)
        eligible=set(inv.loc[inv.eligible,"bus"])
        self.assertEqual(eligible,{"l3029502","m1069504","third"})
        self.assertEqual(inv.set_index("bus").at["mono","exclusion_reasons"],"not_three_phase;no_adjacent_three_phase_line")
        b=select_candidates(inv,self.data,s,"bess")
        v=select_candidates(inv,self.data,s,"v2g")
        self.assertEqual(b.iloc[0].bus,"l3029502")
        self.assertEqual(v.iloc[0].bus,"m1069504")
        self.assertEqual(len(b),3)
        self.assertEqual(len(v),3)
        self.assertEqual(v.iloc[0].infrastructure_status,"existing_v2g_host")
        self.assertEqual(b.iloc[0].infrastructure_status,"existing_bess_host")

    def test_evcs_only_selection(self):
        s=ScreenSettings(v2g_only_existing_evcs=True,spacing_m=0,electrical_spacing_km=0)
        inv,_=build_inventory(self.repo,self.ref,s)
        v=select_candidates(inv,self.data,s,"v2g")
        self.assertEqual(set(v.bus),{"m1069504","third"})
        self.assertEqual(v.loc[v.bus=="third","infrastructure_status"].iat[0],"evcs_upgrade_required")

    def test_spatial_spacing_and_anchor(self):
        s=ScreenSettings(spacing_m=1000,electrical_spacing_km=0)
        inv,_=build_inventory(self.repo,self.ref,s)
        b=select_candidates(inv,self.data,s,"bess")
        # Anchor conservada; não escolher segunda barra a menos de 1 km geométrico.
        self.assertIn("l3029502",set(b.bus))
        self.assertEqual(set(b.bus),{"l3029502","third"})

    def test_write_manifest(self):
        out=self.root/"out"
        meta=write_screen(self.repo,self.ref,out,ScreenSettings(spacing_m=0,electrical_spacing_km=0))
        self.assertEqual(meta["selected"]["bess"],3)
        self.assertTrue((out/"candidatos_v2g.csv").is_file())
        self.assertTrue((out/"manifest_v20.json").is_file())

    def test_missing_or_bad_baseline(self):
        p=self.ref/"S1/voltages.csv"
        df=pd.read_csv(p)
        df=df.loc[df.hour!=23]
        df.to_csv(p,index=False)
        with self.assertRaisesRegex(ValueError,"faltam horários"):
            _load_results(self.ref)

    def test_ac_metric_and_alignment(self):
        b=pd.DataFrame([{"hour":18,"line":"a","phase":"A","loading_pct":110.},
                        {"hour":18,"line":"b","phase":"B","loading_pct":100.}])
        v=pd.DataFrame([{"hour":18,"bus":"a","phase":"A","v_pu":.94},
                        {"hour":18,"bus":"b","phase":"B","v_pu":1.06}])
        m=_metric(b,v)
        self.assertEqual(m["overload_samples"],1)
        self.assertAlmostEqual(m["thermal_severity_pp"],10.)
        self.assertAlmostEqual(m["undervoltage_severity_pu"],.01)
        delta=_matching_effect(b,b,["hour","line","phase"],"loading_pct")
        self.assertEqual(len(delta),2)
        self.assertAlmostEqual(float((delta.loading_pct_probe-delta.loading_pct_base).abs().sum()),0.)

    def test_graph_open_switch(self):
        import networkx as nx
        g=_energized_graph(self.data)
        self.assertFalse(nx.has_path(g,"source","open"))


if __name__ == "__main__":
    unittest.main()
