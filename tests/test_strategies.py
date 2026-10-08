"""Every registered strategy must honour its module contract; new ones are checked automatically."""
from pathlib import Path
import unittest
import numpy as np
from core.schemas import TimeGrid, read_parameters
from core.assets import Battery, Station
from bess.model import Dispatch
import evcs.strategies, bess.strategies, v2g.strategies
from v2g.model import Vehicle
from v2g.runner import vehicles_from_config, discharge_mask

ROOT = Path(__file__).resolve().parents[1]


class TestStrategies(unittest.TestCase):
    def test_evcs_strategies(self):
        config = read_parameters(ROOT/"configs/evcs.yaml")
        station, grid = Station(**config["station"]), TimeGrid(**config["time"])
        for name, fn in evcs.strategies.STRATEGIES.items():
            with self.subTest(strategy=name):
                demand = np.asarray(fn(station,grid,config),float)
                self.assertEqual(demand.shape,(grid.steps,))
                self.assertTrue(np.isfinite(demand).all() and (demand >= 0).all())

    def test_bess_strategies(self):
        config = read_parameters(ROOT/"configs/bess.yaml")
        battery, grid = Battery(**config["battery"]), TimeGrid(**config["time"])
        demand = np.asarray(config["standalone_demand_kw"],float)
        for name, fn in bess.strategies.STRATEGIES.items():
            with self.subTest(strategy=name):
                d = fn(battery,demand,config,grid.dt_h)
                self.assertIsInstance(d,Dispatch)
                self.assertEqual(d.injection_kw.shape,(grid.steps,))
                self.assertTrue((np.abs(d.injection_kw) <= battery.power_kw+1e-9).all())
                self.assertTrue((d.soc >= battery.soc_min-1e-9).all() and (d.soc <= battery.soc_max+1e-9).all())
                self.assertAlmostEqual(d.soc[-1],battery.soc_initial)

    def test_v2g_strategies(self):
        config = read_parameters(ROOT/"configs/v2g.yaml")
        grid, fixture = TimeGrid(**config["time"]), config["standalone"]
        vehicles = vehicles_from_config(config)
        for name, fn in v2g.strategies.STRATEGIES.items():
            for enable in (True, False):
                with self.subTest(strategy=name,enable_v2g=enable):
                    p = fn(Station(**fixture["station"]),grid,vehicles,fixture["independent_evcs_kw"],
                           fixture["independent_ids"],discharge_mask(config,grid),enable)
                    p.validate()
                    # Re-validating the power matrix enforces SOC, reserve, ratings and sharing.
                    v2g.strategies.fleet_profile(Station(**fixture["station"]),grid,vehicles,p.metadata["vehicle_power_kw"],
                        fixture["independent_evcs_kw"],fixture["independent_ids"],discharge_mask(config,grid),enable)
                    self.assertEqual(set(p.metadata["departure_energy_kwh"]),{v.id for v in vehicles})

    def test_templates_reproduce_defaults(self):
        config = read_parameters(ROOT/"configs/v2g.yaml")
        grid, fixture = TimeGrid(**config["time"]), config["standalone"]
        args = (Station(**fixture["station"]),grid,vehicles_from_config(config),fixture["independent_evcs_kw"],
                fixture["independent_ids"],discharge_mask(config,grid),True)
        heuristic, template = v2g.strategies.schedule(*args), v2g.strategies.template(*args)
        np.testing.assert_allclose(template.total_injection(),heuristic.total_injection())
        np.testing.assert_allclose(template.metadata["energy_kwh"],heuristic.metadata["energy_kwh"])

    def test_fleet_profile_rejects_violations(self):
        grid = TimeGrid(steps=6)
        station = Station("s","n","ABC",2,11,22)
        vehicle = Vehicle("v",Battery(capacity_kwh=60,power_kw=11,soc_initial=.8),1,5,15,.5)
        power = np.zeros((1,6))
        power[0,0] = -5  # vehicle not connected yet
        with self.assertRaisesRegex(ValueError,"disconnected"):
            v2g.strategies.fleet_profile(station,grid,[vehicle],power,[0]*6)
        power = np.zeros((1,6)); power[0,2] = 5  # discharge without a V2G window
        with self.assertRaisesRegex(ValueError,"window"):
            v2g.strategies.fleet_profile(station,grid,[vehicle],power,[0]*6)

    def test_unknown_strategy_is_rejected(self):
        for module in (evcs.strategies,bess.strategies,v2g.strategies):
            with self.subTest(module=module.__name__), self.assertRaisesRegex(ValueError,"available"):
                module.get_strategy("does-not-exist")
