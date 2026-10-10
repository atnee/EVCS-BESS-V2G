"""EVCS siting screening: network hosting capacity x fleet sessions x coverage, for DC hubs and AC eletropostos.

python -m integration.evcs_screening                # -> results/<feeder>/evcs_screening/ (configs/network.yaml)
python -m integration.evcs_screening --resweep      # recomputes the hosting sweep (IEEE 8500 ~1.5 h)
Peak snapshot (load multiplier 1.0). A screening baseline for the optimization, not its result.
"""
from pathlib import Path
import argparse
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from evcs.planning import (from_config, with_fleet, generate_sessions, size_sites, simulate, resample, greedy_coverage,
                           thin_candidates, optimal_coverage)
from network.hosting import HostingStudy
from network.feeder_loader import FEEDERS
from network.topology import feeder_graph, bus_table
from integration.s0_study import _style, _edges, save_figures, INK, MUTED, SURFACE
from integration.scenarios import study_feeder

FLEETS = (2000, 3000, 4000, 5000, 10000)   # sensitivity range (cars in the feeder area)
TYPE_STYLE = {"dc": dict(color="#4a3aa7",marker="H",label="DC hub"),
              "ac": dict(color="#eb6834",marker="P",label="three-phase AC station"),
              "ac1": dict(color="#1baf7a",marker="o",label="single-phase AC station")}
SHORT_NAME = {"dc": "DC hub", "ac": "AC station", "ac1": "single-phase AC station"}


def candidate_table(network, buses, demand=None, spacing_m=0.):
    """Three-phase buses at the feeder voltage (the most common bus voltage), except the source:
    where a three-phase station can connect; thinned to `spacing_m` on large feeders."""
    mv = buses.vn_kv.mode()[0]
    c = buses[(buses.phases=="ABC") & (buses.vn_kv==mv) & (buses.bus!=network.slack_bus)]
    return thin_candidates(c,demand,spacing_m) if demand is not None else c.reset_index(drop=True)


def sweep_chunk(feeder, buses, max_kw, tol_kw, tolerance_mw, voltage_only=False):
    """Hosting capacity of a group of buses in its own model (one worker process of hosting_sweep).
    voltage_only: ignore line and equipment limits (electrical strength of the bus)."""
    limits = dict(line_limit_pct=np.inf,equipment_limit_pct=np.inf) if voltage_only else {}
    study = HostingStudy(feeder=feeder,tolerance_mw=tolerance_mw,**limits)
    return study.sweep(buses,max_kw,tol_kw)


def hosting_sweep(study, candidates, max_kw=3000., tol_kw=10., feeder=None, workers=None):
    """Full criteria and voltage-only capacity of every candidate. With several CPU cores the buses are
    dealt round-robin to parallel processes (integration.parallel), each with its own network model."""
    from integration.parallel import run_parallel, default_workers
    feeder = feeder or next(k for k,v in FEEDERS.items() if v["name"] == study.network.equipment["feeder_data"][0]["name"])
    buses = list(candidates.bus)
    n = default_workers(len(buses)) if workers is None else max(1,workers)
    chunks = [buses[i::n] for i in range(n)]
    tasks = [(feeder,c,max_kw,tol_kw,study.tolerance_mw,False) for c in chunks] + \
            [(feeder,c,max_kw*2,tol_kw*5,study.tolerance_mw,True) for c in chunks]
    parts = run_parallel(sweep_chunk,tasks,n)
    full, voltage_only = pd.concat(parts[:n]), pd.concat(parts[n:])
    return candidates.merge(full,on="bus").merge(
        voltage_only.rename(columns={"hosting_kw":"voltage_only_kw","binding":"voltage_binding",
                                     "v_min_at_capacity":"voltage_v_min"}),on="bus")


def ac_candidates(params, buses, slack, hosting, taken=(), excluded=()):
    """Every medium-voltage bus as a possible eletroposto: three-phase buses take the three-phase `ac` type,
    single/two-phase buses the single-phase `ac1` type on their first phase. Three-phase buses whose swept
    hosting capacity is below the site power are left out."""
    mv = buses.vn_kv.mode()[0]
    c = buses[(buses.vn_kv==mv) & (buses.bus!=slack) & ~buses.bus.isin(list(taken)) & ~buses.bus.isin(list(excluded))].copy()
    c["kind"] = np.where(c.phases=="ABC","ac","ac1")
    c = c[c.kind.isin(list(params.types))]
    low = hosting.set_index("bus").hosting_kw
    weak = c.bus.map(low).lt(params.types["ac"].site_kw).fillna(False) & (c.kind=="ac")
    c = c[~weak].reset_index(drop=True)
    c["phase"] = np.where(c.kind=="ac","ABC",c.phases.str[0])
    return c


def place_fleet(params, candidates, demand, buses=None, slack=None, excluded=(), capacity_factor=1.):
    """Size hubs and eletropostos from the simulated sessions, then site hubs first (they need the
    most network capacity), then eletropostos on the remaining buses: greedy maximal coverage with the
    sized count, or (ac_siting: full_coverage) the minimum-cost set that serves every load within the
    radius with at least the sized AC charging power. Returns sessions, sizing, sites {bus: type},
    coverage {type: share} and phases {bus: phase} of the single-phase sites."""
    sessions = generate_sessions(params)
    sizing = size_sites(params,sessions)
    sites, coverage, phases = {}, {}, {}
    hub = params.types.get("dc")
    if hub:
        chosen,coverage["dc"] = greedy_coverage(candidates,demand,hub.coverage_radius_m,hub.min_spacing_m,
                                                sizing["dc"]["sites"],hub.site_kw,taken=sites)
        sites.update({c["bus"]: "dc" for c in chosen})
    ac = params.types["ac"]
    if params.ac_siting == "full_coverage":
        c = ac_candidates(params,buses,slack,candidates,taken=sites,excluded=excluded)
        types = [params.types[k] for k in c.kind]
        capacity = np.array([t.chargers_per_site*params.charging_kw(k) for t,k in zip(types,c.kind)])
        need = sizing["ac"]["chargers"]*params.charging_kw("ac")*capacity_factor
        chosen,coverage["ac"],_ = optimal_coverage(c,demand,ac.coverage_radius_m,[t.capex_usd for t in types],
                                                   capacity,need)
        for i in chosen:
            sites[c.bus.iloc[i]] = c.kind.iloc[i]
            if c.kind.iloc[i] != "ac":
                phases[c.bus.iloc[i]] = c.phase.iloc[i]
    else:
        chosen,coverage["ac"] = greedy_coverage(candidates,demand,ac.coverage_radius_m,ac.min_spacing_m,
                                                sizing["ac"]["sites"],ac.site_kw,taken=sites)
        sites.update({c["bus"]: "ac" for c in chosen})
    return sessions,sizing,sites,coverage,phases


def run_screening(config_dir="configs", fleets=FLEETS, hosting_csv=None, max_kw=3000., tol_kw=10., workers=None):
    params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
    # 10 W outer-loop tolerance: hosting limits are searched to tol_kw anyway.
    study = HostingStudy(feeder=study_feeder(config_dir),tolerance_mw=1e-5)
    graph = feeder_graph(study.network.equipment["feeder_data"][0])
    buses = bus_table(graph)
    # Demand proxy: cars live where the load is.
    demand = buses[buses.load_kw > 0][["bus","x","y"]].assign(weight=buses.load_kw[buses.load_kw > 0])
    candidates = candidate_table(study.network,buses,demand,params.candidate_spacing_m)
    saved = pd.read_csv(hosting_csv,dtype={"bus":str}) if hosting_csv and Path(hosting_csv).exists() else None
    if saved is not None and set(saved.bus) == set(candidates.bus):
        candidates = saved
    else:  # no sweep saved for this feeder/candidate set
        candidates = hosting_sweep(study,candidates,max_kw,tol_kw,study_feeder(config_dir),workers)
    rows = []
    slack = study.network.slack_bus
    for evs in fleets:
        p = with_fleet(params,evs)
        excluded, factor = set(), 1.
        for _ in range(15):
            # Full coverage: (1) drop three-phase sites the network cannot host; (2) if fewer than
            # min_served_share of the AC sessions are served (queues at small sites), ask for more
            # AC charging power and re-solve.
            sessions,sizing,sites,coverage,phases = place_fleet(p,candidates,demand,buses,slack,excluded,factor)
            if p.ac_siting != "full_coverage":
                break
            swept = set(candidates.bus)
            weak = [b for b,k in sites.items() if k == "ac" and b not in swept
                    and not study.evaluate(b,p.types["ac"].site_kw)["ok"]]
            if weak:
                excluded.update(weak)
                continue
            served,power = simulate(p,sites,sessions)
            ac_served = float(served[served.type=="ac"].served.mean())
            if ac_served >= p.min_served_share:
                break
            factor *= 1.15
        served,power = simulate(p,sites,sessions)
        total = sum(power.values()) if power else np.zeros(1440)
        installed = {bus: p.types[k].site_kw for bus,k in sites.items()}
        # Main check: each site at its highest simulated 15-min power, all at the feeder peak
        # (conservative: site peaks need not coincide). Installed power is the theoretical worst case.
        simulated = {bus: float(resample(power[bus],p.resolution_min).max()) for bus in sites}
        joint = study.check(simulated,phases) if simulated else study.base
        worst = study.check(installed,phases) if installed else study.base
        sized = ("dc",) if p.ac_siting == "full_coverage" else ("dc","ac")  # full coverage sizes AC by power
        missing = [k for k in sized if k in sizing and sum(v==k for v in sites.values()) < sizing[k]["sites"]]
        count = lambda key: sum(v==key for v in sites.values())
        rows.append(dict(evs=evs,daily_public_kwh=p.fleet.daily_public_kwh,sessions=len(sessions),
                         hubs=count("dc"),eletropostos=count("ac"),eletropostos_1f=count("ac1"),
                         chargers_dc=sizing.get("dc",{}).get("chargers",0),chargers_ac=sizing.get("ac",{}).get("chargers",0),
                         sites=json.dumps(sites),site_phases=json.dumps(phases),installed_kw=sum(installed.values()),
                         capex_usd=sum(p.types[k].capex_usd for k in sites.values()),
                         excluded_weak_sites=len(excluded),ac_capacity_factor=factor,
                         sim_peak_1min_kw=float(total.max()),sim_peak_15min_kw=float(resample(total,p.resolution_min).max()),
                         sim_peak_60min_kw=float(resample(total,60).max()),served_share=float(served.served.mean()),
                         coverage_hub=coverage.get("dc",0.),coverage_eletroposto=coverage.get("ac",0.),
                         network_accepts=bool(joint["ok"]) and not missing,
                         binding=joint["binding"] or (f"not enough eligible sites for {missing}" if missing else ""),
                         v_min_pu=joint["v_min_pu"],max_equipment_pct=joint["max_equipment_pct"],
                         accepts_installed=bool(worst["ok"]),installed_binding=worst["binding"],
                         installed_equipment_pct=worst["max_equipment_pct"]))
    return dict(params=params,candidates=candidates,fleets=pd.DataFrame(rows),graph=graph,buses=buses,
                demand=demand,preexisting_overloads=study.preexisting,base=study.base)


def plot_hosting_map(result, column="hosting_kw", title=None, ax=None):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.lines import Line2D
    graph = result["graph"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    c = result["candidates"]
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    _style(ax,title or "Hosting capacity per bus at the peak (kW)")
    _edges(ax,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("seq",["#cde2fb","#86b6ef","#3987e5","#1c5cab","#0d366b"])
    binding = c.binding if column == "hosting_kw" else pd.Series(["voltage"]*len(c))
    # Values span a narrow band; start the ramp just below the minimum so differences show.
    low, high = .95*c[column].min(), c[column].max()
    markers = {"equipment": "s", "line": "D", "voltage": "o", ">": "*"}
    kind = binding.str.extract(r"^(equipment|line|voltage|>)")[0].fillna("voltage")
    for k,m in markers.items():
        part = c[kind==k]
        if len(part):
            points = ax.scatter(part.x,part.y,c=part[column],cmap=cmap,vmin=low,vmax=high,marker=m,s=70,
                                edgecolor=MUTED,linewidth=.6,zorder=3)
    bar = plt.colorbar(points,ax=ax,shrink=.6,pad=.01)
    bar.set_label("kW the bus accepts",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for _,row in pd.concat([c.nsmallest(3,column),c.nlargest(1,column)]).iterrows():
        ax.annotate(f"{row.bus}: {row[column]:.0f} kW",(row.x,row.y),xytext=(6,6),textcoords="offset points",fontsize=8,
                    color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    if column == "hosting_kw":
        ax.legend(handles=[Line2D([],[],marker=m,ls="",color=MUTED,ms=8,label=l) for l,m in
                           (("limited by a regulator",markers["equipment"]),("limited by a line",markers["line"]),
                            ("limited by voltage",markers["voltage"]))],loc="lower left",frameon=False,fontsize=9)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_sites(ax, graph, sites, params, title, demand=None):
    """Hubs and eletropostos on the feeder with their coverage radius."""
    from matplotlib.patches import Circle
    from matplotlib.lines import Line2D
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    _style(ax,title)
    _edges(ax,graph,pos,emphasis=False)  # no equipment markers: station colours stay unambiguous
    if demand is not None:
        ax.scatter(demand.x,demand.y,s=demand.weight/2,color="#86b6ef",alpha=.6,edgecolor="none",zorder=2)
    for bus,key in sites.items():
        x,y = pos[bus]; st = TYPE_STYLE[key]; t = params.types[key]
        ax.add_patch(Circle((x,y),t.coverage_radius_m/.3048,facecolor=st["color"],alpha=.06,edgecolor=st["color"],
                            lw=1.1,ls=(0,(4,3)),zorder=1))
        ax.scatter(x,y,marker=st["marker"],s={"dc":280,"ac":220}.get(key,110),color=st["color"],edgecolor=SURFACE,
                   linewidth=1.5,zorder=6)
        if key != "dc" and len(sites) > 12:
            continue  # many eletropostos: labels only for the hubs
        ax.annotate(f"{SHORT_NAME[key]} {bus}\n{t.chargers_per_site}×{t.charger_kw:.0f} kW",(x,y),xytext=(9,9),
                    textcoords="offset points",fontsize=8.5,color=INK,
                    bbox=dict(boxstyle="round,pad=.2",fc=SURFACE,ec=st["color"],alpha=.9))
    handles = [Line2D([],[],marker=st["marker"],ls="",color=st["color"],ms=11,
                      label=f'{st["label"]} ({params.types[k].coverage_radius_m:.0f} m radius)')
               for k,st in TYPE_STYLE.items() if k in params.types]
    if demand is not None:
        handles.append(Line2D([],[],marker="o",ls="",color="#86b6ef",ms=8,label="existing load (proxy for where the cars are)"))
    ax.legend(handles=handles,loc="lower left",frameon=False,fontsize=9)
    # Frame the feeder, not the coverage circles (a hub radius can exceed the whole feeder).
    xy = np.array(list(pos.values()))
    pad = .08*(xy.max(axis=0)-xy.min(axis=0))
    ax.set_xlim(xy[:,0].min()-pad[0],xy[:,0].max()+pad[0]); ax.set_ylim(xy[:,1].min()-2*pad[1],xy[:,1].max()+pad[1])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_coverage(result, evs, ax=None):
    import matplotlib.pyplot as plt
    row = result["fleets"].set_index("evs").loc[evs]
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    status = "network accepts" if row.network_accepts else f"network does NOT accept: {row.binding}"
    single = int(row.get("eletropostos_1f",0))
    return plot_sites(ax,result["graph"],json.loads(row.sites),result["params"],
                      f"{evs} EVs: {row.hubs} DC hub(s), {row.eletropostos} three-phase and {single} single-phase AC stations"
                      f" ({status})",result["demand"])


def study_dir(config_dir="configs", study="evcs_screening"):
    """Default output folder of an EVCS study: results/<feeder>/<study> (feeder from configs/network.yaml)."""
    return Path("results")/study_feeder(config_dir)/study


def export_screening(output=None, config_dir="configs", fleets=FLEETS, resweep=False, workers=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = Path(output or study_dir(config_dir)); output.mkdir(parents=True,exist_ok=True)
    tables = output/"dados"; tables.mkdir(exist_ok=True)  # CSV tables apart from the figures
    result = run_screening(config_dir,fleets,None if resweep else tables/"hosting_capacity.csv",workers=workers)
    result["candidates"].to_csv(tables/"hosting_capacity.csv",index=False)
    result["fleets"].to_csv(tables/"fleet_scenarios.csv",index=False)
    figures = {"hosting_capacity_map": plot_hosting_map(result).figure,
               "hosting_capacity_voltage_only_map": plot_hosting_map(
                   result,"voltage_only_kw","Hosting capacity limited by voltage only (kW): electrical strength of each bus").figure}
    for evs in result["fleets"].evs:
        figures[f"stations_{evs}_evs"] = plot_coverage(result,evs).figure
    save_figures(figures,output,clean=True)
    summary = {"parameters": json.loads(json.dumps(dataclasses.asdict(result["params"]),default=list)),
               "preexisting_line_overloads": result["preexisting_overloads"],
               "base_peak_equipment_pct": result["base"]["max_equipment_pct"],
               "fleets": json.loads(result["fleets"].to_json(orient="records"))}
    (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",help="default: results/<feeder>/evcs_screening")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--resweep",action="store_true",help="recompute the hosting sweep instead of reusing the CSV")
    parser.add_argument("--workers",type=int,help="parallel processes for the hosting sweep (default: CPU cores - 1)")
    args = parser.parse_args()
    result = export_screening(args.output,args.configs,resweep=args.resweep,workers=args.workers)
    print(result["fleets"].drop(columns=["sites"]).to_string(index=False))


if __name__ == "__main__":
    main()
