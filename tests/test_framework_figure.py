"""Fig. 1 (integration.framework_figure): the numbers on the schematic come from the configs."""
from pathlib import Path
import unittest
import matplotlib
matplotlib.use("Agg")
import yaml
from integration.framework_figure import framework_facts, framework_figures

ROOT = Path(__file__).resolve().parents[1]


class TestFrameworkFigure(unittest.TestCase):
    def test_facts_follow_the_configs_and_figure_is_built(self):
        f = framework_facts(ROOT/"configs")
        e = yaml.safe_load((ROOT/"configs/evcs.yaml").read_text(encoding="utf-8"))
        self.assertEqual(f["plan_steps"]*f["plan_min"],24*60)               # lane A covers one day
        self.assertAlmostEqual(f["int_steps"]*f["int_dt_h"],24.)            # lane B covers one day
        self.assertEqual(f["sites"],len(e["integration"]["sites"]))
        self.assertEqual(f["public_evs"]+f["v2g_cars"],f["fleet"])           # V2G cars come out of the fleet
        self.assertAlmostEqual(sum(f["shares"].values()),1.)
        import matplotlib.pyplot as plt
        figs = framework_figures(f)
        self.assertEqual(list(figs),["fig1_framework"])
        plt.close("all")


if __name__ == "__main__":
    unittest.main()
