"""Independent EVCS execution: only evcs.yaml and the shared core contract."""
from dataclasses import asdict
from pathlib import Path
import argparse
from core.schemas import TimeGrid, read_parameters
from core.assets import Station
from core.artifacts import export_profile_bundle
from evcs.model import demand_profile


def run(config_dir="configs"):
    config = read_parameters(Path(config_dir)/"evcs.yaml")
    return demand_profile(Station(**config["station"]),TimeGrid(**config["time"]),
                          config["independent_demand_kw"],vehicle_ids=("independent-demand-group",))


def run_integrated(config_dir, grid):
    """Integration contract: the coordinator calls only this function. Returns (Profile, tables)."""
    config = read_parameters(Path(config_dir)/"evcs.yaml")
    if TimeGrid(**config["time"]) != grid:
        raise ValueError("Integrated EVCS/BESS/V2G time grids must match")
    station = Station(**config["station"])
    if station.currency != "USD":
        raise ValueError("Demo costs require USD; currency conversion is not automatic")
    profile = demand_profile(station,grid,config["independent_demand_kw"],vehicle_ids=("independent-demand-group",))
    # Station travels inside the profile so V2G never reads evcs.yaml.
    profile.metadata.update(station=asdict(station),
        kpis=dict(evcs_requested_kwh=profile.metadata["requested_kwh"],evcs_served_kwh=profile.metadata["served_kwh"],
                  evcs_unserved_kwh=profile.metadata["unserved_kwh"],capex_usd=station.capex))
    return profile,{}


def export(config_dir="configs", output="results/modules"):
    profile = run(config_dir)
    return export_profile_bundle(profile,Path(output)/"evcs",module="evcs",context="standalone",
                                 input_files=[Path(config_dir)/"evcs.yaml"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--output",default="results/modules",help="Parent directory; writes only its evcs subdirectory")
    args=parser.parse_args()
    print(export(args.configs,args.output))
