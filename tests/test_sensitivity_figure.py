"""Fig. 8 (integration.sensitivity_figure): seed families, the thermal check against sensitivity.csv."""
import unittest
import matplotlib
matplotlib.use("Agg")
import pandas as pd
from integration.sensitivity_figure import case_table, family, sensitivity_figures


def table(tmp, rows):
    path = tmp/"sensitivity.csv"
    pd.DataFrame([dict(scenario="S4",status="ok",**r) for r in rows]).to_csv(path,index=False)
    return path


class TestSensitivityFigure(unittest.TestCase):
    def test_families_match_seeds_across_configurations(self):
        self.assertEqual(family("seed_03"),("seed",3))
        self.assertEqual(family("combo_seed_10"),("combo_seed",10))
        self.assertEqual(family("band1v_15min"),(None,None))
        self.assertEqual(family("other_seed_01"),(None,None))  # unknown family

    def test_counts_must_reproduce_sensitivity_csv(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = table(Path(d),[dict(case="base",new_overloads=0,v_min_pu=.951,success=True),
                                  dict(case="seed_01",new_overloads=2,v_min_pu=.947,success=False)])
            t = case_table(path,{"base": (98.8,0),"seed_01": (101.3,2)})
            self.assertEqual(t.set_index("case").line_max_pct.to_dict(),{"base": 98.8,"seed_01": 101.3})
            with self.assertRaisesRegex(ValueError,"do not reproduce"):
                case_table(path,{"base": (98.8,0),"seed_01": (101.3,1)})
            with self.assertRaisesRegex(ValueError,"No line peaks"):
                case_table(path,{"base": (98.8,0)})

    def test_figure_builds(self):
        rows = [dict(case="base",v_min_pu=.951,line_max_pct=98.8,success=True)]
        for k in range(1,4):
            for fam,dv in (("seed",0.),("bess050_seed",0.),("band1v_seed",.003),("combo_seed",.003)):
                rows.append(dict(case=f"{fam}_{k:02d}",v_min_pu=.947+dv,line_max_pct=99.+k/10,success=dv > 0))
        rows += [dict(case=c,v_min_pu=.946,line_max_pct=99.3,success=False)
                 for c in ("resolution_15min","resolution_15min_recal","band1v_15min","combo_15min")]
        rows.append(dict(case="conv_both",v_min_pu=.937,line_max_pct=117.,success=False))
        for r in rows:
            r.setdefault("new_overloads",0)
        figs = sensitivity_figures(pd.DataFrame(rows)); fig = figs["fig8_sensitivity_s4_seeds"]; self.assertIn("figS2_sensitivity_s4_one_at_a_time",figs)
        self.assertEqual(sum(1 for ax in fig.axes if ax.get_title(loc="left").startswith(("(a)","(b)"))),2)
        import matplotlib.pyplot as plt
        plt.close("all")
        # a case left out of the rows must equal the case drawn for it, and no case may be forgotten
        changed = [dict(r,v_min_pu=.9) if r["case"] == "band1v_seed_02" else r for r in rows]
        with self.assertRaisesRegex(ValueError,"differs from combo_seed_02"):
            sensitivity_figures(pd.DataFrame(changed))
        with self.assertRaisesRegex(ValueError,"neither drawn"):
            sensitivity_figures(pd.DataFrame(rows+[dict(case="new_case",v_min_pu=.95,line_max_pct=99.,
                                                         success=True,new_overloads=0)]))


if __name__ == "__main__":
    unittest.main()
