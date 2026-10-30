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
from v2g.strategies import fleet_profile, STRATEGIES, cesar_v1, hourly_to_steps
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
    def test_arrival_rule_keeps_the_old_target(self):
        # Neutrality: restore_trip=False is the rule in force, max(reserve, arrival energy).
        for v in (self.vehicle,Vehicle("w",Battery(capacity_kwh=60,power_kw=11,soc_initial=.3),1,5,15,.5)):
            self.assertFalse(v.restore_trip)
            self.assertEqual(v.departure_target_kwh,max(v.reserve_kwh,v.battery.soc_initial*v.battery.capacity_kwh))
    def test_arrival_plus_trip_target(self):
        # 60 kWh at 0.8 with a 15 kWh trip: min(48 + 15, 0.9*60) = 54 kWh (reserve 30 kWh).
        v = Vehicle("v",Battery(capacity_kwh=60,power_kw=11,soc_initial=.8),1,5,15,.5,restore_trip=True)
        self.assertAlmostEqual(v.departure_target_kwh,54)
        p = schedule(self.station,self.grid,[v],[0]*6,discharge_mask=[True,True,True,False,False,True])
        self.assertAlmostEqual(p.metadata["departure_energy_kwh"]["v"],54)
        fleet_profile(self.station,self.grid,[v],p.metadata["vehicle_power_kw"],[0]*6,
                      discharge_mask=[True,True,True,False,False,True])
    def test_fleet_profile_rejects_discharge_below_reserve(self):
        # Reserve 30 kWh: 11 kW out for two steps leaves 48-2*11/.95 = 24.8 kWh, then 2 h of charging back.
        power = np.array([[0,11,11,-11,-11,0]],float)
        mask = [False,True,True,False,False,False]
        with self.assertRaisesRegex(ValueError,"reserve"):
            fleet_profile(self.station,self.grid,[self.vehicle],power,[0]*6,discharge_mask=mask)
    def test_hourly_prices_follow_quarter_hour_steps(self):
        values = np.arange(24.)
        np.testing.assert_array_equal(hourly_to_steps(values,TimeGrid(steps=96,dt_h=.25)),np.repeat(values,4))
        with self.assertRaises(ValueError): hourly_to_steps(values[:23],self.grid)
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
        names = list(self.fleet["profiles"])
        counts = profile_counts([p["share"] for p in self.fleet["profiles"].values()],self.fleet["n_vehicles"])
        self.assertEqual(sum(v.id.startswith("residential") for v in vehicles),counts[names.index("residential")])
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
        # The fleet: and cesar_v1: blocks are the S3 data; their prices follow the S3 window, not configs/'.
        mask = discharge_mask(dict(self.config,discharge_window_h=S3_WINDOW),self.grid)
        for name,fn in STRATEGIES.items():
            with self.subTest(strategy=name):
                p = fn(station,self.grid,vehicles,demand,(),mask,True,**self.config.get(name,{}))
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
    def test_unknown_departure_rule(self):
        with self.assertRaisesRegex(ValueError,"departure_rule"): vehicles_from_config(dict(self.config,departure_rule="full"))
    def test_departure_rule_reaches_every_vehicle(self):
        for source in ("vehicles","fleet"):
            for rule,flag in (("arrival",False),("arrival_plus_trip",True)):
                vehicles = vehicles_from_config(dict(self.config,vehicle_source=source,departure_rule=rule))
                self.assertTrue(all(v.restore_trip is flag for v in vehicles),(source,rule))
    def test_official_heuristic_matrix_is_unchanged(self):
        # configs/v2g.yaml: 3 cars of 60 kWh connected 8-22 h, reserve 30 kWh, window 17-21 h (steps 17-20).
        # fleet-0/1 (48 kWh, target 48): 11 kW out at 17 -> 48-11/.95; then down to the 30 kWh reserve at 18;
        # 0 at 19; at 20 back to 48-10.45 so 21 can close the gap at 11 kW. fleet-2 (18 kWh, target 30)
        # charges 11 kW at 8 and the remaining (30-18-10.45)/.95 at 9.
        config = self.config
        self.assertEqual((config["strategy"],config["vehicle_source"],config["departure_rule"]),("heuristic","vehicles","arrival"))
        fixture = config["standalone"]
        p = STRATEGIES[config["strategy"]](Station(**fixture["station"]),self.grid,vehicles_from_config(config),
                                            fixture["independent_evcs_kw"],fixture["independent_ids"],
                                            discharge_mask(config,self.grid),True,**config.get(config["strategy"],{}))
        rich = np.zeros(24)
        rich[17],rich[18],rich[20],rich[21] = 11,(48-11/.95-30)*.95,-(48-10.45-30)/.95,-11
        low = np.zeros(24)
        low[8],low[9] = -11,-(30-18-10.45)/.95
        np.testing.assert_allclose(p.metadata["vehicle_power_kw"],[rich,rich,low],atol=1e-12)


S3_WINDOW = [17, 20]


def s3_config():
    """S3 scenario built in memory from configs/v2g.yaml (tests never read notas/)."""
    config = copy.deepcopy(read_parameters(ROOT/"configs/v2g.yaml"))
    config.update(strategy="cesar_v1",vehicle_source="fleet",departure_rule="arrival_plus_trip",discharge_window_h=S3_WINDOW)
    config["standalone"]["station"].update(chargers=154,connection_kw=1694.)
    return config


class TestCesarV1(unittest.TestCase):
    """cesar_v1 on the S3 scenario: 150-car fleet, arrival_plus_trip, 17-20 h window, 154-port station."""
    @classmethod
    def setUpClass(cls):
        cls.config = s3_config()
        cls.options = cls.config["cesar_v1"]
        cls.grid = TimeGrid(**cls.config["time"])
        cls.vehicles = vehicles_from_config(cls.config)
        fixture = cls.config["standalone"]
        cls.station = Station(**fixture["station"])
        cls.demand = np.asarray(fixture["independent_evcs_kw"],float)
        cls.mask = discharge_mask(cls.config,cls.grid)
        cls.profile = cls.solve()
        cls.power = np.array(cls.profile.metadata["vehicle_power_kw"])
        cls.energy = np.array(cls.profile.metadata["energy_kwh"])
    @classmethod
    def solve(cls, enable_v2g=True, **options):
        return cesar_v1(cls.station,cls.grid,cls.vehicles,cls.demand,(),cls.mask,enable_v2g,**dict(cls.options,**options))
    def hours(self, window):
        elapsed = np.arange(self.grid.steps)*self.grid.dt_h
        return np.flatnonzero((elapsed >= window[0]) & (elapsed < window[1]))
    def test_no_capped_departure_target(self):
        self.assertTrue(all(v.restore_trip for v in self.vehicles))
        for v in self.vehicles:
            b = v.battery
            self.assertLessEqual(b.soc_initial,b.soc_max-v.mobility_kwh/b.capacity_kwh+1e-12)
        self.assertEqual(self.profile.metadata["capped_target_vehicles"],[])
    def test_no_charging_in_no_charge_window(self):
        self.assertTrue((self.power[:,self.hours(self.options["no_charge_window_h"])] >= -1e-9).all())
    def test_no_step_charges_and_discharges(self):
        charging,discharging = (self.power < -1e-9).any(axis=0),(self.power > 1e-9).any(axis=0)
        self.assertFalse((charging & discharging).any())
    def test_every_car_leaves_with_its_target(self):
        for v in self.vehicles:
            self.assertGreaterEqual(self.profile.metadata["departure_energy_kwh"][v.id]+1e-7,v.departure_target_kwh)
    def test_energy_stays_above_reserve_after_discharging(self):
        for i,v in enumerate(self.vehicles):
            order = session_steps(self.grid.steps,v.arrival,v.departure)
            out = [k for k,t in enumerate(order) if self.power[i,t] > 1e-9]
            if out:
                after = [self.energy[i,t+1] for t in order[out[0]:]]
                self.assertGreaterEqual(min(after)+1e-7,v.reserve_kwh,v.id)
    def test_discharge_only_in_window(self):
        discharge = np.maximum(self.power,0).sum(axis=0)
        self.assertEqual(set(np.flatnonzero(discharge > 1e-9)),{17,18,19})
        self.assertGreater(self.profile.metadata["v2g_delivered_kwh"],0)
        self.assertEqual(self.profile.metadata["warnings"],[])
    def test_valley_recharge_is_spread(self):
        # Same tariff from 0 to 6 h: one hour holding all of it would peak at the valley total.
        charge = np.maximum(-self.power,0).sum(axis=0)[self.hours([0,6])]
        self.assertGreater(charge.sum(),0)
        self.assertLess(charge.max(),charge.sum())
        self.assertLessEqual(charge.max(),1.01*charge.mean())
    def test_commercial_precharges_and_refills(self):
        rows = [i for i,v in enumerate(self.vehicles) if v.id.startswith("commercial")]
        charge = np.maximum(-self.power[rows],0).sum(axis=0)
        self.assertGreater(charge[self.hours([14,17])].sum(),0)
        self.assertGreater(charge[self.hours([22,24])].sum(),0)
        self.assertEqual(charge[self.hours([17,22])].sum(),0)
    def test_s1_reproduces_schedule(self):
        p = self.solve(enable_v2g=False)
        base = schedule(self.station,self.grid,self.vehicles,self.demand,(),self.mask,False)
        np.testing.assert_allclose(p.metadata["vehicle_power_kw"],base.metadata["vehicle_power_kw"])
        self.assertEqual(p.metadata["v2g_delivered_kwh"],0)
    def test_price_policy_is_enforced(self):
        tariff = list(self.options["tariff_usd_kwh"]); tariff[3] = 0.30  # valley as dear as the peak
        with self.assertRaisesRegex(ValueError,"tariff"): self.solve(tariff_usd_kwh=tariff)
        remuneration = list(self.options["v2g_remuneration_usd_kwh"]); remuneration[21] = 0.40
        with self.assertRaisesRegex(ValueError,"remuneration"): self.solve(v2g_remuneration_usd_kwh=remuneration)
