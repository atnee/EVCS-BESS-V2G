"""Independent V2G execution with explicit station and occupancy fixtures."""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from core.schemas import TimeGrid, read_parameters
from core.assets import Battery, Station
from core.artifacts import export_profile_bundle
from v2g.model import Vehicle
from v2g.strategies import get_strategy


def vehicles_from_config(config):
    return [Vehicle(battery=Battery(**v["battery"]),**{k:value for k,value in v.items() if k!="battery"})
            for v in config["vehicles"]]


def discharge_mask(config,grid):
    start,end = config["discharge_window_h"]
    if not 0 <= start < end <= grid.steps*grid.dt_h:
        raise ValueError("Discharge window must lie inside the horizon")
    elapsed = np.arange(grid.steps)*grid.dt_h
    return (elapsed>=start)&(elapsed<end)


def output_tables(profile):
    powers,energy = [],[]
    for i,vehicle in enumerate(profile.vehicle_ids):
        powers.extend(dict(time=t,vehicle_id=vehicle,p_kw=p) for t,p in zip(profile.grid.index,profile.metadata["vehicle_power_kw"][i]))
        energy.extend(dict(boundary=t,vehicle_id=vehicle,energy_kwh=e) for t,e in enumerate(profile.metadata["energy_kwh"][i]))
    return {"vehicle_power":pd.DataFrame(powers,columns=["time","vehicle_id","p_kw"]),
            "vehicle_energy":pd.DataFrame(energy,columns=["boundary","vehicle_id","energy_kwh"])}


def run(config_dir="configs"):
    config = read_parameters(Path(config_dir)/"v2g.yaml")
    grid = TimeGrid(**config["time"])
    fixture = config["standalone"]
    return get_strategy(config.get("strategy","heuristic"))(
        Station(**fixture["station"]),grid,vehicles_from_config(config),
        fixture["independent_evcs_kw"],fixture["independent_ids"],discharge_mask(config,grid),True)


def run_integrated(config_dir, grid, evcs_profile, enable_v2g=True):
    """Integration contract: shares the station and served demand carried by the EVCS Profile."""
    config = read_parameters(Path(config_dir)/"v2g.yaml")
    if TimeGrid(**config["time"]) != grid:
        raise ValueError("Integrated EVCS/BESS/V2G time grids must match")
    profile = get_strategy(config.get("strategy","heuristic"))(
        Station(**evcs_profile.metadata["station"]),grid,vehicles_from_config(config),
        # Only the shared host station's demand: public EVCS stations are separate equipment.
        evcs_profile.metadata.get("station_demand_kw",-evcs_profile.total_injection()),
        evcs_profile.vehicle_ids,discharge_mask(config,grid),enable_v2g)
    profile.metadata["kpis"] = dict(v2g_delivered_kwh=profile.metadata["v2g_delivered_kwh"])
    return profile,output_tables(profile)


def export(config_dir="configs", output="results/modules"):
    profile = run(config_dir)
    return export_profile_bundle(profile,Path(output)/"v2g",module="v2g",context="standalone-station-fixture",
                                 tables=output_tables(profile),input_files=[Path(config_dir)/"v2g.yaml"])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--output",default="results/modules",help="Parent directory; writes only its v2g subdirectory")
    args=parser.parse_args()
    print(export(args.configs,args.output))
