"""Single integration point, vehicle de-duplication and reproducible exports."""
from pathlib import Path
import argparse
import json
import platform
import numpy as np
import pandas as pd
from core.schemas import Profile, make_profile, read_parameters
# Each module is reached only through run_integrated (see docs/collaboration_guide.md).
from evcs.runner import run_integrated as run_evcs
from bess.runner import run_integrated as run_bess
from v2g.runner import run_integrated as run_v2g
from network.ieee123_loader import load_ieee123
from network.pandapower_solver import PandapowerSolver
from integration.scenarios import SCENARIOS, integrated_grid
from integration.evaluation import evaluate
from core.artifacts import export_profile_bundle

KPI_COLUMNS = ("evcs_requested_kwh","evcs_served_kwh","evcs_unserved_kwh","v2g_delivered_kwh",
               "bess_throughput_ac_kwh","bess_equivalent_cycles","bess_degradation_usd","capex_usd")


def combine(profiles: list[Profile], network) -> Profile:
    if not profiles:
        raise ValueError("No profiles")
    grid = profiles[0].grid
    seen, assets = set(), set()
    for p in profiles:
        p.validate(network)
        if p.grid != grid or seen.intersection(p.vehicle_ids) or p.asset_id in assets:
            raise ValueError("Incompatible horizon or duplicated vehicle/asset")
        seen.update(p.vehicle_ids)
        assets.add(p.asset_id)
    data = pd.concat([p.data for p in profiles]).groupby(["bus","phase","time"],as_index=False)[["p_kw","q_kvar"]].sum()
    result = Profile(data, grid, "integrated", tuple(sorted(seen)))
    result.validate(network)
    return result


def run_scenarios(config_dir="configs"):
    grid = integrated_grid(config_dir)
    config = read_parameters(Path(config_dir)/"ieee123.yaml")
    if config.get("network") != "ieee123" or config.get("solver") != "pandapower.runpp_3ph":
        raise ValueError("Scenarios require the configured IEEE123 pandapower backend")
    network = load_ieee123()
    multipliers = np.asarray(config.get("load_multipliers", [1.]*grid.steps),float)
    if multipliers.shape != (grid.steps,) or not np.isfinite(multipliers).all() or (multipliers<0).any():
        raise ValueError("IEEE123 load_multipliers must match the time horizon")
    # EVCS demand is identical in S1-S4; validating it here also checks the station bus early.
    ev = run_evcs(config_dir,grid)
    ev[0].validate(network)
    nominal_kw = sum(l["p_kw"] for l in network.equipment["ieee123_data"][0]["loads"])
    solver = PandapowerSolver(multipliers)
    records, details = [], {}
    for name,(use_ev,use_bess,use_v2g) in SCENARIOS.items():
        # Original loads and capacitors live in the backend. Only additional
        # EVCS/BESS/V2G injections are aggregated here, avoiding double counting.
        profiles = [make_profile(grid,"external-zero",network.slack_bus,"ABC",np.zeros(grid.steps))]
        modules = {}  # module -> (Profile, tables)
        if use_ev:
            modules["evcs"] = ev
            # Same vehicles and mobility in S1-S4; bidirectional operation only S3/S4.
            modules["v2g"] = run_v2g(config_dir,grid,ev[0],enable_v2g=use_v2g)
            profiles.extend([ev[0],modules["v2g"][0]])
        if use_bess:
            demand_before = nominal_kw*multipliers-combine(profiles,network).total_injection()
            modules["bess"] = run_bess(config_dir,grid,demand_before)
            profiles.append(modules["bess"][0])
        profile = combine(profiles,network)
        flow = solver.solve(network,profile)
        metrics = evaluate(flow,grid.dt_h)
        metrics.update(scenario=name, synthetic=False, network=network.name, fidelity=flow["fidelity"],
                       **dict.fromkeys(KPI_COLUMNS,0.))
        for module_profile,_ in modules.values():
            for key,value in module_profile.metadata["kpis"].items():
                metrics[key] += value  # capex_usd is summed across modules
        records.append(metrics)
        details[name] = {"flow":flow,"profile":profile,"network":network,"modules":modules}
    return pd.DataFrame(records),details


def export_results(output="results/demo", config_dir="configs"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import hashlib
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    summary,details = run_scenarios(config_dir)
    summary.to_csv(output/"summary.csv",index=False)
    fig,ax = plt.subplots(figsize=(9,4.8),layout="constrained")
    for name,d in details.items():
        d["profile"].export(output/f"{name}_injections.csv")
        for module in ("evcs","bess","v2g"):
            active = module in d["modules"]
            profile,tables = d["modules"][module] if active else (
                make_profile(d["profile"].grid,f"{module}-disabled",d["network"].slack_bus,
                             "ABC",np.zeros(d["profile"].grid.steps)),{})
            export_profile_bundle(profile,output/"modules"/module/name,module=module,
                context={"mode":"integrated","scenario":name,"active":active,
                         "v2g_discharge_enabled":SCENARIOS[name][2] if module=="v2g" else None},
                tables=tables,input_files=Path(config_dir).glob("*.yaml"))
        for table in ("voltages","branches","source","native_loads"):
            d["flow"][table].to_csv(output/f"{name}_{table}.csv",index=False)
        ax.plot(np.arange(len(d["flow"]["source"])),d["flow"]["source"].p_kw,label=name,linewidth=1.8)
        if "bess" in d["modules"]:
            d["modules"]["bess"][1]["soc"].to_csv(output/f"{name}_bess_soc.csv",index=False)
        if "v2g" in d["modules"]:
            (output/f"{name}_fleet.json").write_text(json.dumps(d["modules"]["v2g"][0].metadata,indent=2))
    ax.set(xlabel="Time step",ylabel="Source active power (kW)",title="IEEE123 / pandapower: EVCS–BESS–V2G")
    ax.legend(ncol=5);ax.grid(alpha=.2)
    fig.savefig(output/"scenario_power.png",dpi=300)
    plt.close(fig)
    manifest={"python":platform.python_version(),"seed":0,"stochastic":False,
              "network":details["S0"]["network"].name,"solver":"pandapower.runpp_3ph",
              "backend_version":details["S0"]["flow"]["backend_version"],
              "fidelity":details["S0"]["flow"]["fidelity"],
              "limitations":details["S0"]["flow"]["limitations"],
              "injections_scope":"additional DER only; original realized loads in *_native_loads.csv",
              "module_outputs":"modules/{evcs,bess,v2g}/{S0,S1,S2,S3,S4}/profile.csv",
              "dataset_provenance":details["S0"]["network"].equipment["ieee123_data"][0]["provenance"],
              "dataset_sha256":hashlib.sha256(json.dumps(details["S0"]["network"].equipment["ieee123_data"][0],sort_keys=True).encode()).hexdigest(),
              "config_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(config_dir).glob("*.yaml")}}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",default="results/demo")
    parser.add_argument("--configs",default="configs")
    args=parser.parse_args()
    print(export_results(args.output,args.configs).to_string(index=False))

if __name__ == "__main__":
    main()
