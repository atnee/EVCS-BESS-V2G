"""Testes offline V2.8 (OpenDSS real só no computador do projeto)."""
from __future__ import annotations
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from distributed_stochastic import otimizar_conjunto_v28 as v28


def opts(**updates):
    d=dict(trust_radius=.25,voltage_budget_puh=.03,thermal_orig_budget_pph=.02,
           peak_allowance_kw=60,vmin_drop_pu=.0007,max_loading_growth_pp=.12,
           predicted_thermal_gain_pph=.02,predicted_focus_gain_pph=.005,
           local_loading_tolerance_pp=.05,local_voltage_tolerance_pu=.0007,
           local_peak_growth_kw=50,hour20_improvement_pp=.0,
           hour15_max_worsening_pp=.1,hour20_max_worsening_pp=.02,
           hour18_vmin_drop_pu=.0003,hour18_under_budget_puh=.015)
    d.update(updates)
    return SimpleNamespace(**d)


def fake_ac(line=99.8,phase_a_voltage=.938,source_p=10000):
    lines=pd.DataFrame({'hour':[15,18,20,20,20],
        'line':['ln6351510-1','ln5860423-3','ln6350556-2','ln6350556-3','ln5835160-1'],
        'phase':['C','A','A','A','A'],
        'loading_pct':[line,99.3,100.02,100.02,100.02]})
    volt=pd.DataFrame({'hour':[18,18],'bus':['A','B'],'phase':['A','A'],
        'v_pu':[phase_a_voltage,.96]})
    source=pd.DataFrame({'hour':[15,18,20],'p_kw':[source_p]*3})
    return dict(branches=lines,voltages=volt,source=source)

class V28Tests(unittest.TestCase):
    def test_align_keys_reorder(self):
        a=fake_ac();b=fake_ac()
        b['branches']=b['branches'].iloc[::-1].reset_index(drop=True)
        np.testing.assert_allclose(v28.align_values(a['branches'],b['branches'],v28.KEY_LINE,'loading_pct'),
                                   a['branches']['loading_pct'].to_numpy())
        b['branches'].loc[0,'line']='missing'
        with self.assertRaises(ValueError):v28.align_values(a['branches'],b['branches'],v28.KEY_LINE,'loading_pct')

    def test_derive_slope_and_shape(self):
        a=fake_ac();b=fake_ac(line=99.9,phase_a_voltage=.94,source_p=10100)
        s=v28.derive_slope(a,b,.1)
        self.assertAlmostEqual(s['line'][0],1)
        self.assertAlmostEqual(s['volt'][0],.02)
        self.assertAlmostEqual(s['source'][0],1000)

    def test_schedule_requires_convex_weights(self):
        base={'cars':np.zeros((150,24)),'bess':{'b':np.zeros(24)}}
        target={'cars':np.ones((150,24)),'bess':{'b':np.zeros(24)}}
        cap={'b':{'power_kw':20,'energy_kwh':100}}
        b,c,meta=v28.make_schedule(base,[target],np.array([.25]),cap,15)
        np.testing.assert_allclose(c,.25)
        self.assertAlmostEqual(float(b['b'].sum()),0)
        with self.assertRaises(ValueError):v28.make_schedule(base,[target],np.array([1.2]),cap,15)

    def test_lp_restricts_voltage_and_thermal(self):
        a=fake_ac(line=99.8)
        s_good={'line':np.array([-2.,0.,0.,0.,0.]),'volt':np.zeros(2),'source':np.zeros(3)}
        # Direcao 2 melhora termica mas derruba voltage da fase A em 18h.
        s_bad={'line':np.array([-4.,0.,0.,0.,0.]),'volt':np.array([-.02,0.]),'source':np.zeros(3)}
        grads=[{'thermal':-10,'voltage':0,'volt_orig':0,'thermal_orig':-10,'focus':-5,
                'peak':0,'vmin':0,'maxload':-2},
               {'thermal':-20,'voltage':5,'volt_orig':5,'thermal_orig':-20,'focus':-10,
                'peak':0,'vmin':-.02,'maxload':-4}]
        met={'policy':{'thermal_aggravation_pp_h':120,'voltage_aggravation_pu_h':1.2},
             'orig':{'source_peak_kw':12000},'focus':65}
        guards=pd.DataFrame([{'hour':18,'line':'ln5860423-3','phase':'A','target_pct':99.9}])
        c,A,b,labels,summ=v28.build_lp(a,grads,[s_good,s_bad],guards,opts(),met)
        w,report=v28.solve_lp(c,A,b,.25)
        self.assertIsNotNone(w, report)
        self.assertLessEqual(w.sum(),.2500001)
        self.assertTrue(np.all(A@w<=b+1e-6))
        self.assertLess(w[1],.02)  # restricao local da subtensao corta direcao ruim

    def test_lp_fails_safe(self):
        w,msg=v28.solve_lp(np.array([-1.]),np.array([[1.],[-1.]]),np.array([0,-1.]),.5)
        self.assertIsNone(w)
        self.assertEqual(msg['state'],'LP_INFEASIBLE_OR_FAILED')

    def test_forecast_fallback_keeps_ac_gates(self):
        c=np.array([-2.])
        A=np.array([[1.],[-1.],[1.]])
        b=np.array([.2,-.02,-.01])
        # H20 exige w<=-0.01 e conflita com os pesos não-negativos.
        tags=['TRUST_RADIUS','MIN_THERMAL_GAIN','H20:test']
        w,meta=v28.solve_lp_hierarchical(c,A,b,tags,.2)
        self.assertIsNotNone(w,meta)
        self.assertEqual(meta['level'],1)
        self.assertEqual(meta['state'],'LP_FORECAST_CONSTRAINTS_RELAXED')

    def test_hour20_events(self):
        a=fake_ac()
        b=fake_ac();b['branches'].loc[2,'loading_pct']=100.1
        count,_=v28.hour20_events(b,a)
        self.assertEqual(count,0)  # ja existente no incumbent
        a['branches'].loc[2:4,'loading_pct']=99.9
        count,_=v28.hour20_events(b,a)
        self.assertEqual(count,3)

    def test_critical_hour_audit(self):
        a=fake_ac(line=109.,phase_a_voltage=.938)
        b=fake_ac(line=109.2,phase_a_voltage=.937)
        reasons,report=v28.critical_hour_audit(a,b,opts(hour18_under_budget_puh=.0001))
        self.assertIn('CRITICAL_15H_OVERLOAD_WORSENED',reasons)
        self.assertIn('H18_PHASE_A_MIN_VOLTAGE_WORSENED',reasons)
        self.assertIn('H18_PHASE_A_UNDERVOLTAGE_AGGRAVATION',reasons)
        self.assertIn('voltage_min_18h_A_before_pu',report)

if __name__=='__main__':unittest.main()
