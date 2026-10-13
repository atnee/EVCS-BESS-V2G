import unittest
import pandas as pd
from integration.zoning import classify_customers, zone_cells, service_transformers, FT_TO_M

SETTINGS = dict(residential_max_kva=25,industrial_min_kva_per_phase=100,cell_m=1000,commercial_share=.4,
                industrial_radius_m=1000)
KM = 1000/FT_TO_M  # one km in feet (IEEE coordinates)


class TestZoning(unittest.TestCase):
    def test_customer_class_from_service_transformer(self):
        c = pd.DataFrame(dict(bus=["r","c","big","p","p","p"],phases=["A","B","C","A","B","C"],
                              kva=[25,37.5,250,250,250,250],p_kw=[5,15,90,90,90,90]))
        classes = classify_customers(c,SETTINGS).customer_class.tolist()
        # A single-phase 250 kVA customer is large commercial; three phases of >= 100 kVA on one bus are industrial.
        self.assertEqual(classes,["residential","commercial","commercial","industrial","industrial","industrial"])

    def test_regions(self):
        c = pd.DataFrame(dict(x=[.5*KM,.6*KM,1.5*KM,1.6*KM,5.5*KM],y=[.5*KM]*5,p_kw=[10,10,10,30,50],
                              customer_class=["residential","residential","residential","commercial","industrial"]))
        cells = zone_cells(c,SETTINGS,(0.,0.)).set_index(["cx","cy"]).zone
        self.assertEqual(cells[(0,0)],"residential")   # no commercial load
        self.assertEqual(cells[(1,0)],"commercial")    # 75 % commercial
        self.assertEqual(cells[(5,0)],"industrial")    # holds the industrial customer

    def test_every_ieee8500_load_has_a_service_transformer(self):
        kva = service_transformers("ieee8500")
        self.assertEqual(len(kva),1177)
        self.assertTrue(set(kva.values()) <= {5.,10.,15.,25.,37.5,50.,75.,100.,250.})
