import unittest
from pathlib import Path
import numpy as np
from tempfile import TemporaryDirectory
from unittest.mock import patch
from core.artifacts import read_profile_bundle
from core.schemas import TimeGrid,make_profile
from integration.scenarios import synthetic_network
from integration.coordinator import combine,run_scenarios,export_results

class TestIntegration(unittest.TestCase):
    def test_duplicate_ids(self):
        g=TimeGrid(steps=1);n=synthetic_network()
        a=make_profile(g,"a","n1","A",[-1],vehicle_ids=["v"])
        b=make_profile(g,"b","n2","A",[1],vehicle_ids=["v"])
        with self.assertRaises(ValueError): combine([a,b],n)
    def test_horizon_mismatch(self):
        with self.assertRaises(ValueError): combine([make_profile(TimeGrid(steps=1),"a","n1","A",[-1]),make_profile(TimeGrid(steps=2),"b","n1","A",[-1,-1])],synthetic_network())
    def test_all_scenarios_and_end_states(self):
        root=Path(__file__).resolve().parents[1]
        summary,details=run_scenarios(root/"configs")
        self.assertEqual(list(summary.scenario),["S0","S1","S2","S3","S4"])
        self.assertFalse(summary.synthetic.any())
        self.assertTrue(summary.network.str.contains("IEEE8500").all())
        self.assertTrue(np.isfinite(summary.select_dtypes('number')).all().all())
        for d in details.values():
            f=d["flow"]
            balance=f["source"].p_kw.to_numpy()+d["profile"].total_injection()-f["native_loads"].groupby("time").p_kw.sum().to_numpy()-f["branches"].groupby("time").loss_kw.sum().to_numpy()
            np.testing.assert_allclose(balance,0,atol=.001)
        for name in ("S2","S4"):
            soc=details[name]["modules"]["bess"][0].metadata["soc"]
            self.assertAlmostEqual(soc[0],soc[-1])
        for name in ("S1","S2","S3","S4"):
            end=details[name]["modules"]["v2g"][0].metadata["departure_energy_kwh"]
            self.assertEqual(set(end),{"fleet-0","fleet-1","fleet-2"})
            for key in end:
                self.assertAlmostEqual(end[key],details["S1"]["modules"]["v2g"][0].metadata["departure_energy_kwh"][key])
        # Export already-computed flows once; verify module attribution can be
        # recombined exactly without rerunning their implementations.
        with TemporaryDirectory() as tmp, patch("integration.coordinator.run_scenarios",return_value=(summary,details)):
            export_results(tmp,root/"configs")
            for scenario,d in details.items():
                profiles=[read_profile_bundle(Path(tmp)/"modules"/m/scenario,d["network"])
                          for m in ("evcs","bess","v2g")]
                reconstructed=combine(profiles,d["network"])
                np.testing.assert_allclose(reconstructed.total_injection(),d["profile"].total_injection(),atol=1e-10)
                keys=["bus","phase","time"]
                expected=d["profile"].data.set_index(keys)[["p_kw","q_kvar"]]
                actual=reconstructed.data.set_index(keys)[["p_kw","q_kvar"]]
                a,b=actual.align(expected,fill_value=0)
                np.testing.assert_allclose(a,b,atol=1e-10)
