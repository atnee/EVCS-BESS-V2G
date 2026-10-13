"""Scenario definitions, the feeder of every study and the shared integration horizon."""
from core.schemas import TimeGrid

SCENARIOS = {"S0": (False,False,False), "S1": (True,False,False),
             "S2": (True,True,False), "S3": (True,False,True), "S4": (True,True,True)}

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
