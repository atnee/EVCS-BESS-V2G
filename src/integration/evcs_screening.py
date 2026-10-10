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
                           thin_candidates)
from network.hosting import HostingStudy
from network.topology import feeder_graph, bus_table
from integration.s0_study import _style, _edges, INK, MUTED, SURFACE
from integration.scenarios import study_feeder

FLEETS = (2000, 3000, 4000, 5000)   # sensitivity range (cars in the feeder area)
TYPE_STYLE = {"dc": dict(color="#4a3aa7",marker="H",label="hub DC"),
              "ac": dict(color="#eb6834",marker="P",label="eletroposto AC")}


def candidate_table(network, buses, demand=None, spacing_m=0.):
    """Three-phase buses at the feeder voltage (the most common bus voltage), except the source:
    where a three-phase station can connect; thinned to `spacing_m` on large feeders."""
    mv = buses.vn_kv.mode()[0]
    c = buses[(buses.phases=="ABC") & (buses.vn_kv==mv) & (buses.bus!=network.slack_bus)]
    return thin_candidates(c,demand,spacing_m) if demand is not None else c.reset_index(drop=True)


def hosting_sweep(study, candidates, max_kw=3000., tol_kw=10.):
    full = study.sweep(candidates.bus,max_kw,tol_kw)
    voltage_only = HostingStudy(study.network,line_limit_pct=np.inf,equipment_limit_pct=np.inf,tolerance_mw=study.tolerance_mw).sweep(candidates.bus,max_kw*2,tol_kw*5)
    return candidates.merge(full,on="bus").merge(
        voltage_only.rename(columns={"hosting_kw":"voltage_only_kw","binding":"voltage_binding",
                                     "v_min_at_capacity":"voltage_v_min"}),on="bus")


def place_fleet(params, candidates, demand):
    """Size hubs and eletropostos from the simulated sessions, then site hubs first (they need the
    most network capacity), then eletropostos on the remaining buses."""
    sessions = generate_sessions(params)
    sizing = size_sites(params,sessions)
    sites, coverage = {}, {}
    for key in ("dc","ac"):
        if key not in params.types:
            continue
        t, need = params.types[key], sizing[key]
        chosen,coverage[key] = greedy_coverage(candidates,demand,t.coverage_radius_m,t.min_spacing_m,
                                               need["sites"],t.site_kw,taken=sites)
        sites.update({c["bus"]: key for c in chosen})
    return sessions,sizing,sites,coverage


def run_screening(config_dir="configs", fleets=FLEETS, hosting_csv=None, max_kw=3000., tol_kw=10.):
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
        candidates = hosting_sweep(study,candidates,max_kw,tol_kw)
    rows = []
    for evs in fleets:
        p = with_fleet(params,evs)
        sessions,sizing,sites,coverage = place_fleet(p,candidates,demand)
        served,power = simulate(p,sites,sessions)
        total = sum(power.values()) if power else np.zeros(1440)
        installed = {bus: p.types[k].site_kw for bus,k in sites.items()}
        # Main check: each site at its highest simulated 15-min power, all at the feeder peak
        # (conservative: site peaks need not coincide). Installed power is the theoretical worst case.
        simulated = {bus: float(resample(power[bus],p.resolution_min).max()) for bus in sites}
        joint = study.check(simulated) if simulated else study.base
        worst = study.check(installed) if installed else study.base
        missing = [k for k in sizing if sum(v==k for v in sites.values()) < sizing[k]["sites"]]
        rows.append(dict(evs=evs,daily_public_kwh=p.fleet.daily_public_kwh,sessions=len(sessions),
                         hubs=sizing.get("dc",{}).get("sites",0),eletropostos=sizing.get("ac",{}).get("sites",0),
                         chargers_dc=sizing.get("dc",{}).get("chargers",0),chargers_ac=sizing.get("ac",{}).get("chargers",0),
                         sites=json.dumps(sites),installed_kw=sum(installed.values()),
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
    _style(ax,title or "Capacidade de hospedagem por barra na ponta (kW)")
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
    bar.set_label("kW aceitos na barra",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for _,row in pd.concat([c.nsmallest(3,column),c.nlargest(1,column)]).iterrows():
        ax.annotate(f"{row.bus}: {row[column]:.0f} kW",(row.x,row.y),xytext=(6,6),textcoords="offset points",fontsize=8,
                    color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    if column == "hosting_kw":
        ax.legend(handles=[Line2D([],[],marker=m,ls="",color=MUTED,ms=8,label=l) for l,m in
                           (("limitado por regulador",markers["equipment"]),("limitado por linha",markers["line"]),
                            ("limitado por tensão",markers["voltage"]))],loc="lower left",frameon=False,fontsize=9)
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
        ax.scatter(x,y,marker=st["marker"],s=280 if key=="dc" else 220,color=st["color"],edgecolor=SURFACE,
                   linewidth=1.5,zorder=6)
        ax.annotate(f"{t.name} {bus}\n{t.chargers_per_site}×{t.charger_kw:.0f} kW",(x,y),xytext=(9,9),
                    textcoords="offset points",fontsize=8.5,color=INK,
                    bbox=dict(boxstyle="round,pad=.2",fc=SURFACE,ec=st["color"],alpha=.9))
    handles = [Line2D([],[],marker=st["marker"],ls="",color=st["color"],ms=11,
                      label=f'{st["label"]} (raio {params.types[k].coverage_radius_m:.0f} m)')
               for k,st in TYPE_STYLE.items() if k in params.types]
    if demand is not None:
        handles.append(Line2D([],[],marker="o",ls="",color="#86b6ef",ms=8,label="carga existente (proxy de onde estão os carros)"))
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
    status = "rede aceita" if row.network_accepts else f"rede NÃO aceita: {row.binding}"
    return plot_sites(ax,result["graph"],json.loads(row.sites),result["params"],
                      f"{evs} carros → {row.hubs} hub(s) + {row.eletropostos} eletroposto(s) — {status}",result["demand"])


def study_dir(config_dir="configs", study="evcs_screening"):
    """Default output folder of an EVCS study: results/<feeder>/<study> (feeder from configs/network.yaml)."""
    return Path("results")/study_feeder(config_dir)/study


def export_screening(output=None, config_dir="configs", fleets=FLEETS, resweep=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = Path(output or study_dir(config_dir)); output.mkdir(parents=True,exist_ok=True)
    tables = output/"dados"; tables.mkdir(exist_ok=True)  # CSV tables apart from the figures
    result = run_screening(config_dir,fleets,None if resweep else tables/"hosting_capacity.csv")
    result["candidates"].to_csv(tables/"hosting_capacity.csv",index=False)
    result["fleets"].to_csv(tables/"fleet_scenarios.csv",index=False)
    figures = {"hosting_map": plot_hosting_map(result).figure,
               "voltage_only_map": plot_hosting_map(result,"voltage_only_kw","Capacidade limitada só pela tensão (kW): força elétrica da barra").figure}
    for old in output.glob("coverage_*_evs.png"):
        old.unlink()
    for evs in result["fleets"].evs:
        figures[f"coverage_{evs}_evs"] = plot_coverage(result,evs).figure
    for name,fig in figures.items():
        fig.savefig(output/f"{name}.png",dpi=200,facecolor=SURFACE)
        plt.close(fig)
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
    args = parser.parse_args()
    result = export_screening(args.output,args.configs,resweep=args.resweep)
    print(result["fleets"].drop(columns=["sites"]).to_string(index=False))


if __name__ == "__main__":
    main()
