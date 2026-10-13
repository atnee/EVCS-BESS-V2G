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
