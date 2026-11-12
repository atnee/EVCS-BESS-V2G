"""Fig. 7 (integration.regulator_figure): common voltage scale, minima and tap operations."""
import unittest
import matplotlib
matplotlib.use("Agg")
import networkx as nx
import numpy as np
import pandas as pd
from integration.regulator_figure import (BOUNDS, LIMIT, MAPS, bus_minimum, colour_scale, hourly_minimum,
                                          regulator_figures, tap_operations)


def voltages(values):
    """values[t][bus] -> long table with two phases, the second 0.01 pu higher."""
    rows = [dict(time=t,bus=b,phase=ph,v_pu=v+d) for t,step in enumerate(values) for b,v in step.items()
            for ph,d in (("A",0.),("B",.01))]
    return pd.DataFrame(rows)


class TestRegulatorFigure(unittest.TestCase):
    def test_minima_use_the_lowest_phase(self):
        v = voltages([{"b1": .97,"b2": 1.},{"b1": .94,"b2": .99}])
        self.assertEqual(bus_minimum(v).to_dict(),{"b1": .94,"b2": .99})
        np.testing.assert_allclose(hourly_minimum(v),[.97,.94])

    def test_tap_operations_count_steps(self):
        s = pd.DataFrame({"tap_vreg2": [4,4,6,5],"tap_feeder_reg": [1,1,1,1]})
        self.assertEqual(tap_operations(s),{"vreg2": 3,"feeder_reg": 0})

    def test_one_scale_with_the_limit_as_band_edge(self):
        cmap, norm = colour_scale()
        self.assertIn(LIMIT,BOUNDS)
        self.assertNotEqual(cmap(norm(LIMIT-1e-4)),cmap(norm(LIMIT+1e-4)))
        self.assertEqual(cmap(norm(.80)),cmap(norm(.92)))  # open low band

    def test_figure_builds_four_maps_on_the_shared_scale(self):
        g = nx.Graph()
        for i in range(3):
            g.add_node(f"b{i}",xy=(float(i),0.))
        g.add_edge("b0","b1",kind="line",id="l0"); g.add_edge("b1","b2",kind="regulator",id="vreg2",lv_bus="b2")
        g.add_node("b3",xy=(3.,0.)); g.add_edge("b2","b3",kind="line",id="l1")
        steps = [{"b0": 1.,"b1": .96,"b2": .99,"b3": .94}]*2
        volt = {(sc,view): voltages(steps) for sc in ("S0","S1","S4") for view in ("acting","frozen")
                if not (sc == "S0" and view == "frozen")}
        taps = pd.DataFrame({f"tap_{r}": [1,2] for r in ("feeder_reg","vreg2","vreg3","vreg4")})
        fig = regulator_figures(volt,{sc: taps for sc in ("S0","S1","S4")},g,1.)["fig7_regulators_voltage"]
        cmap, norm = colour_scale()
        titles = [f"{sc}, {'regulators acting' if view == 'acting' else 'taps frozen at S0'}" for sc,view in MAPS]
        maps = [ax for ax in fig.axes if any(t in ax.get_title(loc="left") for t in titles)]
        self.assertEqual(len(maps),4)
        for ax in maps:
            drawn = ax.collections[0].get_colors()
            np.testing.assert_allclose(drawn,cmap(norm(np.sort([.96,.94])[::-1])))
        import matplotlib.pyplot as plt
        plt.close("all")


if __name__ == "__main__":
    unittest.main()
