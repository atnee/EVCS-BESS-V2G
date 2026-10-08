import unittest
from pathlib import Path
import pandas as pd
from core.schemas import TimeGrid
from network.xlsx_loader import SHEETS,from_tables,load_xlsx,load_profile

class TestXLSX(unittest.TestCase):
    def tables(self):
        t={k:pd.DataFrame(columns=v) for k,v in SHEETS.items()}
        t["buses"]=pd.DataFrame([["001","ABC",2400,None,None],["002","A",2400,None,None]],columns=SHEETS["buses"])
        t["lines"]=pd.DataFrame([["l","001","002","A",.2,.1,100]],columns=SHEETS["lines"])
        t["substations"]=pd.DataFrame([["s","001",2400]],columns=SHEETS["substations"])
        return t
    def test_normalized_network(self):
        n=from_tables(self.tables());self.assertEqual(n.buses[0].id,"001");self.assertFalse(n.synthetic)
    def test_unknown_bus(self):
        t=self.tables();t["lines"].loc[0,"to_bus"]="missing"
        with self.assertRaises(ValueError): from_tables(t)
    def test_missing_column(self):
        t=self.tables();t["lines"]=t["lines"].drop(columns="r_ohm")
        with self.assertRaises(ValueError): from_tables(t)
    def test_null_required(self):
        t=self.tables();t["lines"].loc[0,"r_ohm"]=None
        with self.assertRaises(ValueError): from_tables(t)
    def test_load_sign_and_timezone(self):
        t=self.tables();g=TimeGrid(steps=1)
        t["load_profiles"]=pd.DataFrame([[g.start,"002","A",5,2]],columns=SHEETS["load_profiles"])
        p=load_profile(from_tables(t),g)
        self.assertEqual(p.total_injection()[0],-5)
    def test_blank_template_rejected(self):
        p=Path(__file__).resolve().parents[1]/"data/honduras/templates/honduras_template.xlsx"
        with self.assertRaisesRegex(ValueError,"Buses"):
            load_xlsx(p)
