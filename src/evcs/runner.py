"""Independent EVCS execution: only evcs.yaml and the shared core contract."""
from dataclasses import asdict
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from core.schemas import TimeGrid, Profile, read_parameters
from core.assets import Station
from core.artifacts import export_profile_bundle
from evcs.model import demand_profile
from evcs.strategies import get_strategy
from evcs.planning import from_config, with_fleet, simulate, resample, MINUTES


def station_profile(config, station, grid):
    demand = get_strategy(config.get("strategy","fixed"))(station,grid,config)
    return demand_profile(station,grid,demand,vehicle_ids=("independent-demand-group",))


def run(config_dir="configs"):
    config = read_parameters(Path(config_dir)/"evcs.yaml")
    return station_profile(config,Station(**config["station"]),TimeGrid(**config["time"]))


def public_stations(config, grid):
    """Hubs and eletropostos of the `integration:` block, from the session model of `planning:`.
    Returns (per-site grid power in kW at the grid resolution, sessions) or None without the block."""
    block = config.get("integration")
    if not block:
        return None
    step_min = grid.dt_h*60
    if abs(grid.steps*grid.dt_h-24) > 1e-9 or abs(step_min-round(step_min)) > 1e-9 or MINUTES % round(step_min):
        raise ValueError("Public EVCS stations need a one-day integration grid in whole minutes")
    params = with_fleet(from_config(config),block["evs"])
    sites = {str(bus): key for bus,key in block["sites"].items()}
    unknown = set(sites.values())-set(params.types)
    if unknown:
        raise ValueError(f"Unknown station types in integration.sites: {sorted(unknown)}")
    sessions,power = simulate(params,sites)
    return {bus: resample(kw,round(step_min)) for bus,kw in power.items()},sessions


def run_integrated(config_dir, grid):
    """Integration contract: the coordinator calls only this function. Returns (Profile, tables).

    The Profile holds the V2G host station (`station:`, demand from `strategy:`) plus, when evcs.yaml has an
    `integration:` block, the public hubs and eletropostos simulated with charging sessions.
    metadata['station'] and metadata['station_demand_kw'] describe only the host station, which is the one
    V2G shares; the public stations are separate equipment."""
    config = read_parameters(Path(config_dir)/"evcs.yaml")
    if TimeGrid(**config["time"]) != grid:
        raise ValueError("Integrated EVCS/BESS/V2G time grids must match")
    station = Station(**config["station"])
    if station.currency != "USD":
        raise ValueError("Demo costs require USD; currency conversion is not automatic")
    host = station_profile(config,station,grid)
    requested,served = host.metadata["requested_kwh"],host.metadata["served_kwh"]
    parts,tables,vehicle_ids,capex = [host.data],{},host.vehicle_ids,station.capex
    public = public_stations(config,grid)
    if public:
        power,sessions = public
        params = from_config(config)
        phases = {str(b): ph for b,ph in config["integration"].get("phases",{}).items()}
        for bus,kw in power.items():
            ph = phases.get(bus,"ABC")  # single-phase eletropostos on their phase, the rest balanced
            for one in ph:
                parts.append(pd.DataFrame({"time":grid.index,"bus":bus,"phase":one,"p_kw":-kw/len(ph),"q_kvar":0.}))
        energy_grid = sessions.energy_kwh/params.charger_efficiency  # grid-side energy requested
        requested += float(energy_grid.sum())
        served += float(energy_grid[sessions.served].sum())
        vehicle_ids = (*vehicle_ids,"public-sessions")
        capex += sum(params.types[k].capex_usd for k in config["integration"]["sites"].values())
        tables = {"public_station_power": pd.DataFrame(power).assign(time=grid.index),
                  "public_sessions": sessions}
    data = pd.concat(parts).groupby(["time","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
    profile = Profile(data,grid,"EVCS",tuple(vehicle_ids))
    profile.validate()
    # Station travels inside the profile so V2G never reads evcs.yaml.
    profile.metadata.update(host.metadata,station=asdict(station),
        station_demand_kw=(-host.total_injection()).tolist(),
        public_sites=dict(config.get("integration",{}).get("sites",{})),
        kpis=dict(evcs_requested_kwh=requested,evcs_served_kwh=served,evcs_unserved_kwh=requested-served,
                  capex_usd=capex))
    return profile,tables


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
