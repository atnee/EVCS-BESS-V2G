"""Sensitivity case configs (integration.sensitivity.case_configs): each kind of change on the real configs."""
from pathlib import Path
import unittest
import yaml
from integration.sensitivity import case_configs, set_key

ROOT = Path(__file__).resolve().parents[1]
BASE = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in (ROOT/"configs").glob("*.yaml") if p.name != "sensitivity.yaml"}


class TestCaseConfigs(unittest.TestCase):
    def test_base_is_not_changed_and_unknown_keys_fail(self):
        before = yaml.safe_dump(BASE)
        case_configs(BASE,{"seed": 3,"bess_scale": 2.,"resolution_min": 15})
        self.assertEqual(yaml.safe_dump(BASE),before)
        with self.assertRaisesRegex(ValueError,"Unknown key"):
            set_key({"a": {"b": 1}},"a.c",2)

    def test_seed_and_scales(self):
        c = case_configs(BASE,{"seed": 7,"load_scale": 1.1,"bess_scale": .5})
        self.assertEqual((c["evcs.yaml"]["planning"]["seed"],c["v2g.yaml"]["fleet"]["seed"]),(7,7))
        self.assertAlmostEqual(c["ieee8500.yaml"]["load_multipliers"][18],BASE["ieee8500.yaml"]["load_multipliers"][18]*1.1,places=6)
        b, b0 = c["bess.yaml"]["battery"], BASE["bess.yaml"]["battery"]
        self.assertAlmostEqual(b["capacity_kwh"]/b["power_kw"],b0["capacity_kwh"]/b0["power_kw"])  # same duration
        self.assertAlmostEqual(b["capex"]/b["capacity_kwh"],b0["capex"]/b0["capacity_kwh"])     # same USD/kWh

    def test_v2g_fleet_keeps_the_3000_vehicles_and_sizes_the_station(self):
        c = case_configs(BASE,{"v2g_fleet": 50})
        e, v = c["evcs.yaml"], c["v2g.yaml"]
        self.assertEqual(e["integration"]["evs"]+v["fleet"]["n_vehicles"],3000)
        self.assertEqual(e["station"]["chargers"],54)  # 50 cars + 4 ports of the independent demand
        self.assertEqual(e["station"]["connection_kw"],54*e["station"]["charger_kw"])
        self.assertEqual(v["standalone"]["station"]["chargers"],e["station"]["chargers"])

    def test_resolution_keeps_daily_energy(self):
        c = case_configs(BASE,{"resolution_min": 15})
        self.assertTrue(all(c[f]["time"]["steps"] == 96 and c[f]["time"]["dt_h"] == .25 for f in ("evcs.yaml","bess.yaml","v2g.yaml")))
        self.assertEqual(len(c["ieee8500.yaml"]["load_multipliers"]),96)
        hourly = BASE["evcs.yaml"]["independent_demand_kw"]
        self.assertAlmostEqual(sum(c["evcs.yaml"]["independent_demand_kw"])*.25,sum(hourly))  # kWh per day unchanged
