"""Testes isolados para a extensão bidirecional da sondagem V2.0.1."""
import unittest
import numpy as np
import pandas as pd
from distributed_stochastic.ac_probe import (
    normalize_probe_powers, validate_source_response, _metric, _matching_effect
)

class SignedProbeTests(unittest.TestCase):
    def test_old_single_probe_api(self):
        self.assertEqual(normalize_probe_powers(200, None),(200.,))

    def test_plus_minus(self):
        self.assertEqual(normalize_probe_powers(200,[200,-200]),(200.,-200.))

    def test_invalid_powers(self):
        for powers in ([],[0],[2201],[-2201],[np.nan],[200,200]):
            with self.subTest(powers=powers),self.assertRaises(ValueError):
                normalize_probe_powers(200,powers)

    def test_injection_reduces_source(self):
        validate_source_response(200,-244.5)

    def test_absorption_increases_source(self):
        validate_source_response(-200,218.4)

    def test_wrong_source_direction(self):
        for pair in ((200,244.5),(-200,-218.4),(200,0.),(-200,0.)):
            with self.subTest(pair=pair),self.assertRaises(RuntimeError):
                validate_source_response(*pair)

    def test_metric_thresholds(self):
        br=pd.DataFrame({'loading_pct':[100.,105.,99.1]})
        vo=pd.DataFrame({'v_pu':[.95,.94,1.05,1.06]})
        m=_metric(br,vo)
        self.assertEqual(m['overload_samples'],1)
        self.assertEqual(m['undervoltage_samples'],1)
        self.assertEqual(m['overvoltage_samples'],1)
        self.assertAlmostEqual(m['thermal_severity_pp'],5.)

    def test_signed_derivative_from_perturbations(self):
        base=pd.DataFrame({'hour':[18],'line':['L1'],'phase':['A'],'loading_pct':[100.]})
        for step,result in [(200,98.),(-200,102.)]:
            trial=base.copy();trial['loading_pct']=result
            merged=_matching_effect(base,trial,['hour','line','phase'],'loading_pct')
            sens=(merged.loading_pct_probe-merged.loading_pct_base).iloc[0]/step
            self.assertAlmostEqual(sens,-.01)

if __name__=='__main__':unittest.main()
