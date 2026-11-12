"""Figs. 2-6 helpers (integration.paper_s1_figures, integration.loading_maps.corridors) on small tables."""
import unittest
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
from integration.loading_maps import corridors
from integration.paper_s1_figures import penetration_figure, substation_figure, transformers_figure


class TestPaperS1Figures(unittest.TestCase):
    def tearDown(self):
        import matplotlib.pyplot as plt
        plt.close("all")

    def test_peak_increase_is_relative_to_s0_peak(self):
        t = pd.DataFrame({"carros": [0,1000,3000],"ponta_kw": [11750.,12400.,13950.],"vmin_pu": [.95,.949,.933],
                          "vmin_sem_reg_pu": [.95,.942,.924],"linha_max_pct": [96.7,98.9,108.9]})
        fig = penetration_figure(t)["fig3_ev_penetration"]
        texts = [x.get_text() for ax in fig.axes for x in ax.texts]
        self.assertTrue(any(s.startswith("+2.20 MW at 3,000 EVs") for s in texts))

    def test_station_mark_only_on_transformers_with_a_station(self):
        t = pd.DataFrame({"id": list("abcd"),"kva": [10.,15.,5.,5.],"station": ["bus1","bus2",np.nan,np.nan],
                          "peak_s0_pct": [40.,42.,36.,30.],"peak_s1_pct": [194.,145.,36.,30.]})
        day = pd.DataFrame({k: np.ones(96) for k in "abcd"})
        fig = transformers_figure(t,day,most=2,least=2)["fig4_service_transformers"]
        labels = [x.get_text() for x in fig.axes[0].get_xticklabels()]
        self.assertEqual([l.split("\n")[0] for l in labels],["T1*","T2*","T3","T4"])

    def test_substation_legend_gives_each_peak(self):
        sources = {sc: pd.DataFrame({"p_kw": np.full(24,1000.*(k+1))}) for k,sc in enumerate(("S0","S1"))}
        fig = substation_figure(sources)["fig5_substation_demand"]
        labels = [t.get_text() for t in fig.axes[0].get_legend().get_texts()]
        self.assertEqual(labels,["S0: peak 1.00 MW","S1: peak 2.00 MW"])

    def test_corridors_group_overloaded_lines_by_conductor(self):
        peak = pd.Series({"l1": 108.,"l2": 101.,"l3": 103.,"l4": 99.,"l5": 120.})
        codes = {"l1": "3ph_h-397_acsr397_acsr397_acsr2/0_acsr","l2": "3ph_h-4_acsr4_acsr4_acsr4_wpal",
                 "l3": "3ph_h-2/0_acsr2/0_acsr2/0_acsr2_acsr","l4": "3ph_h-397_acsr397_acsr397_acsr397_acsr",
                 "l5": "1ph-x"}
        g = corridors(peak,codes)
        self.assertEqual({k: v[1] for k,v in g.items()},{"C1": ["l1"],"C2": ["l2"],"C3": ["l3"]})


if __name__ == "__main__":
    unittest.main()
