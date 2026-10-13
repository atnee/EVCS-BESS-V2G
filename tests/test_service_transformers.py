"""Service transformer loading (integration.service_transformers): station sharing rule and analytic loading."""
import unittest
import numpy as np
import pandas as pd
from integration.service_transformers import shared_stations, transformer_demand, transformer_summary, select


class TestServiceTransformers(unittest.TestCase):
    def setUp(self):
        # 10 kVA with 8 kW + 6 kvar at the peak: exactly 100 % of the rating.
        self.customers = pd.DataFrame({"id":["t1","t2","t3"],"bus":["b1","b2","b3"],"phases":["A","B","C"],
                                       "kva":[10.,25.,50.],"p_kw":[8.,5.,2.],"q_kvar":[6.,0.,0.]})

    def test_only_single_phase_stations_on_the_customer_phase_share(self):
        sites = {"b1":"ac1","b2":"ac1","b3":"dc","b9":"ac1"}
        phases = {"b1":"A","b2":"A","b9":"B"}  # b2 on another phase; b3 three-phase hub; b9 has no customer
        self.assertEqual(shared_stations(self.customers,sites,phases),{"t1":"b1"})
        self.assertEqual(shared_stations(self.customers,sites,phases,share=False),{})

    def test_loading_is_apparent_power_over_rating(self):
        kw,pct = transformer_demand(self.customers,[.5,1.])
        np.testing.assert_allclose(pct.t1,[50.,100.])
        _,pct = transformer_demand(self.customers,[1.],{"b1":[4.]},{"t1":"b1"})
        self.assertAlmostEqual(pct.t1.iloc[0],100*np.hypot(12.,6.)/10)  # station at unity power factor

    def test_summary_energy_and_selection(self):
        _,pct0 = transformer_demand(self.customers,[1.,1.])
        kw1,pct1 = transformer_demand(self.customers,[1.,1.],{"b1":[4.,0.]},{"t1":"b1"})
        t = transformer_summary(self.customers,{"t1":"b1"},pct0,pct1,kw1,dt_h=.25)
        self.assertEqual(t.id.tolist(),["t1","t2","t3"])
        self.assertAlmostEqual(t.set_index("id").energy_s1_kwh["t1"],(12.+8.)*.25)
        self.assertAlmostEqual(t.set_index("id").hours_over_100_s1["t1"],.25)
        top,bottom = select(t,1,2)
        self.assertEqual((top.id.tolist(),bottom.id.tolist()),(["t1"],["t3","t2"]))
