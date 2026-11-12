import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from distributed_stochastic.otimizar_siting_v21 import (build_injections,build_and_solve,read_probes,forecast_constant)

class TestV21(unittest.TestCase):
    def test_double_count_relocation(self):
        rows=[];old=[]
        for h in range(24):
            for ph in 'ABC':
                rows.append(dict(hour=h,bus='old',phase=ph,p_kw=-1.,q_kvar=0.))
                old.append(dict(hour=h,bus='old',phase=ph,p_kw=-1.,q_kvar=0.))
        s1=pd.DataFrame(rows); orig=pd.DataFrame(old)
        power=np.full((2,24),-1.5)
        new=build_injections(s1,orig,{'bess':np.zeros(24)},power,['a','b'])
        self.assertAlmostEqual(new.p_kw.sum(),-72.)
        self.assertAlmostEqual(new[new.bus=='old'].p_kw.sum(),0.)
        self.assertAlmostEqual(new[new.bus=='a'].p_kw.sum(),-36.)
        self.assertAlmostEqual(new[new.bus=='b'].p_kw.sum(),-36.)
    def test_constant_removal(self):
        self.assertAlmostEqual(forecast_constant({('host',1):-0.01},17,np.array([0.]*17+[-80]+[0.]*6),'host'),-0.8)
    def test_actual_probe_coverage(self):
        p=Path('/mnt/data/_v21_uploaded/v21_sondagem_24h')
        if p.exists():
            raw,u=read_probes(p)
            self.assertEqual(len(raw),624)
            self.assertEqual(len(u),576)
    def test_synthetic_siting(self):
        # 24-hour network reference with one critical line and two buses.
        ids=['a','b'];host='b'
        car=np.zeros((1,24));car[0,18]=-11.;car[0,17]=8.
        old=np.zeros(24);old[18]=-11.
        s1l=pd.DataFrame([{'hour':18,'line':'A','phase':'A',
          'loading_pct_s1':110.,'loading_pct_s0':100.}]).set_index(['hour','line','phase'])
        s1v=pd.DataFrame([{'hour':18,'bus':'LV','phase':'A',
          'v_pu_s1':.948,'v_pu_s0':.95}]).set_index(['hour','bus','phase'])
        slope={('a',1):-.01,('a',-1):-.01,('b',1):-.015,('b',-1):-.015}
        sl={ (18,'A','A'):slope }
        sv={ (18,'LV','A'):{('a',1):1e-5,('a',-1):1e-5,('b',1):2e-5,('b',-1):2e-5}}
        sp={h:{(s,k):(-1 if k==1 else -1) for s in ids for k in [1,-1]} for h in range(24)}
        source=pd.DataFrame({'hour':range(24),'p_kw':[7000.]*24})
        r=build_and_solve(bsites=ids,vsites=ids,s1l=s1l,s0l=None,
                    s1v=s1v,s0v=None,source=source,
                    line_slope=sl,volt_slope=sv,source_slope=sp,
                    car_power=car,oldfleet=old,host=host,
                    n_bess=2,n_v2g=2,solver_seconds=15,mip_gap=.15)
        self.assertAlmostEqual(sum(z['power_kw'] for z in r['allocation_bess'].values()),2200,places=2)
        self.assertAlmostEqual(sum(z['energy_kwh'] for z in r['allocation_bess'].values()),7200,places=2)
        self.assertEqual(len(r['vehicle_sites']),1)
        for site,soc in r['soc'].items():
            self.assertTrue(np.all(soc>=.1-1e-6))
            self.assertTrue(np.all(soc<=.9+1e-6))
            self.assertAlmostEqual(soc[-1],.30,places=4)

if __name__=='__main__':unittest.main()
