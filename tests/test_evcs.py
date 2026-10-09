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


class TestIntegratedPublicStations(unittest.TestCase):
    """run_integrated: V2G host station plus the public hubs/eletropostos of the `integration:` block."""
    def setUp(self):
        from pathlib import Path
        import shutil, tempfile, yaml
        self.root = Path(__file__).resolve().parents[1]
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(self.root/"configs",self.tmp/"configs")
        self.configs = self.tmp/"configs"
        self.yaml = yaml

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_public_stations_join_the_profile(self):
        from integration.scenarios import integrated_grid
        from evcs.runner import run_integrated
        grid = integrated_grid(self.configs)
        profile,tables = run_integrated(self.configs,grid)
        config = self.yaml.safe_load((self.configs/"evcs.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(profile.data.bus),set(config["integration"]["sites"])|{config["station"]["bus"]})
        k = profile.metadata["kpis"]
        self.assertAlmostEqual(k["evcs_served_kwh"],-profile.total_injection().sum()*grid.dt_h,places=6)
        self.assertGreater(k["evcs_requested_kwh"],k["evcs_served_kwh"]-1e-9)
        self.assertGreater((-profile.total_injection()).max(),config["station"]["connection_kw"])  # public stations dominate
        # V2G shares only the host station: its demand is carried separately.
        host = config["independent_demand_kw"]
        self.assertEqual(len(profile.metadata["station_demand_kw"]),grid.steps)
        self.assertTrue(all(abs(a-min(b,config["station"]["connection_kw"])) < 1e-9
                            for a,b in zip(profile.metadata["station_demand_kw"],host)))
        self.assertIn("public_sessions",tables)

    def test_without_integration_block_only_host_station(self):
        from integration.scenarios import integrated_grid
        from evcs.runner import run_integrated
        path = self.configs/"evcs.yaml"
        config = self.yaml.safe_load(path.read_text(encoding="utf-8"))
        config.pop("integration")
        path.write_text(self.yaml.safe_dump(config),encoding="utf-8")
        profile,_ = run_integrated(self.configs,integrated_grid(self.configs))
        self.assertEqual(set(profile.data.bus),{config["station"]["bus"]})
        self.assertEqual(profile.metadata["kpis"]["capex_usd"],config["station"]["capex"])
