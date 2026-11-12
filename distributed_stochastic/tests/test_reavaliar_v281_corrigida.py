"""Testes offline da dupla referencia; nenhuma simulacao OpenDSS e iniciada."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from distributed_stochastic import otimizar_conjunto_v281 as v281
from distributed_stochastic import reavaliar_v281_corrigida as audit


def args():
    return SimpleNamespace(historical_voltage_budget_puh=.15,voltage_budget_puh=.03,
                           min_actual_thermal_gain_pph=.05)


class DualBudgetTests(unittest.TestCase):
    def test_distinct_budgets_sent_to_correct_gates(self):
        a=args();ctx={'start_metrics':{'orig':{},'policy':{}}}
        current={'metrics':{},'ac':'AC_V27'}
        observed={}
        def historic(ctx,ac,base,metrics,config,before):
            observed['historical']=config.voltage_budget_puh
            self.assertIs(config is a,False)
            return True,[],{'passed_v27_gate':True}
        def cumulative(ctx,ac,metrics,base,config):
            observed['incremental']=config.voltage_budget_puh
            self.assertIs(config,a)
            return [],{}
        with patch.object(v281.v28,'gate_v28',side_effect=historic),\
             patch.object(v281,'cumulative_gate',side_effect=cumulative):
            ok,reasons,detail=v281.dual_voltage_budget_gate(ctx,'AC',{},current,a)
        self.assertTrue(ok)
        self.assertEqual(reasons,[])
        self.assertEqual(observed,{'historical':.15,'incremental':.03})
        self.assertEqual(detail['historical_reference'],'V2.5')
        self.assertEqual(detail['incremental_reference'],'V2.7')
        self.assertEqual(a.voltage_budget_puh,.03)

    def test_historical_rejection_is_not_relaxed(self):
        with patch.object(v281.v28,'gate_v28',return_value=(False,['RETAINED_LINE_GUARDS_AC'],{})),\
             patch.object(v281,'cumulative_gate',return_value=([],{})):
            ok,reasons,_=v281.dual_voltage_budget_gate({},'AC',{}, {'metrics':{},'ac':None},args())
        self.assertFalse(ok)
        self.assertIn('RETAINED_LINE_GUARDS_AC',reasons)

    def test_incremental_rejection_is_not_relaxed(self):
        with patch.object(v281.v28,'gate_v28',return_value=(True,[],{})),\
             patch.object(v281,'cumulative_gate',return_value=(['CUMULATIVE_VOLTAGE_BUDGET_POLICY'],{})):
            ok,reasons,_=v281.dual_voltage_budget_gate({},'AC',{}, {'metrics':{},'ac':None},args())
        self.assertFalse(ok)
        self.assertIn('CUMULATIVE_VOLTAGE_BUDGET_POLICY',reasons)

    def test_budget_inversion_fails_closed(self):
        a=args();a.historical_voltage_budget_puh=.01
        with self.assertRaises(ValueError):
            v281.dual_voltage_budget_gate({},'AC',{}, {},a)

    def test_output_cannot_touch_source(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            prior=root/'prior';prior.mkdir()
            with self.assertRaises(ValueError):
                audit.require_distinct_output(prior/'new',[prior])
            with self.assertRaises(ValueError):
                audit.require_distinct_output(root,[prior])
            audit.require_distinct_output(root/'new',[prior])

    def test_replay_manifest_cannot_change_original_params(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            manifest={"task":"run","repo":"ORIG","baseline":"ORIG", "v22_dir":"ORIG", "v23_dir":"ORIG",
                "v24_dir":"ORIG", "v25_dir":"ORIG", "v26_dir":"ORIG", "v27_dir":"ORIG",
                "v28_dir":"ORIG","output":"ORIG","voltage_budget_puh":.03,
                "min_actual_thermal_gain_pph":.05,"max_loading_growth_pp":.12}
            (root/'run_manifest_v281.json').write_text(json.dumps(manifest),encoding='utf-8')
            cli=argparse.Namespace(**{k:'NEW' for k in ('repo','baseline','v22_dir','v23_dir',
                'v24_dir','v25_dir','v26_dir','v27_dir','v28_dir','output')})
            cli.historical_voltage_budget_puh=.15;cli.incremental_voltage_budget_puh=.03
            original_args,saved=audit.parse_saved_manifest(root,cli)
            self.assertEqual(original_args.min_actual_thermal_gain_pph,.05)
            self.assertEqual(original_args.max_loading_growth_pp,.12)
            self.assertEqual(original_args.voltage_budget_puh,.03)
            self.assertEqual(original_args.historical_voltage_budget_puh,.15)
            self.assertEqual(saved['repo'],'ORIG')


if __name__=='__main__':
    unittest.main()
