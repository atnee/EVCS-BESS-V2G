import copy
import unittest
from pathlib import Path
import numpy as np
from core.schemas import TimeGrid, read_parameters
from bess.model import Battery
from evcs.model import Station
from v2g.model import Vehicle, schedule
from v2g.fleet import generate_fleet, profile_counts
from v2g.runner import vehicles_from_config, discharge_mask
from v2g.strategies import fleet_profile, STRATEGIES
from v2g.availability import availability, session_steps

class TestV2G(unittest.TestCase):
    def setUp(self):
        self.grid=TimeGrid(steps=6)
        self.station=Station("s","n","ABC",2,11,22)
        self.vehicle=Vehicle("v",Battery(capacity_kwh=60,power_kw=11,soc_initial=.8),1,5,15,.5)
    def test_availability_reserve_and_restoration(self):
        p=schedule(self.station,self.grid,[self.vehicle],[0]*6,discharge_mask=[True,True,True,False,False,True])
        power=np.array(p.metadata["vehicle_power_kw"])[0]
        e=np.array(p.metadata["energy_kwh"])[0]
        self.assertEqual(power[0],0);self.assertEqual(power[-1],0)
        self.assertGreater(power[1],0)
        self.assertGreaterEqual(e.min(),30-1e-7)
        self.assertAlmostEqual(e[5],48)
    def test_duplicate_vehicle(self):
        with self.assertRaises(ValueError): schedule(self.station,self.grid,[self.vehicle],[0]*6,independent_ids=["v"])
    def test_shared_ports(self):
        with self.assertRaises(ValueError): schedule(self.station,self.grid,[self.vehicle],[15]*6)
    def test_shared_connection(self):
        station=Station("s","n","ABC",3,11,12)
        with self.assertRaises(ValueError): schedule(station,self.grid,[self.vehicle],[4]*6)
    def test_infeasible_mobility(self):
        v=Vehicle("v",Battery(capacity_kwh=60,power_kw=1,soc_initial=.1),1,2,30,.5)
        with self.assertRaises(ValueError): schedule(self.station,self.grid,[v],[0]*6)
    def test_unidirectional_station(self):
        with self.assertRaises(ValueError): schedule(Station("s","n","A",2,11,22,bidirectional=False),self.grid,[self.vehicle],[0]*6)
    def test_overnight_session_is_cyclic(self):
        # Connected at 4, 5 (evening) and 0, 1 (morning); 48 kWh at arrival, reserve 30 kWh.
        np.testing.assert_array_equal(availability(6,4,2),[1,1,0,0,1,1])
        self.assertEqual(session_steps(6,4,2),[4,5,0,1])
        v=Vehicle("v",Battery(capacity_kwh=60,power_kw=11,soc_initial=.8),4,2,15,.5)
        mask=[False]*4+[True,False]
        p=schedule(self.station,self.grid,[v],[0]*6,discharge_mask=mask)
        power=np.array(p.metadata["vehicle_power_kw"])[0]
        e=np.array(p.metadata["energy_kwh"])[0]
        after_discharge=48-11/.95
        after_evening=after_discharge+11*.95
        np.testing.assert_allclose(power,[-(48-after_evening)/.95,0,0,0,11,-11])
        np.testing.assert_allclose(e,[after_evening,48,48,48,48,after_discharge,after_evening])
        self.assertAlmostEqual(p.metadata["departure_energy_kwh"]["v"],48)
        self.assertAlmostEqual(p.metadata["v2g_delivered_kwh"],11)
        fleet_profile(self.station,self.grid,[v],[power],[0]*6,discharge_mask=mask)
        with self.assertRaises(ValueError): fleet_profile(self.station,self.grid,[v],[power*[0,1,1,1,1,1]],[0]*6,discharge_mask=mask)
    def test_invalid_sessions(self):
        for arrival,departure in ((2,2),(3,0),(6,1),(0,7)):
            with self.subTest(arrival=arrival,departure=departure), self.assertRaises(ValueError): availability(6,arrival,departure)


ROOT = Path(__file__).resolve().parents[1]

class TestFleet(unittest.TestCase):
    def setUp(self):
        self.config = read_parameters(ROOT/"configs/v2g.yaml")
        self.fleet = copy.deepcopy(self.config["fleet"])
        self.grid = TimeGrid(**self.config["time"])
    def test_profile_counts_sum_to_fleet(self):
        np.testing.assert_array_equal(profile_counts([.8,.2],10),[8,2])
        np.testing.assert_array_equal(profile_counts([1/3,1/3,1/3],10),[4,3,3])
        for bad in ([.5,.4],[1.2,-.2]):
            with self.assertRaises(ValueError): profile_counts(bad,10)
    def test_config_fleet_is_reproducible_and_inside_windows(self):
        vehicles = generate_fleet(self.fleet,self.grid)
        self.assertEqual(len(vehicles),self.fleet["n_vehicles"])
        self.assertEqual(len({v.id for v in vehicles}),len(vehicles))
        self.assertEqual(vehicles,generate_fleet(self.fleet,self.grid))
        for v in vehicles:
            p = self.fleet["profiles"][v.id.rsplit("-",1)[0]]
            self.assertIn(v.arrival,range(p["arrival_h"][0],p["arrival_h"][1]+1))
            self.assertIn(v.departure,range(p["departure_h"][0],p["departure_h"][1]+1))
            self.assertAlmostEqual(v.mobility_kwh,p["daily_km"]*p["kwh_per_km"])
            self.assertGreaterEqual(v.battery.capacity_kwh,self.fleet["battery_min_kwh"])
            self.assertTrue(self.fleet["soc_min"] <= v.battery.soc_initial <= self.fleet["soc_max"])
        self.assertEqual(sum(v.id.startswith("residential") for v in vehicles),80)
        self.assertTrue(all(v.departure < v.arrival for v in vehicles if v.id.startswith("residential")))
        self.fleet["seed"] += 1
        self.assertNotEqual(vehicles,generate_fleet(self.fleet,self.grid))
    def test_fixed_battery_and_quarter_hour_steps(self):
        p = self.fleet["profiles"]["commercial"]
        p.update(battery_std_kwh=0.,arrival_h=[15,15],departure_h=[22,22])
        self.fleet.update(n_vehicles=3,profiles={"commercial":dict(p,share=1.)})
        vehicles = generate_fleet(self.fleet,TimeGrid(steps=96,dt_h=.25))
        self.assertEqual({(v.battery.capacity_kwh,v.arrival,v.departure) for v in vehicles},{(44.9,60,88)})
    def test_invalid_fleet(self):
        for key,value in (("arrival_h",[20,25]),("departure_h",[14,16])):
            fleet = copy.deepcopy(self.fleet)
            fleet["profiles"]["commercial"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): generate_fleet(fleet,self.grid)
        del self.fleet["profiles"]["commercial"]["daily_km"]
        with self.assertRaises(ValueError): generate_fleet(self.fleet,self.grid)
    def test_config_fleet_runs_through_every_strategy(self):
        vehicles = generate_fleet(self.fleet,self.grid)
        connected = np.array([availability(self.grid.steps,v.arrival,v.departure) for v in vehicles]).sum(axis=0)
        demand = np.asarray(self.config["standalone"]["independent_evcs_kw"],float)
        station = Station("s","n","ABC",int(np.ceil(demand/11.).max()+connected.max()),11.,float((demand+11.*connected).max()))
        mask = discharge_mask(self.config,self.grid)
        for name,fn in STRATEGIES.items():
            with self.subTest(strategy=name):
                p = fn(station,self.grid,vehicles,demand,(),mask,True)
                fleet_profile(station,self.grid,vehicles,p.metadata["vehicle_power_kw"],demand,discharge_mask=mask)
                e = np.array(p.metadata["energy_kwh"])
                overnight = [i for i,v in enumerate(vehicles) if v.departure < v.arrival]
                np.testing.assert_allclose(e[overnight,0],e[overnight,-1])
                self.assertGreater(p.metadata["v2g_delivered_kwh"],0)
    def test_daytime_fleet_departs_with_guaranteed_energy(self):
        self.fleet.update(n_vehicles=20,profiles={"commercial":dict(self.fleet["profiles"]["commercial"],share=1.)})
        vehicles = generate_fleet(self.fleet,self.grid)
        station = Station("s","n","ABC",24,11.,44.+11.*20)
        config = dict(self.config,vehicle_source="fleet",fleet=self.fleet)
        self.assertEqual(vehicles_from_config(config),vehicles)
        mask = discharge_mask(self.config,self.grid)
        demand = np.asarray(self.config["standalone"]["independent_evcs_kw"],float)
        p = schedule(station,self.grid,vehicles,demand,discharge_mask=mask)
        fleet_profile(station,self.grid,vehicles,p.metadata["vehicle_power_kw"],demand,discharge_mask=mask)
        for v in vehicles:
            self.assertGreaterEqual(p.metadata["departure_energy_kwh"][v.id]+1e-7,
                                    max(v.reserve_kwh,v.battery.soc_initial*v.battery.capacity_kwh))
        self.assertGreater(p.metadata["v2g_delivered_kwh"],0)
    def test_unknown_vehicle_source(self):
        with self.assertRaises(ValueError): vehicles_from_config(dict(self.config,vehicle_source="taxis"))
