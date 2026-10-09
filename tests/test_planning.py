import dataclasses
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from evcs.planning import from_config, charging_needs, hourly_demand, distances_m, greedy_coverage

ROOT = Path(__file__).resolve().parents[1]


class TestPlanning(unittest.TestCase):
    def setUp(self):
        self.p = from_config(read_parameters(ROOT/"configs/evcs.yaml"))

    def test_sizing_arithmetic(self):
        p = dataclasses.replace(self.p,evs=100,daily_km=40,kwh_per_km=.2,public_share=.5,charger_kw=20,
                                chargers_per_station=4,utilization=.25,peak_hour_share=.1)
        need = charging_needs(p)  # 400 kWh/day, 40 kW peak
        self.assertAlmostEqual(need["daily_public_kwh"],400)
        self.assertEqual((need["chargers_by_peak"],need["chargers_by_energy"]),(2,4))
        self.assertEqual((need["chargers"],need["stations"],need["installed_kw"]),(4,1,80))

    def test_hourly_demand_conserves_energy(self):
        demand = hourly_demand(self.p)
        self.assertEqual(demand.shape,(24,))
        self.assertAlmostEqual(demand.sum(),charging_needs(self.p)["daily_public_kwh"])

    def test_distances_in_metres(self):
        self.assertAlmostEqual(distances_m([[0,0]],[[1000,0]])[0,0],304.8)

    def test_greedy_respects_hosting_and_spacing(self):
        p = dataclasses.replace(self.p,coverage_radius_m=100,min_spacing_m=500)
        candidates = pd.DataFrame(dict(bus=["a","b","c"],x=[0,100,3000],y=[0,0,0],hosting_kw=[50,500,500]))
        demand = pd.DataFrame(dict(bus=["a","b","c"],x=[0,100,3000],y=[0,0,0],weight=[10.,10.,5.]))
        chosen,coverage = greedy_coverage(candidates,demand,p,stations=3,station_kw=100)
        # "a" cannot host 100 kW; "b" and "c" are 885 m apart, so both fit.
        self.assertEqual([c["bus"] for c in chosen],["b","c"])
        self.assertAlmostEqual(coverage,1.)

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            dataclasses.replace(self.p,public_share=0)


class TestHosting(unittest.TestCase):
    def test_capacity_is_feasible_and_bounded(self):
        from network.hosting import HostingStudy
        study = HostingStudy()
        self.assertTrue(study.base["ok"] or study.preexisting)
        result = study.capacity("67",max_kw=2000,tol_kw=50)
        self.assertGreater(result["hosting_kw"],0)
        self.assertTrue(study.evaluate("67",result["hosting_kw"])["ok"])
        self.assertFalse(study.evaluate("67",result["hosting_kw"]+60)["ok"])
        self.assertTrue(study.check({"67": 10.})["ok"])
