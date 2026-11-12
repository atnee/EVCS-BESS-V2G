"""Fig. 6 (integration.loading_maps): one colour scale for every panel and the criterion's line counts."""
import unittest
import matplotlib
matplotlib.use("Agg")
import networkx as nx
import numpy as np
import pandas as pd
from integration.loading_maps import BOUNDS, SCENARIOS, colour_scale, loading_map_figures, panel_numbers


def tiny_feeder():
    g = nx.Graph()
    for i in range(4):
        g.add_node(f"b{i}",xy=(float(i),0.))
    for i in range(3):
        g.add_edge(f"b{i}",f"b{i+1}",kind="line",id=f"l{i}")
    return g


class TestLoadingMaps(unittest.TestCase):
    def test_new_overloads_exclude_lines_already_over_in_s0(self):
        s0 = pd.Series({"l0": 99.,"l1": 108.,"l2": 50.})
        n = panel_numbers(s0,pd.Series({"l0": 101.,"l1": 120.,"l2": 100.}))
        self.assertEqual((n["new"],n["new_max"],n["base"]),(1,101.,{"l1": 120.}))
        self.assertEqual(panel_numbers(s0,pd.Series({"l0": 90.,"l1": 105.,"l2": 60.}))["new"],0)

    def test_one_scale_for_all_panels(self):
        peaks = {sc: pd.Series({"l0": 40.+k,"l1": 95.,"l2": 104.+10*k}) for k,sc in enumerate(SCENARIOS)}
        cmap, norm, top = colour_scale(peaks)
        self.assertEqual(top,134.)
        self.assertEqual(list(norm.boundaries),BOUNDS)
        self.assertEqual(cmap(norm(134.)),cmap(norm(111.)))   # open top band
        self.assertNotEqual(cmap(norm(99.)),cmap(norm(101.)))  # 100 % is a band edge
        peaks["S0"] = pd.Series({"l0": 30.,"l1": 80.,"l2": 101.})
        figs = loading_map_figures(peaks,tiny_feeder(),"b0",{"S2": {"b3": "bess"},"S3": {"b2": "v2g"}},.3048)
        fig = figs["fig6_line_loading_maps"]
        maps = [ax for ax in fig.axes if ax.collections and ax.get_title(loc="left")]
        self.assertEqual(len(maps),4)
        # every panel draws its lines with the colours of the shared scale
        for ax,(sc,_) in zip(maps,SCENARIOS.items()):
            drawn = ax.collections[0].get_colors()
            expected = cmap(norm(np.sort(peaks[sc].to_numpy())))
            np.testing.assert_allclose(drawn,expected)
        import matplotlib.pyplot as plt
        plt.close("all")


if __name__ == "__main__":
    unittest.main()
