"""EVCS siting screening: network hosting capacity x planning parameters x coverage.

python -m integration.evcs_screening --output results/evcs_screening
Peak snapshot (load multiplier 1.0). A screening baseline for the optimization, not its result.
"""
from pathlib import Path
import argparse
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from evcs.planning import from_config, charging_needs, greedy_coverage
from network.hosting import HostingStudy
from network.topology import feeder_graph, bus_table
from integration.s0_study import _style, _edges, INK, MUTED, SURFACE

FLEETS = (100, 300, 600, 1000, 2000, 3000)


def candidate_table(network, buses):
    """Three-phase 4.16 kV buses except the source: where a three-phase station can connect."""
    c = buses[(buses.phases=="ABC") & (buses.vn_kv==4.16) & (buses.bus!=network.slack_bus)]
    return c.reset_index(drop=True)


def run_screening(config_dir="configs", fleets=FLEETS, max_kw=3000., tol_kw=10.):
    params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
    study = HostingStudy()
    graph = feeder_graph(study.network.equipment["ieee123_data"][0])
    buses = bus_table(graph)
    candidates = candidate_table(study.network,buses)
    full = study.sweep(candidates.bus,max_kw,tol_kw)
    voltage_only = HostingStudy(study.network,line_limit_pct=np.inf,equipment_limit_pct=np.inf).sweep(candidates.bus,max_kw*2,tol_kw*5)
    candidates = candidates.merge(full,on="bus").merge(
        voltage_only.rename(columns={"hosting_kw":"voltage_only_kw","binding":"voltage_binding",
                                     "v_min_at_capacity":"voltage_v_min"}),on="bus")
    # Demand proxy: cars live where the load is.
    demand = buses[buses.load_kw > 0][["bus","x","y"]].assign(weight=buses.load_kw[buses.load_kw > 0])
    rows = []
    for evs in fleets:
        p = dataclasses.replace(params,evs=evs)
        need = charging_needs(p)
        chosen,coverage = greedy_coverage(candidates,demand,p,need["stations"],need["station_kw"])
        placement = {c["bus"]: need["station_kw"] for c in chosen}
        joint = study.check(placement) if placement else study.base
        rows.append(dict(evs=evs,**need,placed=len(chosen),buses=[c["bus"] for c in chosen],coverage=coverage,
                         network_accepts=bool(joint["ok"]) and len(chosen)==need["stations"],
                         binding=joint["binding"] or ("not enough eligible sites" if len(chosen) < need["stations"] else ""),
                         v_min_pu=joint["v_min_pu"],max_equipment_pct=joint["max_equipment_pct"]))
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


def plot_coverage(result, evs, ax=None):
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    graph = result["graph"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    row = result["fleets"].set_index("evs").loc[evs]
    radius_ft = result["params"].coverage_radius_m/.3048
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    status = "rede aceita" if row.network_accepts else f"rede NÃO aceita: {row.binding}"
    _style(ax,f"{evs} carros → {row.stations} eletroposto(s) de {row.station_kw:.0f} kW, cobertura {row.coverage:.0%} — {status}")
    _edges(ax,graph,pos,emphasis=False)
    d = result["demand"]
    ax.scatter(d.x,d.y,s=d.weight/2,color="#86b6ef",alpha=.7,edgecolor="none",zorder=2,label="carga (proxy de onde estão os carros)")
    for bus in row.buses:
        x,y = pos[bus]
        ax.add_patch(Circle((x,y),radius_ft,facecolor="#2a78d6",alpha=.07,edgecolor="#2a78d6",lw=1,ls=(0,(4,3)),zorder=1))
        ax.scatter(x,y,marker="P",s=220,color="#eb6834",edgecolor=SURFACE,linewidth=1.5,zorder=5)
        ax.annotate(f"EVCS {bus}",(x,y),xytext=(8,8),textcoords="offset points",fontsize=9,color=INK,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.legend(loc="lower left",frameon=False,fontsize=9)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def export_screening(output="results/evcs_screening", config_dir="configs", fleets=FLEETS):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    result = run_screening(config_dir,fleets)
    result["candidates"].to_csv(output/"hosting_capacity.csv",index=False)
    result["fleets"].to_csv(output/"fleet_scenarios.csv",index=False)
    figures = {"hosting_map": plot_hosting_map(result).figure,
               "voltage_only_map": plot_hosting_map(result,"voltage_only_kw","Capacidade limitada só pela tensão (kW): força elétrica da barra").figure}
    for evs in result["fleets"].evs:
        figures[f"coverage_{evs}_evs"] = plot_coverage(result,evs).figure
    for name,fig in figures.items():
        fig.savefig(output/f"{name}.png",dpi=200,facecolor=SURFACE)
        plt.close(fig)
    summary = {"parameters": dataclasses.asdict(result["params"]),
               "preexisting_line_overloads": result["preexisting_overloads"],
               "base_peak_equipment_pct": result["base"]["max_equipment_pct"],
               "fleets": json.loads(result["fleets"].to_json(orient="records"))}
    (output/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",default="results/evcs_screening")
    parser.add_argument("--configs",default="configs")
    args = parser.parse_args()
    result = export_screening(args.output,args.configs)
    print(result["fleets"].to_string(index=False))


if __name__ == "__main__":
    main()
