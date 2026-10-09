"""Scenario inputs and legacy synthetic fixtures for analytical unit tests."""
import numpy as np
from core.schemas import Bus, Line, Network, TimeGrid, make_profile

SCENARIOS = {"S0": (False,False,False), "S1": (True,False,False),
             "S2": (True,True,False), "S3": (True,False,True), "S4": (True,True,True)}

def synthetic_network() -> Network:
    return Network("SYNTHETIC-4 (NOT IEEE123)",
        [Bus("source"), Bus("n1"), Bus("n2"), Bus("n3","A")],
        [Line("l1","source","n1","ABC",.25,.15,100),
         Line("l2","n1","n2","ABC",.3,.2,80),
         Line("l3","n1","n3","A",.4,.25,50)], "source", synthetic=True)

def study_feeder(config_dir="configs"):
    """Feeder of every study, from configs/network.yaml (`feeder`)."""
    from pathlib import Path
    from core.schemas import read_parameters
    path = Path(config_dir)/"network.yaml"
    return read_parameters(path)["feeder"] if path.exists() else "ieee8500"

def integrated_grid(config_dir="configs"):
    """Shared horizon. Only the `time` block of each module YAML is part of the contract."""
    from pathlib import Path
    from core.schemas import read_parameters
    grids = [TimeGrid(**read_parameters(Path(config_dir)/f"{m}.yaml")["time"]) for m in ("evcs","bess","v2g")]
    if any(g != grids[0] for g in grids):
        raise ValueError("Integrated EVCS/BESS/V2G time grids must match; standalone horizons may differ")
    return grids[0]

def baseline(grid: TimeGrid):
    h = np.arange(grid.steps)*grid.dt_h
    shape = 1+0.65*np.exp(-((h-18)/3)**2)
    return [make_profile(grid,"base-n1","n1","ABC",-90*shape,-27*shape),
            make_profile(grid,"base-n2","n2","AB",-45*shape,-12*shape),
            make_profile(grid,"base-n3","n3","A",-15*shape,-5*shape)]
