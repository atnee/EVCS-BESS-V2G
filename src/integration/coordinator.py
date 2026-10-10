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
from network.feeder_loader import load_feeder
from network.pandapower_solver import PandapowerSolver
from integration.scenarios import SCENARIOS, integrated_grid, study_feeder
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


def run_scenarios(config_dir="configs", frozen_taps=False):
    """frozen_taps: also solve S1-S4 with the regulator taps of S0 (details[name]['frozen']), to separate
    the effect of the new assets from the regulators' response (see integration.impact)."""
    grid = integrated_grid(config_dir)
    feeder = study_feeder(config_dir)  # configs/network.yaml
    config = read_parameters(Path(config_dir)/f"{feeder}.yaml")
    if config.get("network") != feeder or config.get("solver") != "pandapower.runpp_3ph":
        raise ValueError(f"Scenarios require the configured {feeder} pandapower backend")
    network = load_feeder(feeder)
    multipliers = np.asarray(config.get("load_multipliers", [1.]*grid.steps),float)
    if multipliers.shape != (grid.steps,) or not np.isfinite(multipliers).all() or (multipliers<0).any():
        raise ValueError(f"{feeder}.yaml load_multipliers must match the time horizon")
    # EVCS demand is identical in S1-S4; validating it here also checks the station bus early.
    ev = run_evcs(config_dir,grid)
    ev[0].validate(network)
    nominal_kw = sum(l["p_kw"] for l in network.equipment["feeder_data"][0]["loads"])
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
        if frozen_taps and name != "S0":
            details[name]["frozen"] = PandapowerSolver(multipliers,fixed_taps=details["S0"]["flow"]["source"]).solve(network,profile)
    return pd.DataFrame(records),details


def site_kinds(detail):
    """Bus -> kind of the assets active in a scenario, for the network figures."""
    kinds = {}
    for module,(profile,_) in detail["modules"].items():
        if module == "evcs":
            meta = profile.metadata
            kinds.update({bus: key for bus,key in meta.get("public_sites",{}).items()})
            kinds.setdefault(meta["station"]["bus"],"host")
        elif module == "bess":
            kinds.update({bus: "bess" for bus in set(profile.data.bus)})
    return kinds


def export_impact(output, details):
    """impact_<Sx>.png and impact.csv: each scenario against S0 (integration.impact)."""
    import matplotlib.pyplot as plt
    from network.topology import feeder_graph, bus_table
    from integration.impact import plot_impact, impact_summary, plot_network_state
    network = details["S0"]["network"]
    graph = feeder_graph(network.equipment["feeder_data"][0])
    buses = bus_table(graph)
    rows = []
    for name,d in details.items():
        if name == "S0":
            continue
        assets = sorted(set(d["profile"].data.bus)-{network.slack_bus})
        fig = plot_impact(details["S0"]["flow"],d["flow"],buses,graph,network.slack_bus,frozen=d.get("frozen"),
                          sites={b: b for b in assets},label=name,demand_kw=-d["profile"].total_injection())
        fig.savefig(output/f"impact_{name}.png",dpi=200)
        plt.close(fig)
        fig = plot_network_state(details["S0"]["flow"],d["flow"],buses,graph,network.slack_bus,
                                 site_kinds(d),label=name,title=f"Rede no S0 e no {name}")
        fig.savefig(output/f"network_state_{name}.png",dpi=170)
        plt.close(fig)
        s = impact_summary(details["S0"]["flow"],d["flow"],buses,network.slack_bus,d.get("frozen"))
        row = {"scenario": name, **{k: v for k,v in s.items() if not isinstance(v,dict)}}
        for view in ("regulated","frozen_taps"):
            row.update({f"{view}_{k}": v for k,v in s.get(view,{}).items()})
        row.update(tap_operations_s0=sum(s["tap_operations_s0"].values()),tap_operations=sum(s["tap_operations"].values()))
        rows.append(row)
    pd.DataFrame(rows).to_csv(output/"impact.csv",index=False)


def export_results(output="results/demo", config_dir="configs", frozen_taps=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import hashlib
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    summary,details = run_scenarios(config_dir,frozen_taps)
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
    ax.set(xlabel="Time step",ylabel="Source active power (kW)",title=f"{details['S0']['network'].name}: EVCS–BESS–V2G")
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
              "dataset_provenance":details["S0"]["network"].equipment["feeder_data"][0]["provenance"],
              "dataset_sha256":hashlib.sha256(json.dumps(details["S0"]["network"].equipment["feeder_data"][0],sort_keys=True).encode()).hexdigest(),
              "config_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(config_dir).glob("*.yaml")}}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2))
    export_impact(output,details)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",default="results/demo")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--frozen-taps",action="store_true",
                        help="also solve S1-S4 with the S0 regulator taps (impact figures; ~+6 min on the IEEE 8500)")
    args=parser.parse_args()
    print(export_results(args.output,args.configs,args.frozen_taps).to_string(index=False))

if __name__ == "__main__":
    main()
