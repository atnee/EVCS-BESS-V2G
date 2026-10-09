"""S1 study: EVCS stations allocated by the screening, 24 h power flow and comparison with S0.

python -m integration.s1_study --output results/s1            # fleets: evcs.yaml planning.evs + largest accepted
python -m integration.s1_study --evs 300 1000                  # chosen fleet sizes
Needs results/evcs_screening/fleet_scenarios.csv (python -m integration.evcs_screening).
"""
from pathlib import Path
import argparse
import ast
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import make_profile, read_parameters
from evcs.planning import from_config, charging_needs, hourly_demand
from network.pandapower_solver import PandapowerSolver
from integration.coordinator import combine
from integration.scenarios import integrated_grid
from integration.s0_study import (run_s0, voltages_at, feeder_voltages, plot_voltage_profile, plot_daily,
                                  _style, _edges, INK, MUTED, SURFACE)

STATION_COLORS = ("#eb6834","#2a78d6","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948")


def load_fleets(screening_dir):
    fleets = pd.read_csv(Path(screening_dir)/"fleet_scenarios.csv")
    fleets["buses"] = fleets.buses.apply(ast.literal_eval)
    return fleets.set_index("evs")


def station_demand(params, buses, steps):
    """Public charging demand split equally among stations, limited by each station's power."""
    need = charging_needs(params)
    total = hourly_demand(params,steps)
    per_station = total/max(len(buses),1)
    served = np.minimum(per_station,need["station_kw"])
    return {bus: served for bus in buses}, float((per_station-served).clip(0).sum()*len(buses))


def run_s1(evs, config_dir="configs", screening_dir="results/evcs_screening", s0=None):
    s0 = run_s0(config_dir) if s0 is None else s0
    params = dataclasses.replace(from_config(read_parameters(Path(config_dir)/"evcs.yaml")),evs=int(evs))
    row = load_fleets(screening_dir).loc[int(evs)]
    network, grid = s0["network"], integrated_grid(config_dir)
    demand,unserved = station_demand(params,row.buses,grid.steps)
    profiles = [make_profile(grid,"external-zero",network.slack_bus,"ABC",np.zeros(grid.steps))]
    profiles += [make_profile(grid,f"EVCS-{bus}",bus,"ABC",-kw) for bus,kw in demand.items()]
    flow = PandapowerSolver(s0["multipliers"]).solve(network,combine(profiles,network))
    hours = flow["source"].time
    study = dict(s0,flow=flow,peak=hours[int(flow["source"].p_kw.idxmax())],valley=hours[int(flow["source"].p_kw.idxmin())])
    return dict(study=study,s0=s0,params=params,row=row,demand=demand,unserved_kwh=unserved,need=charging_needs(params))


def delta_v(s1):
    """S1 - S0 voltage per bus, phase and hour (negative = voltage dropped)."""
    a, b = feeder_voltages(s1["s0"]), feeder_voltages(s1["study"])
    d = a.merge(b,on=["time","bus","phase"],suffixes=("_s0","_s1"))
    d["dv_pu"] = d.v_pu_s1-d.v_pu_s0
    return d.merge(s1["study"]["buses"][["bus","distance_km","x","y"]],on="bus")


def plot_allocation(s1, ax=None):
    """Where the stations went: coverage circles, demand proxy and hosting limit of each site."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    study, row, p = s1["study"], s1["row"], s1["params"]
    graph, buses = study["graph"], study["buses"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    status = "rede aceita" if row.network_accepts else "rede NÃO aceita"
    _style(ax,f"S1 — {int(row.name)} carros: {len(row.buses)} eletroposto(s) de {row.station_kw:.0f} kW, "
              f"cobertura {row.coverage:.0%}, {status}")
    _edges(ax,graph,pos,emphasis=False)  # no equipment markers: station colours stay unambiguous
    load = buses[buses.load_kw > 0]
    ax.scatter(load.x,load.y,s=load.load_kw/2,color="#86b6ef",alpha=.6,edgecolor="none",zorder=2,
               label="carga existente (proxy de onde estão os carros)")
    radius = p.coverage_radius_m/.3048
    for i,bus in enumerate(row.buses):
        x,y = pos[bus]; color = STATION_COLORS[i % len(STATION_COLORS)]
        ax.add_patch(Circle((x,y),radius,facecolor=color,alpha=.08,edgecolor=color,lw=1.2,ls=(0,(4,3)),zorder=1))
        ax.scatter(x,y,marker="P",s=260,color=color,edgecolor=SURFACE,linewidth=1.5,zorder=6)
        ax.annotate(f"EVCS {bus}\n{row.station_kw:.0f} kW",(x,y),xytext=(9,9),textcoords="offset points",fontsize=9,
                    color=INK,bbox=dict(boxstyle="round,pad=.2",fc=SURFACE,ec=color,alpha=.9))
    ax.scatter([],[],marker="P",s=120,color=STATION_COLORS[0],label=f"eletroposto (raio de cobertura {p.coverage_radius_m:.0f} m)")
    ax.legend(loc="lower left",frameon=False,fontsize=9)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_delta_v_map(s1, ax=None):
    """Voltage drop caused by the stations at the S1 peak hour (one-hue sequential: darker = larger drop)."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    study = s1["study"]
    graph = study["graph"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    d = delta_v(s1)
    drop = -d[d.time==study["peak"]].groupby("bus").dv_pu.min()*100  # % of nominal, positive = drop
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    _style(ax,f"Queda de tensão causada pelos eletropostos — ponta {study['peak']:%H:%M} (% da nominal)")
    _edges(ax,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("drop",["#f0efec","#ec835a","#d03b3b","#7a1f1f"])
    xy = np.array([pos[b] for b in drop.index])
    points = ax.scatter(xy[:,0],xy[:,1],c=drop.to_numpy(),cmap=cmap,vmin=0,vmax=max(drop.max(),1e-6),s=50,
                        edgecolor=MUTED,linewidth=.5,zorder=3)
    bar = plt.colorbar(points,ax=ax,shrink=.6,pad=.01)
    bar.set_label("queda de tensão (%)",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for bus in s1["row"].buses:
        ax.scatter(*pos[bus],marker="P",s=200,color=INK,edgecolor=SURFACE,linewidth=1.2,zorder=5)
    worst = drop.idxmax()
    ax.annotate(f"maior queda: barra {worst}, {drop[worst]:.2f}%",pos[worst],xytext=(-10,-18),textcoords="offset points",
                ha="right",fontsize=9,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_profile_comparison(s1):
    """Minimum phase voltage vs. distance at the S1 peak: S0 in gray, S1 in color."""
    import matplotlib.pyplot as plt
    study = s1["study"]
    d = delta_v(s1)
    d = d[d.time==study["peak"]].groupby(["bus","distance_km"])[["v_pu_s0","v_pu_s1"]].min().reset_index()
    fig,ax = plt.subplots(figsize=(10,4.8),layout="constrained")
    _style(ax,f"Tensão mínima por barra vs. distância — ponta {study['peak']:%H:%M}")
    ax.scatter(d.distance_km,d.v_pu_s0,s=26,color="#b9b8b3",label="S0 (sem eletropostos)",zorder=2)
    ax.scatter(d.distance_km,d.v_pu_s1,s=22,color="#d03b3b",marker="v",label="S1 (com eletropostos)",zorder=3)
    for _,r in d.iterrows():
        ax.plot([r.distance_km]*2,[r.v_pu_s0,r.v_pu_s1],color="#d03b3b",lw=.8,alpha=.5)
    stations = d[d.bus.isin(s1["row"].buses)]
    for _,r in stations.iterrows():
        ax.annotate(f"EVCS {r.bus}",(r.distance_km,r.v_pu_s1),xytext=(4,-14),textcoords="offset points",fontsize=8,color=INK)
    for limit in (.95,1.05):
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_xlabel("distância elétrica da subestação (km)",color=MUTED); ax.set_ylabel("tensão (pu)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="upper center",ncol=2)
    return fig


def plot_power(s1):
    """Station demand per hour (stacked) and substation power S0 vs S1, as two single-axis panels."""
    import matplotlib.pyplot as plt
    study, s0 = s1["study"], s1["s0"]
    hours = np.arange(len(study["flow"]["source"]))
    fig,(ax1,ax2) = plt.subplots(1,2,figsize=(13,4.4),layout="constrained")
    _style(ax1,"Demanda dos eletropostos por hora")
    bottom = np.zeros(len(hours))
    for i,(bus,kw) in enumerate(s1["demand"].items()):
        ax1.bar(hours,kw,.8,bottom=bottom,color=STATION_COLORS[i % len(STATION_COLORS)],label=f"EVCS {bus}",
                edgecolor=SURFACE,linewidth=1)
        bottom += kw
    ax1.set_xlabel("hora",color=MUTED); ax1.set_ylabel("kW",color=MUTED); ax1.legend(frameon=False,fontsize=9)
    _style(ax2,"Potência ativa na subestação")
    ax2.plot(hours,s0["flow"]["source"].p_kw,color="#b9b8b3",lw=2,marker="o",ms=4,label="S0")
    ax2.plot(hours,study["flow"]["source"].p_kw,color="#d03b3b",lw=2,marker="v",ms=4,label="S1")
    ax2.set_xlabel("hora",color=MUTED); ax2.set_ylabel("kW",color=MUTED); ax2.legend(frameon=False,fontsize=9)
    return fig


def summary(s1):
    study, s0 = s1["study"], s1["s0"]
    d = delta_v(s1)
    worst = d.loc[d.dv_pu.idxmin()]
    b = study["flow"]["branches"]; b = b[b.physical_phase & (b.time==study["peak"])]
    return {"evs": int(s1["row"].name), "stations": list(s1["row"].buses), "station_kw": float(s1["row"].station_kw),
            "coverage": float(s1["row"].coverage), "screening_network_accepts": bool(s1["row"].network_accepts),
            "screening_binding": s1["row"].binding if isinstance(s1["row"].binding,str) else "",
            "daily_public_kwh": s1["need"]["daily_public_kwh"], "unserved_kwh": s1["unserved_kwh"],
            "peak_s0_kw": float(s0["flow"]["source"].p_kw.max()), "peak_s1_kw": float(study["flow"]["source"].p_kw.max()),
            "losses_s0_kwh": float(s0["flow"]["branches"].loss_kw.sum()),
            "losses_s1_kwh": float(study["flow"]["branches"].loss_kw.sum()),
            "max_drop_pu": float(-worst.dv_pu), "max_drop_at": f"{worst.bus}.{worst.phase} {worst.time}",
            "v_min_s1_pu": float(feeder_voltages(study).v_pu.min()),
            "buses_below_0.95": int(feeder_voltages(study).query("v_pu < .95").bus.nunique()),
            "max_line_loading_peak_pct": float(b.loading_pct.max()), "max_loaded": b.loc[b.loading_pct.idxmax(),"line"]}


def export_s1(output="results/s1", config_dir="configs", screening_dir="results/evcs_screening", fleets=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    table = load_fleets(screening_dir)
    if not fleets:
        planned = from_config(read_parameters(Path(config_dir)/"evcs.yaml")).evs
        accepted = table[table.network_accepts].index
        fleets = sorted({planned,*(accepted[-1:] if len(accepted) else [])})
    s0 = run_s0(config_dir)
    results = []
    for evs in fleets:
        s1 = run_s1(evs,config_dir,screening_dir,s0)
        out = Path(output)/f"evs_{evs}"; out.mkdir(parents=True,exist_ok=True)
        for table_name in ("voltages","branches","source"):
            s1["study"]["flow"][table_name].to_csv(out/f"{table_name}.csv",index=False)
        delta_v(s1).to_csv(out/"delta_v.csv",index=False)
        pd.DataFrame(s1["demand"]).assign(hour=range(len(next(iter(s1["demand"].values()))))).to_csv(out/"station_demand.csv",index=False)
        figures = {"allocation": plot_allocation(s1).figure, "delta_v_map": plot_delta_v_map(s1).figure,
                   "voltage_comparison": plot_profile_comparison(s1), "power": plot_power(s1),
                   "voltage_profile": plot_voltage_profile(s1["study"]), "daily": plot_daily(s1["study"])}
        for name,fig in figures.items():
            fig.savefig(out/f"{name}.png",dpi=200,facecolor=SURFACE)
            plt.close(fig)
        result = summary(s1)
        (out/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n",encoding="utf-8")
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",default="results/s1")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--screening",default="results/evcs_screening")
    parser.add_argument("--evs",type=int,nargs="*")
    args = parser.parse_args()
    print(json.dumps(export_s1(args.output,args.configs,args.screening,args.evs),indent=2,default=str))


if __name__ == "__main__":
    main()
