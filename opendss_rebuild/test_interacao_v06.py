"""Teste sem OpenDSS da previsão linear conjunta e da verificação de bases."""
import unittest
import pandas as pd
from calibrar_interacao_bess_v2g import superposition_table

class TestInteracao(unittest.TestCase):
    def test_superposition_exact_for_synthetic_linear_system(self):
        baseline=[{'hour':18,'line':'l1','phase':'A','loading_pct':110.0}]
        joint=[{'hour':18,'line':'l1','phase':'A','loading_pct':104.0}]
        b=pd.DataFrame([{'hour':18,'line':'l1','phase':'A','loading_pct_before':110.,'d_loading_pct_per_kw':-0.02}])
        v=pd.DataFrame([{'hour':18,'line':'l1','phase':'A','loading_pct_before':110.,'d_loading_pct_per_kw':-0.01}])
        out=superposition_table(baseline,joint,b,v,18,['hour','line','phase'],'loading_pct','d_loading_pct_per_kw',200.,200.)
        self.assertAlmostEqual(out.iloc[0].pred_joint,104.)
        self.assertAlmostEqual(out.iloc[0].abs_residual_joint,0.)

    def test_reject_mismatched_baseline(self):
        baseline=[{'hour':18,'bus':'a','phase':'A','v_pu':1.0}]
        joint=[{'hour':18,'bus':'a','phase':'A','v_pu':1.01}]
        b=pd.DataFrame([{'hour':18,'bus':'a','phase':'A','v_pu_before':0.97,'d_v_pu_per_kw':0.00002}])
        v=pd.DataFrame([{'hour':18,'bus':'a','phase':'A','v_pu_before':1.,'d_v_pu_per_kw':0.00003}])
        with self.assertRaisesRegex(ValueError,'Bases'):
            superposition_table(baseline,joint,b,v,18,['hour','bus','phase'],'v_pu','d_v_pu_per_kw',200.,200.)

if __name__=='__main__': unittest.main()
