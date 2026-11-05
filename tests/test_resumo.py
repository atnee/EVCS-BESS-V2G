"""One-page summary (integration.resumo): table verdicts and studies not run yet."""
import tempfile
import unittest
from pathlib import Path
from integration.resumo import loading_label, export_resumo


class TestResumo(unittest.TestCase):
    def test_loading_label_against_100_pct(self):
        self.assertEqual(loading_label(96.7),"abaixo do limite")
        self.assertEqual(loading_label(100.2),"no limite")
        self.assertEqual(loading_label(108.9),"**acima do limite**")

    def test_pending_studies_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = (export_resumo(Path(tmp)/"ieee8500")/"LEIA-ME.md").read_text(encoding="utf-8")
        self.assertIn("Estudo S1 ainda não rodado",text)
        self.assertIn("Integração S0–S4 (passo de 1 h)",text)
        self.assertIn("Ainda não rodada",text)


class TestSuccessCriterion(unittest.TestCase):
    def test_base_overloads_are_reported_apart(self):
        import pandas as pd
        from integration.resumo import success_criterion
        s0 = pd.Series({"a": 90.,"b": 108.,"c": 70.})
        peak, hours = pd.Series({"a": 103.,"b": 120.,"c": 80.}), pd.Series({"a": 2,"b": 5,"c": 0})
        r = success_criterion(s0,peak,hours,1.,.96,.94)
        self.assertEqual((r["new_overloads"],r["new_max_pct"],r["new_line_hours"]),(1,103.,2.))
        self.assertEqual((r["base_overloads"],r["base_max_pct"]),(1,120.))
        self.assertFalse(r["success"])
        ok = success_criterion(s0,pd.Series({"a": 99.,"b": 120.,"c": 80.}),hours,1.,.951,.93)
        self.assertTrue(ok["success"])  # only the S0 overload remains; regulated voltage >= 0.95
