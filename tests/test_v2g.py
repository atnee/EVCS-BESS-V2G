import unittest
import numpy as np
from core.schemas import TimeGrid
from bess.model import Battery
from evcs.model import Station
from v2g.model import Vehicle, schedule

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
