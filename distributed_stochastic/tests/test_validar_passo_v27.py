"""Testes V2.7 offline; nao exigem OpenDSS."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]/'validar_passo_v27.py'
sp=importlib.util.spec_from_file_location('v27_local',ROOT)
v27=importlib.util.module_from_spec(sp)
sp.loader.exec_module(v27)


def q(**overrides):
    result=dict(new_overloaded_lines_vs_s0=0,thermal_aggravation_pp_h=126.4,
                voltage_aggravation_pu_h=1.1,strict_voltage_samples=564,
                strict_overload_samples=44,vmin_pu=.93815,max_loading_pct=110.858,
                source_peak_kw=12409.)
    result.update(overrides)
    return result


class V27Tests(unittest.TestCase):
    def test_parse_alphas(self):
        self.assertEqual(v27.parse_alphas('.125,0.25,0.125'),[.125,.25])
        for bad in ('0','-0.3','nan','1.2',''):
            with self.assertRaises(ValueError):v27.parse_alphas(bad)

    def test_blend(self):
        b={'b1':np.zeros(24)};t={'b1':np.ones(24)}
        cars=np.zeros((150,24));cand=np.ones((150,24))
        rb,rc=v27.blend_schedules(b,cars,t,cand,.25)
        self.assertTrue(np.allclose(rb['b1'],.25))
        self.assertTrue(np.allclose(rc,.25))
        with self.assertRaises(ValueError):v27.blend_schedules(b,cars,{'b2':np.zeros(24)},cand,.25)

    def test_repair_energy_sign_change(self):
        base=np.zeros(24);base[1]=-20.;base[3]=18.05 # nao terminal exato; reparo deve corrigir
        b,meta=v27.repair_bess_soc({'site1':base},
              {'site1':{'power_kw':50.,'energy_kwh':500.}},max_correction_kw=15)
        self.assertLessEqual(meta['site1']['max_adjustment_kw'],15+1e-6)
        p=b['site1'];E=.3*500
        for hour, x in enumerate(p):
            E+=.95*max(-x,0)-max(x,0)/.95
            self.assertGreaterEqual(E,.1*500-.001)
            self.assertLessEqual(E,.9*500+.001)
            if 17<=hour<20:self.assertGreaterEqual(x,-1e-6)
        self.assertAlmostEqual(E,.3*500,places=4)

    def test_guard_detects_overflow(self):
        ref=pd.DataFrame([dict(hour=18,line='a',phase='A',target_pct=99.9)])
        b=pd.DataFrame([dict(hour=18,line='a',phase='A',loading_pct=100.14)])
        result=v27.guard_values(ref,b)
        self.assertGreater(float(result.exceed_target_pp.iloc[0]),0)
        with self.assertRaises(ValueError):v27.guard_values(ref,b.iloc[:0])

    def test_acceptance_all_guards(self):
        opts=SimpleNamespace(min_thermal_gain=.01,min_focus_gain_pph=.5,
            voltage_budget_puh=.15,max_new_voltage_samples=20,
            max_new_thermal_samples=4,vmin_drop_pu=.002,
            max_loading_growth_pp=.15,peak_allowance_kw=120.)
        g=pd.DataFrame({'exceed_target_pp':[-.2],'loading_pct':[99.7]})
        ops=dict(tap_absolute_steps_total=140,capacitor_switch_events=14)
        ref=q(); cand=q(thermal_aggravation_pp_h=120.,voltage_aggravation_pu_h=1.15)
        ok,why=v27.accept(ref,ref,cand,cand,73.,68.,g,ops,ops,opts)
        self.assertTrue(ok,str(why))
        cand['new_overloaded_lines_vs_s0']=2
        ok,why=v27.accept(ref,ref,cand,cand,73.,68.,g,ops,ops,opts)
        self.assertFalse(ok)
        self.assertIn('NEW_LINES_VS_ORIGINAL_S0',why)
        cand['new_overloaded_lines_vs_s0']=0
        cand['voltage_aggravation_pu_h']=2.18
        ok,why=v27.accept(ref,ref,cand,cand,73.,68.,g,ops,ops,opts)
        self.assertFalse(ok)
        self.assertIn('VOLTAGE_BUDGET_EXCEEDED',why)

    def test_no_overwrite_prior(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);src=root/'prev';src.mkdir()
            with self.assertRaises(ValueError):v27.no_overwrite(src,[src])
            v27.no_overwrite(root/'results'/'v27',[src])


if __name__=='__main__':unittest.main()
