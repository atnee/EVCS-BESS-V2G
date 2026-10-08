"""Independent BESS execution with an explicit demand input fixture."""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from core.schemas import TimeGrid, read_parameters, make_profile
from core.assets import Battery
from core.artifacts import export_profile_bundle
from bess.strategies import get_strategy


def profile_from_dispatch(dispatch, grid, connection):
    profile = make_profile(grid,"BESS-1",connection["bus"],connection["phases"],dispatch.injection_kw)
    profile.metadata.update(soc=dispatch.soc.tolist(),rejected_kw=dispatch.rejected_kw.tolist())
    return profile


def output_tables(profile, demand_kw):
    return {"dispatch":pd.DataFrame({"time":profile.grid.index,"demand_before_kw":demand_kw,
                "injection_kw":profile.total_injection(),"rejected_kw":profile.metadata["rejected_kw"]}),
            "soc":pd.DataFrame({"boundary":np.arange(profile.grid.steps+1),"soc":profile.metadata["soc"]})}


def dispatch_from_config(config, battery, demand, grid):
    return get_strategy(config.get("strategy","peak_shave"))(battery,demand,config,grid.dt_h)


def run(config_dir="configs"):
    config = read_parameters(Path(config_dir)/"bess.yaml")
    grid = TimeGrid(**config["time"])
    demand = np.asarray(config["standalone_demand_kw"],float)
    if demand.shape != (grid.steps,):
        raise ValueError("Standalone BESS demand must match its time grid")
    dispatch = dispatch_from_config(config,Battery(**config["battery"]),demand,grid)
    return profile_from_dispatch(dispatch,grid,config["connection"]),demand


def run_integrated(config_dir, grid, demand_kw):
    """Integration contract: peak shaving of the aggregated demand supplied by the coordinator."""
    config = read_parameters(Path(config_dir)/"bess.yaml")
    if TimeGrid(**config["time"]) != grid:
        raise ValueError("Integrated EVCS/BESS/V2G time grids must match")
    battery = Battery(**config["battery"])
    if battery.currency != "USD":
        raise ValueError("Demo costs require USD; currency conversion is not automatic")
    demand = np.asarray(demand_kw,float)
    dispatch = dispatch_from_config(config,battery,demand,grid)
    profile = profile_from_dispatch(dispatch,grid,config["connection"])
    throughput = float(np.abs(dispatch.injection_kw).sum()*grid.dt_h)
    cycles = float((np.maximum(-dispatch.injection_kw,0).sum()*battery.eta_charge+
                    np.maximum(dispatch.injection_kw,0).sum()/battery.eta_discharge)*grid.dt_h/(2*battery.capacity_kwh))
    profile.metadata["kpis"] = dict(bess_throughput_ac_kwh=throughput,bess_equivalent_cycles=cycles,
                                    bess_degradation_usd=throughput*battery.degradation_per_kwh,capex_usd=battery.capex)
    return profile,output_tables(profile,demand)


def export(config_dir="configs", output="results/modules"):
    profile,demand = run(config_dir)
    return export_profile_bundle(profile,Path(output)/"bess",module="bess",context="standalone-demand-fixture",
                                 tables=output_tables(profile,demand),input_files=[Path(config_dir)/"bess.yaml"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--output",default="results/modules",help="Parent directory; writes only its bess subdirectory")
    args=parser.parse_args()
    print(export(args.configs,args.output))
