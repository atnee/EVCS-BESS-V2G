"""Prove modules can run without sibling code/configs and share no outputs."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import unittest
import yaml
import numpy as np
from core.artifacts import read_profile_bundle

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("evcs","bess","v2g")


def hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob("*") if p.is_file()}


class TestModuleIndependence(unittest.TestCase):
    def test_run_without_sibling_modules_or_network(self):
        for module in MODULES:
            with self.subTest(module=module), TemporaryDirectory() as tmp:
                root = Path(tmp)
                shutil.copy(ROOT/f"configs/{module}.yaml",root)
                # Execute with sibling and network imports actively prohibited.
                code = '''
import importlib.abc, sys
from pathlib import Path
module, root = sys.argv[1:]
blocked = ({'bess','evcs','v2g'}-{module}) | {'network','integration','pandapower','opendssdirect','dss'}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise RuntimeError('Forbidden dependency: '+fullname)
sys.meta_path.insert(0,Block())
from importlib import import_module
import_module(module+'.runner').export(root,Path(root)/'out')
'''
                result = subprocess.run([sys.executable,"-c",code,module,str(root)],cwd=ROOT,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                profile = read_profile_bundle(root/"out"/module)
                self.assertEqual(profile.grid.steps,24)
                manifest=json.loads((root/"out"/module/"manifest.json").read_text())
                self.assertEqual(set(manifest["inputs_sha256"]),{module+".yaml"})

    def test_parallel_exports_and_config_change_are_isolated(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/"configs";shutil.copytree(ROOT/"configs",config)
            outputs=root/"outputs"
            runners={m:importlib.import_module(m+".runner") for m in MODULES}
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures=[pool.submit(runners[m].export,config,outputs) for m in MODULES]
                for f in futures: f.result()
            before={m:hashes(outputs/m) for m in MODULES}
            bess_config=yaml.safe_load((config/"bess.yaml").read_text())
            bess_config["target_kw"]=2500.
            (config/"bess.yaml").write_text(yaml.safe_dump(bess_config))
            runners["bess"].export(config,outputs)
            self.assertNotEqual(hashes(outputs/"bess"),before["bess"])
            for m in ("evcs","v2g"):
                self.assertEqual(hashes(outputs/m),before[m])
                runners[m].export(config,outputs)
                self.assertEqual(hashes(outputs/m),before[m])
            self.assertEqual({p.name for p in outputs.iterdir()},set(MODULES))

    def test_bundle_round_trip_preserves_ids_and_detects_tampering(self):
        from core.schemas import TimeGrid,make_profile
        from core.artifacts import export_profile_bundle
        with TemporaryDirectory() as tmp:
            p=make_profile(TimeGrid(steps=2),"test","001","A",[-5,6],vehicle_ids=("v",))
            export_profile_bundle(p,tmp,module="v2g",context="test")
            result=read_profile_bundle(tmp)
            self.assertEqual(result.data.bus.iloc[0],"001")
            self.assertEqual(result.vehicle_ids,p.vehicle_ids)
            np.testing.assert_allclose(result.total_injection(),p.total_injection())
            with (Path(tmp)/"profile.csv").open("a") as f: f.write("\n")
            with self.assertRaisesRegex(ValueError,"hash mismatch"): read_profile_bundle(tmp)

    def test_integrated_horizon_mismatch_rejected(self):
        from integration.scenarios import integrated_grid
        with TemporaryDirectory() as tmp:
            root=Path(tmp)/"configs";shutil.copytree(ROOT/"configs",root)
            path=root/"bess.yaml";config=yaml.safe_load(path.read_text());config["time"]["steps"]=12
            path.write_text(yaml.safe_dump(config))
            with self.assertRaisesRegex(ValueError,"time grids must match"): integrated_grid(root)

    def test_integration_uses_only_run_integrated(self):
        """Modules may refactor freely; integration only depends on their declared public contracts:
        <module>.runner.run_integrated, plus the EVCS planning API used by the siting screening."""
        import ast
        contracts = {f"{m}.runner": {"run_integrated"} for m in MODULES}
        contracts["evcs.planning"] = {"from_config","with_fleet","generate_sessions","size_sites","simulate","resample","greedy_coverage"}
        for path in (ROOT/"src/integration").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node,ast.Import):
                    self.assertFalse([a.name for a in node.names if a.name.split(".")[0] in MODULES],path.name)
                if isinstance(node,ast.ImportFrom) and (node.module or "").split(".")[0] in MODULES:
                    with self.subTest(file=path.name,module=node.module):
                        self.assertIn(node.module,contracts)
                        self.assertLessEqual({a.name for a in node.names},contracts[node.module])
