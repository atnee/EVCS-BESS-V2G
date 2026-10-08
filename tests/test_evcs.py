import unittest
from core.schemas import TimeGrid
from core.units import energy_kwh, injection_to_consumption
from evcs.model import Station, demand_profile

class TestEVCS(unittest.TestCase):
    def test_capacity_and_unserved_energy(self):
        s=Station("s","n","ABC",2,11,15)
        p=demand_profile(s,TimeGrid(steps=2,dt_h=.5),[10,20])
        self.assertEqual(p.metadata["unserved_kwh"],2.5)
        self.assertEqual(energy_kwh(injection_to_consumption(p.total_injection()),.5),12.5)
    def test_reject_negative_demand(self):
        with self.assertRaises(ValueError):
            demand_profile(Station("s","n","A",1,11,11),TimeGrid(steps=1),[-1])
    def test_reject_invalid_station(self):
        with self.assertRaises(ValueError): Station("s","n","AA",1,11,11)
