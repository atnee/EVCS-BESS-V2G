"""Testes offline V2.8.1; nao substituem replay OpenDSS na maquina real."""
from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd
from distributed_stochastic import otimizar_conjunto_v281 as v281


class V281Tests(unittest.TestCase):
    def test_epsilon_objective_reverses_bad_weighting(self):
        # Ganho termico com custo de tensao positivo; a busca antiga bloqueou.
        grad=[{'thermal':-26.46,'focus':-63.57,'voltage':1.406},
              {'thermal':8.67,'focus':9.28,'voltage':-.085}]
        c=v281.epsilon_objective(grad, .02)
        self.assertLess(c[0],0)
        self.assertGreater(c[1],0)
        with self.assertRaises(ValueError):v281.epsilon_objective(grad,float('nan'))

    def test_lp_keeps_voltage_budget_and_removes_h20_forecast(self):
        cost=np.array([-5.,2.])
        # trust <= .2; candidate 1 improves thermal but worsens voltage.
        A=np.array([[1,1],[1,0],[-5,2],[1,0],[0,1]])
        b=np.array([.2,.1,-.05,-.001,.2])
        tags=['TRUST_RADIUS','VOLTAGE_SAME_POLICY_BUDGET','MIN_THERMAL_GAIN',
              'H20:forecast_only','OTHER_HARD']
        weights,r=v281.solve_epsilon_lp(cost,A,b,tags,.2)
        self.assertIsNotNone(weights,r)
        self.assertEqual(r['selected_level'],1)
        self.assertLessEqual(weights[0],.1000001)
        self.assertGreater(weights[0],.009)

    def test_lp_never_relaxes_global_voltage(self):
        c=np.array([-5.]);A=np.array([[1.],[-5.],[1.]])
        b=np.array([0.,-.1,-.01]);tags=['VOLTAGE_SAME_POLICY_BUDGET','MIN_THERMAL_GAIN','H20:near']
        w,r=v281.solve_epsilon_lp(c,A,b,tags,.2)
        self.assertIsNone(w)
        self.assertEqual(r['state'],'NO_EPSILON_LP_PROPOSAL')

    def test_lp_rejects_no_predicted_gain(self):
        w,r=v281.solve_epsilon_lp(np.array([2.,3.]),
           np.array([[1.,1.]]),np.array([.2]),['TRUST_RADIUS'],.2)
        self.assertIsNone(w)
        self.assertEqual(r['state'],'NO_EPSILON_LP_PROPOSAL')

    def test_split_directions(self):
        old={'cars':np.zeros((150,24)),'bess':{'A':np.zeros(24)}}
        sof={'cars':np.ones((150,24)),'bess':{'A':np.ones(24)},'path':'V26'}
        ctx={'directions':{'v26_soft':sof},'inputs':{'soft':sof}}
        d=v281.split_directions(ctx,old,'soft')
        self.assertEqual(set(d),{'v26_soft','split_soft_bess','split_soft_v2g'})
        np.testing.assert_allclose(d['split_soft_bess']['cars'],0.)
        np.testing.assert_allclose(d['split_soft_v2g']['bess']['A'],0.)
        self.assertEqual(set(v281.split_directions(ctx,old,'none')),{'v26_soft'})

    def test_split_drops_null_direction(self):
        old={'cars':np.zeros((150,24)),'bess':{'A':np.zeros(24)}}
        sof={'cars':np.zeros((150,24)),'bess':{'A':np.ones(24)},'path':'V26'}
        ctx={'directions':{},'inputs':{'soft':sof}}
        d=v281.split_directions(ctx,old,'soft')
        self.assertEqual(set(d),{'split_soft_bess'})

    def test_cumulative_voltage_budget_not_reset_each_iteration(self):
        original={'orig':{'voltage_aggravation_pu_h':1.,'source_peak_kw':10000,
                    'vmin_pu':.94,'max_loading_pct':110,'strict_voltage_samples':500,
                    'strict_overload_samples':40},
                  'policy':{'voltage_aggravation_pu_h':1.18,'thermal_aggravation_pp_h':118.},
                  'focus':70.}
        partial={'orig':{'voltage_aggravation_pu_h':1.025,'source_peak_kw':10000,
                    'vmin_pu':.94,'max_loading_pct':110,'strict_voltage_samples':500,
                    'strict_overload_samples':40},
                  'policy':{'voltage_aggravation_pu_h':1.20,'thermal_aggravation_pp_h':117.9},
                  'focus':69.9}
        worse={'orig':{**partial['orig'],'voltage_aggravation_pu_h':1.045},
               'policy':{'voltage_aggravation_pu_h':1.22,'thermal_aggravation_pp_h':117.7},
               'focus':69.7}
        ctx={'start_metrics':original,'ac27':'BASE'}
        args=SimpleNamespace(voltage_budget_puh=.03,min_actual_thermal_gain_pph=.05,
                min_actual_focus_gain_pph=.02,peak_allowance_kw=60.,vmin_drop_pu=.0007,
                max_loading_growth_pp=.12,max_added_voltage_samples=5,
                max_added_overload_samples=0)
        # Fake audit requires no dataframes here; checks the independent cumulative logic.
        from unittest.mock import patch
        with patch.object(v281.v28,'critical_hour_audit',return_value=([],{})):
            problems,_=v281.cumulative_gate(ctx,'AFTER',worse,{'metrics':partial},args)
        self.assertIn('CUMULATIVE_VOLTAGE_BUDGET_ORIG',problems)
        self.assertIn('CUMULATIVE_VOLTAGE_BUDGET_POLICY',problems)
        self.assertNotIn('NO_INCREMENTAL_THERMAL_REDUCTION',problems)

    def test_invalid_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            v281.solve_epsilon_lp(np.array([-1.,-2.]),np.array([[1.,2.,3.]]),
                                   np.array([.1]),['some'],.2)


if __name__ == '__main__':
    unittest.main()
