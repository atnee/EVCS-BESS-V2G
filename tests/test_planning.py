import dataclasses
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from evcs.planning import (from_config, with_fleet, generate_sessions, size_sites, simulate, resample,
                           distances_m, greedy_coverage, MINUTES)

ROOT = Path(__file__).resolve().parents[1]


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.p = with_fleet(from_config(read_parameters(ROOT/"configs/evcs.yaml")),500)
        self.s = generate_sessions(self.p)

    def test_daily_energy_split_by_class_and_service(self):
        f = self.p.fleet
        self.assertAlmostEqual(self.s.energy_kwh.sum(),sum(f.evs*c.share*c.daily_km*c.kwh_per_km*c.public_share
                                                           for c in f.classes))
        for c in f.classes:
            for key,share in (c.services or [(k,t.share) for k,t in self.p.types.items()]):
                part = self.s[(self.s.vehicle_class==c.name) & (self.s.type==key)]
                self.assertAlmostEqual(part.energy_kwh.sum(),f.public_kwh(c)*share)

    def test_class_arrival_shape_is_used(self):
        for c in self.p.fleet.classes:
            for key,shape in c.arrival_shape:
                hours = self.s[(self.s.vehicle_class==c.name) & (self.s.type==key)].arrival_min//60
                self.assertTrue(set(hours) <= {h for h,w in enumerate(shape) if w > 0})

    def test_charging_time_and_car_limits(self):
        for key,t in self.p.types.items():
            part = self.s[self.s.type==key]
            self.assertTrue((part.power_kw <= min(t.charger_kw,self.p.car_limit(key))).all())
            np.testing.assert_array_equal(part.duration_min,np.maximum(1,np.ceil(part.energy_kwh/part.power_kw*60)))
            # Never charges beyond the target SOC.
            self.assertTrue((part.soc_arrival+part.energy_kwh/part.battery_kwh <= t.target_soc+1e-9).all())

    def test_reproducible(self):
        pd.testing.assert_frame_equal(self.s,generate_sessions(self.p))

    def test_simulation_conserves_energy(self):
        sizing = size_sites(self.p,self.s)
        sites = {f"ac{i}": "ac" for i in range(sizing["ac"]["sites"])} | {f"dc{i}": "dc" for i in range(sizing["dc"]["sites"])}
        served,power = simulate(self.p,sites,self.s)
        delivered = sum(kw.sum() for kw in power.values())/60*self.p.charger_efficiency
        self.assertAlmostEqual(delivered,served.loc[served.served,"energy_kwh"].sum(),places=6)
        self.assertTrue((served.wait_min.dropna() <= served.type.map({k:t.max_wait_min for k,t in self.p.types.items()})[served.served]).all())

    def test_queue_when_chargers_are_short(self):
        one = {"only": "ac"}
        served,power = simulate(self.p,one,self.s)
        busy = served[served.served & (served.type=="ac")]
        # Never more cars charging than chargers at one site.
        count = np.zeros(MINUTES)
        for s,d in zip(busy.start_min,busy.duration_min):
            np.add.at(count,(np.arange(int(d))+int(s)) % MINUTES,1)
        self.assertLessEqual(count.max(),self.p.types["ac"].chargers_per_site)
        self.assertFalse(served[served.type=="ac"].served.all())

    def test_sessions_past_midnight_hold_their_charger(self):
        """The day is periodic: an evening session that runs past midnight keeps its charger busy
        in the early hours of the same simulated day."""
        p = dataclasses.replace(self.p,types={k: dataclasses.replace(t,chargers_per_site=1) for k,t in self.p.types.items()})
        late = pd.DataFrame([dict(type="ac",vehicle_class="car",arrival_min=1380,battery_kwh=60.,soc_arrival=.1,
                                  energy_kwh=33.,power_kw=11.,grid_kw=11/.95,duration_min=180),
                             dict(type="ac",vehicle_class="car",arrival_min=30,battery_kwh=60.,soc_arrival=.1,
                                  energy_kwh=5.,power_kw=11.,grid_kw=11/.95,duration_min=28)]).sort_values("arrival_min")
        served,power = simulate(p,{"x": "ac"},late.reset_index(drop=True))
        # The 23:00 session (3 h) still holds the only charger at 00:30: the early car waits past its patience.
        self.assertEqual(served.set_index("arrival_min").served.to_dict(),{30: False,1380: True})
        self.assertLessEqual(power["x"].max(),p.charging_kw("ac")/p.charger_efficiency+1e-9)

    def test_resample_preserves_energy(self):
        minute = np.random.default_rng(0).random(MINUTES)
        for res in (1,15,60):
            self.assertAlmostEqual(resample(minute,res).sum()*res,minute.sum())

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            dataclasses.replace(self.p.types["ac"],share=1.5)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.p,resolution_min=7)
        with self.assertRaises(ValueError):  # vehicle shares must add up to 1
            dataclasses.replace(self.p.fleet,classes=self.p.fleet.classes[:1])
        with self.assertRaises(ValueError):  # service shares of a class must add up to 1
            dataclasses.replace(self.p.fleet.classes[0],services=(("ac",.5),("dc",.2)))


class TestSiting(unittest.TestCase):
    def test_distances_in_metres(self):
        self.assertAlmostEqual(distances_m([[0,0]],[[1000,0]])[0,0],304.8)

    def test_greedy_respects_hosting_spacing_and_taken(self):
        candidates = pd.DataFrame(dict(bus=["a","b","c","d"],x=[0,100,3000,3100],y=[0]*4,hosting_kw=[50,500,500,500]))
        demand = pd.DataFrame(dict(bus=["a","b","c"],x=[0,100,3000],y=[0,0,0],weight=[10.,10.,5.]))
        chosen,coverage = greedy_coverage(candidates,demand,100,500,stations=3,station_kw=100,taken={"c"})
        # "a" cannot host 100 kW, "c" is taken, "d" covers c's demand and is far enough from "b".
        self.assertEqual([c["bus"] for c in chosen],["b","d"])
        self.assertAlmostEqual(coverage,1.)


class TestHosting(unittest.TestCase):
    def test_capacity_is_feasible_and_bounded(self):
        from network.hosting import HostingStudy
        study = HostingStudy(feeder="ieee8500",tolerance_mw=1e-5)
        self.assertTrue(study.base["ok"] or study.preexisting)
        bus = "l2804249"  # three-phase candidate limited by a line at ~620 kW in the screening
        result = study.capacity(bus,max_kw=1500,tol_kw=100)
        self.assertGreater(result["hosting_kw"],0)
        self.assertTrue(study.evaluate(bus,result["hosting_kw"])["ok"])
        self.assertFalse(study.evaluate(bus,result["hosting_kw"]+110)["ok"])
        self.assertTrue(study.check({bus: 10.})["ok"])


class TestFullCoverage(unittest.TestCase):
    """optimal_coverage (set covering with capacity) and single-phase `ac1` eletropostos."""
    def test_covers_everything_at_minimum_cost(self):
        from evcs.planning import optimal_coverage
        ft = 1/.3048  # coordinates in feet
        candidates = pd.DataFrame(dict(bus=list("abcd"),x=[0,600*ft,1200*ft,600*ft],y=[0,0,0,10*ft]))
        demand = pd.DataFrame(dict(bus=list("pqr"),x=[0,600*ft,1200*ft],y=[0,0,0],weight=[1.,1.,1.]))
        chosen,covered,uncovered = optimal_coverage(candidates,demand,500.,cost=[1,1,1,1],capacity_kw=[10]*4)
        self.assertEqual(covered,1.)
        self.assertEqual(len(chosen),3)  # each point needs its own site 600 m apart
        self.assertTrue(uncovered.empty)
        # Capacity requirement forces a fourth site.
        chosen,_,_ = optimal_coverage(candidates,demand,500.,cost=[1,1,1,1],capacity_kw=[10]*4,min_capacity_kw=40)
        self.assertEqual(len(chosen),4)

    def test_unreachable_demand_is_reported(self):
        from evcs.planning import optimal_coverage
        candidates = pd.DataFrame(dict(bus=["a"],x=[0],y=[0]))
        demand = pd.DataFrame(dict(bus=["p","far"],x=[0,1e5],y=[0,0],weight=[1.,1.]))
        chosen,covered,uncovered = optimal_coverage(candidates,demand,500.,cost=[1],capacity_kw=[10])
        self.assertEqual(list(uncovered.bus),["far"])
        self.assertAlmostEqual(covered,.5)

    def test_single_phase_sites_serve_ac_sessions_at_their_power(self):
        p = with_fleet(from_config(read_parameters(ROOT/"configs/evcs.yaml")),2000)
        sessions,power = simulate(p,{"x": "ac1"})
        ac = sessions[sessions.type=="ac"]
        self.assertTrue(ac.served.any())
        self.assertTrue((ac.loc[ac.served,"power_kw"] == p.charging_kw("ac1")).all())
        self.assertLessEqual(power["x"].max(),2*p.charging_kw("ac1")/p.charger_efficiency+1e-9)
