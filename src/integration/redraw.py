"""Redraw every figure from results already saved, without solving any power flow (seconds to minutes).

python -m integration.redraw                  # feeder of configs/network.yaml: S0, screening, S1, integration, resumo
Use after changing plots. Studies that were not run are skipped. Frozen-tap views (taps held at the S0 values)
are drawn only where their tables were saved (dados/*frozen_taps*); otherwise only the regulated view appears.
"""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from evcs.planning import from_config, with_fleet
from network.feeder_loader import load_feeder, read_feeder_data
from network.topology import feeder_graph, bus_table
from integration.scenarios import study_feeder
from integration.impact import flow_from_csv, impact_figures, network_state_figures
from integration.s0_study import s0_figures, save_figures, load_curve


def _peak_valley(flow):
    source = flow["source"]
    return source.time[source.p_kw.idxmax()], source.time[source.p_kw.idxmin()]


def _frozen(folder, prefix):
    folder = Path(folder)
    return flow_from_csv(folder,prefix) if (folder/f"{prefix}voltages.csv").exists() else None


def redraw(config_dir="configs", results="results"):
    import matplotlib
    matplotlib.use("Agg")
    feeder = study_feeder(config_dir)
    root = Path(results)/feeder
    data = read_feeder_data(feeder)
    graph = feeder_graph(data); buses = bus_table(graph); network = load_feeder(feeder)
    slack = network.slack_bus
    done = []

    def base_study(flow):
        peak,valley = _peak_valley(flow)
        steps = len(flow["source"])
        return dict(flow=flow,graph=graph,buses=buses,network=network,peak=peak,valley=valley,
                    multipliers=load_curve(config_dir,steps,feeder))

    # S0
    if (root/"s0"/"dados"/"voltages.csv").exists():
        save_figures(s0_figures(base_study(flow_from_csv(root/"s0"/"dados")),data),root/"s0",clean=True)
        done.append("s0")

    # Screening
    screening = root/"evcs_screening"
    if (screening/"dados"/"fleet_scenarios.csv").exists():
        from integration.evcs_screening import plot_hosting_map, plot_coverage
        demand = buses[buses.load_kw > 0][["bus","x","y"]].assign(weight=buses.load_kw[buses.load_kw > 0])
        result = dict(params=from_config(read_parameters(Path(config_dir)/"evcs.yaml")),graph=graph,demand=demand,
                      candidates=pd.read_csv(screening/"dados"/"hosting_capacity.csv",dtype={"bus":str}),
                      fleets=pd.read_csv(screening/"dados"/"fleet_scenarios.csv"))
        figures = {"hosting_capacity_map": plot_hosting_map(result).figure,
                   "hosting_capacity_voltage_only_map": plot_hosting_map(
                       result,"voltage_only_kw","Hosting capacity limited by voltage only (kW): electrical strength of each bus").figure}
        for evs in result["fleets"].evs:
            figures[f"stations_{evs}_evs"] = plot_coverage(result,evs).figure
        save_figures(figures,screening,clean=True)
        done.append("evcs_screening")

    # S1, per fleet and across fleets
    s1_dir = root/"s1"
    if (s1_dir/"dados"/"s0_voltages.csv").exists():
        from integration.s1_study import fleet_figures, export_sensitivity, load_fleets
        s0 = base_study(flow_from_csv(s1_dir/"dados","s0_"))
        params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
        fleets = load_fleets(screening)
        for d in sorted(s1_dir.glob("evs_*"),key=lambda p: int(p.name.split("_")[1])):
            if not (d/"dados"/"voltages.csv").exists():
                continue
            evs = int(d.name.split("_")[1])
            case = flow_from_csv(d/"dados")
            power = pd.read_csv(d/"dados"/"station_power_1min.csv").drop(columns="minute")
            peak,valley = _peak_valley(case)
            s1 = dict(study=dict(s0,flow=case,peak=peak,valley=valley),s0=s0,params=with_fleet(params,evs),
                      frozen=_frozen(d/"dados","frozen_taps_"),sites=fleets.loc[evs].sites,phases=fleets.loc[evs].site_phases,
                      sessions=pd.read_csv(d/"dados"/"sessions.csv"),power={c: power[c].to_numpy() for c in power},evs=evs)
            save_figures(fleet_figures(s1,s0),d,clean=True)
        for old in s1_dir.glob("*.png"):  # all-fleet figures are rebuilt below and by the summary
            old.unlink()
        export_sensitivity(s1_dir)
        done.append("s1")

    # Integration S0-S4
    integration = root/"integration"
    tables = integration/"dados"
    if (tables/"S0_voltages.csv").exists():
        import matplotlib.pyplot as plt
        evcs = read_parameters(Path(config_dir)/"evcs.yaml")
        bess = read_parameters(Path(config_dir)/"bess.yaml")["connection"]["bus"]
        base = flow_from_csv(tables,"S0_")
        for old in integration.glob("*.png"):
            old.unlink()
        ax = plt.subplots(figsize=(10,5),layout="constrained")[1]
        for name in ("S0","S1","S2","S3","S4"):
            if not (tables/f"{name}_source.csv").exists():
                continue
            flow = flow_from_csv(tables,f"{name}_")
            steps = len(flow["source"])
            ax.plot(np.arange(steps)*24/steps,flow["source"].p_kw,label=name,linewidth=1.8)
            if name == "S0":
                continue
            sites = {str(b): k for b,k in evcs.get("integration",{}).get("sites",{}).items()}
            sites.setdefault(str(evcs["station"]["bus"]),"host")
            if name in ("S2","S4"):
                sites.setdefault(str(bess),"bess")
            injections = pd.read_csv(tables/f"{name}_injections.csv")
            demand = -injections.groupby("time").p_kw.sum().to_numpy()
            figures = {**impact_figures(base,flow,buses,graph,slack,_frozen(tables,f"{name}_frozen_taps_"),sites,name,demand),
                       **network_state_figures(base,flow,buses,graph,slack,sites,name)}
            save_figures(figures,integration/name,clean=True)
        ax.set(xlabel="hour of day",ylabel="substation active power (kW)",
               title=f"Substation active power per scenario ({network.name})")
        ax.legend(ncol=5); ax.grid(alpha=.2); ax.set_xlim(0,24); ax.set_xticks(range(0,25,3))
        ax.figure.savefig(integration/"demand_substation_power_per_scenario.png",dpi=200)
        plt.close(ax.figure)
        done.append("integration")

    from integration.resumo import export_resumo
    from integration.analysis import export_analysis
    export_resumo(root)
    done.append("resumo")
    if export_analysis(root,config_dir):
        done.append("s0_vs_s1")
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--results",default="results")
    args = parser.parse_args()
    print("redrawn:",", ".join(redraw(args.configs,args.results)))


if __name__ == "__main__":
    main()
