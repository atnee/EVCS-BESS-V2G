"""S1 study: hubs and eletropostos from the screening, minute-level charging sessions and an
intraday power flow (planning.resolution_min, 15 min by default) compared with S0 at the same resolution.

python -m integration.s1_study                     # fleets: every fleet of the screening (sensitivity 2000-5000)
python -m integration.s1_study --evs 2000 5000
python -m integration.s1_study --intraday-only      # redraw intraday_sensitivity.png from saved results
Reads results/<feeder>/evcs_screening/ (python -m integration.evcs_screening) and writes
results/<feeder>/s1/; the feeder comes from configs/network.yaml.
"""
from pathlib import Path
import argparse
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import make_profile, read_parameters
from evcs.planning import from_config, with_fleet, simulate, resample
from network.pandapower_solver import PandapowerSolver
from integration.coordinator import combine
from integration.scenarios import integrated_grid, study_feeder
from integration.evcs_screening import plot_sites, TYPE_STYLE, study_dir
from integration.s0_study import (run_s0, feeder_voltages, plot_voltage_profile, plot_daily, marker_scale,
                                  _style, _edges, INK, MUTED, SURFACE, V_LIMITS)

S0_GRAY, S1_RED = "#b9b8b3", "#d03b3b"


def load_fleets(screening_dir):
    fleets = pd.read_csv(Path(screening_dir)/"fleet_scenarios.csv")
    fleets["sites"] = fleets.sites.apply(json.loads)
    return fleets.set_index("evs")


def run_s1(evs, config_dir="configs", screening_dir=None, s0=None, sites=None):
    params = with_fleet(from_config(read_parameters(Path(config_dir)/"evcs.yaml")),evs)
    screening_dir = screening_dir or study_dir(config_dir)
    sites = load_fleets(screening_dir).loc[int(evs)].sites if sites is None else sites
    res = params.resolution_min
    s0 = run_s0(config_dir,res,study_feeder(config_dir)) if s0 is None else s0
    sessions,power = simulate(params,sites)
    network = s0["network"]
    grid = dataclasses.replace(integrated_grid(config_dir),steps=1440//res,dt_h=res/60)
    profiles = [make_profile(grid,"external-zero",network.slack_bus,"ABC",np.zeros(grid.steps))]
    profiles += [make_profile(grid,f"{sites[bus]}-{bus}",bus,"ABC",-resample(kw,res)) for bus,kw in power.items()]
    flow = PandapowerSolver(s0["multipliers"]).solve(network,combine(profiles,network))
    times = flow["source"].time
    study = dict(s0,flow=flow,peak=times[int(flow["source"].p_kw.idxmax())],valley=times[int(flow["source"].p_kw.idxmin())])
    return dict(study=study,s0=s0,params=params,sites=sites,sessions=sessions,power=power,evs=int(evs))


def delta_v(s1):
    """S1 - S0 voltage per bus, phase and step (negative = voltage dropped)."""
    a, b = feeder_voltages(s1["s0"]), feeder_voltages(s1["study"])
    d = a.merge(b,on=["time","bus","phase"],suffixes=("_s0","_s1"))
    d["dv_pu"] = d.v_pu_s1-d.v_pu_s0
    return d.merge(s1["study"]["buses"][["bus","distance_km","x","y"]],on="bus")


def by_type(s1, resolution_min):
    """Grid power per station type at a given resolution (kW)."""
    out = {}
    for bus,kw in s1["power"].items():
        key = s1["sites"][bus]
        out[key] = out.get(key,0)+resample(kw,resolution_min)
    return out


def energy_kwh(study):
    """Daily energy of the substation and of the line/transformer losses, any resolution."""
    dt_h = 24/len(study["flow"]["source"])
    return float(study["flow"]["source"].p_kw.sum()*dt_h), float(study["flow"]["branches"].loss_kw.sum()*dt_h)


def _time_axis(ax):
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3)); ax.set_xlabel("hora do dia",color=MUTED)


def plot_allocation(s1, ax=None):
    import matplotlib.pyplot as plt
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    hubs = sum(k=="dc" for k in s1["sites"].values()); ac = len(s1["sites"])-hubs
    buses = s1["study"]["buses"]; load = buses[buses.load_kw > 0]
    return plot_sites(ax,s1["study"]["graph"],s1["sites"],s1["params"],
                      f"S1 — {s1['evs']} carros: {hubs} hub(s) DC + {ac} eletroposto(s) AC",
                      load[["x","y"]].assign(weight=load.load_kw))


def plot_daily_curves(s1):
    """Daily (hourly) curves: EV demand per station type (stacked) and substation power S0 vs S1."""
    import matplotlib.pyplot as plt
    fig,(ax1,ax2) = plt.subplots(1,2,figsize=(13,4.4),layout="constrained")
    _style(ax1,"Curva diária — demanda das estações (média horária)")
    hours, bottom = np.arange(24)+.5, np.zeros(24)
    for key,kw in by_type(s1,60).items():
        st = TYPE_STYLE[key]
        ax1.bar(hours,kw,.85,bottom=bottom,color=st["color"],label=st["label"],edgecolor=SURFACE,linewidth=1)
        bottom += kw
    _time_axis(ax1); ax1.set_ylabel("kW",color=MUTED); ax1.legend(frameon=False,fontsize=9)
    _style(ax2,"Curva diária — potência ativa na subestação (média horária)")
    for name,study,color,marker in (("S0",s1["s0"],S0_GRAY,"o"),("S1",s1["study"],S1_RED,"v")):
        p = study["flow"]["source"].p_kw.to_numpy()
        ax2.plot(hours,resample(np.repeat(p,1440//len(p)),60),color=color,lw=2,marker=marker,ms=4,label=name)
    _time_axis(ax2); ax2.set_ylabel("kW",color=MUTED); ax2.legend(frameon=False,fontsize=9)
    return fig


def plot_intraday_curves(s1):
    """Intraday curves: EV demand at 1 min / resolution / 1 h (why resolution matters) and the
    substation power at the power-flow resolution, S0 vs S1."""
    import matplotlib.pyplot as plt
    res = s1["params"].resolution_min
    total = sum(s1["power"].values())
    fig,(ax1,ax2) = plt.subplots(2,1,figsize=(13,7.6),layout="constrained",sharex=True)
    _style(ax1,f"Curva intradiária — demanda total das estações (pico: {total.max():.0f} kW em 1 min, "
               f"{resample(total,res).max():.0f} kW em {res} min, {resample(total,60).max():.0f} kW em 1 h)")
    ax1.plot(np.arange(1440)/60,total,color="#86b6ef",lw=.8,label="1 min")
    ax1.step(np.arange(1440//res)*res/60,resample(total,res),where="post",color="#1c5cab",lw=2,label=f"{res} min")
    ax1.step(np.arange(24),resample(total,60),where="post",color=INK,lw=1.4,ls=(0,(4,2)),label="1 h")
    for key,kw in by_type(s1,res).items():
        ax1.step(np.arange(len(kw))*res/60,kw,where="post",color=TYPE_STYLE[key]["color"],lw=1.2,alpha=.9,
                 label=f'{s1["params"].types[key].name} ({res} min)')
    ax1.set_ylabel("kW",color=MUTED); ax1.legend(frameon=False,fontsize=8.5,ncol=5,loc="upper left")
    _style(ax2,f"Curva intradiária — potência ativa na subestação ({res} min)")
    for name,study,color in (("S0",s1["s0"],S0_GRAY),("S1",s1["study"],S1_RED)):
        p = study["flow"]["source"].p_kw.to_numpy()
        ax2.step(np.arange(len(p))*24/len(p),p,where="post",color=color,lw=2,label=name)
    ax2.set_ylabel("kW",color=MUTED); ax2.legend(frameon=False,fontsize=9)
    _time_axis(ax2)
    return fig


def plot_intraday_voltage(s1):
    """Minimum feeder voltage per step (S0 vs S1) and the largest drop the stations cause per step."""
    import matplotlib.pyplot as plt
    fig,(ax1,ax2) = plt.subplots(2,1,figsize=(13,7),layout="constrained",sharex=True,height_ratios=(3,2))
    res = s1["params"].resolution_min
    _style(ax1,f"Curva intradiária de tensão ({res} min) — mínima do alimentador")
    for name,study,color in (("S0",s1["s0"],S0_GRAY),("S1",s1["study"],S1_RED)):
        v = feeder_voltages(study).groupby("time").v_pu.min().to_numpy()
        ax1.step(np.arange(len(v))*24/len(v),v,where="post",color=color,lw=2.2,label=name)
    for limit in V_LIMITS:
        ax1.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax1.set_ylabel("tensão (pu)",color=MUTED); ax1.legend(frameon=False,fontsize=9,loc="upper right")
    drop = -delta_v(s1).groupby("time").dv_pu.min().to_numpy()*100
    worst = int(drop.argmax())
    _style(ax2,f"Maior queda de tensão causada pelas estações em cada passo (máx. {drop.max():.2f}% às "
               f"{worst*res//60:02d}:{worst*res%60:02d})")
    ax2.bar(np.arange(len(drop))*24/len(drop),drop,24/len(drop),align="edge",color=S1_RED,edgecolor=SURFACE,linewidth=.5)
    ax2.set_ylabel("queda (%)",color=MUTED)
    _time_axis(ax2)
    return fig


def intraday_table(flow, slack, exclude=()):
    """Per step: substation P, minimum feeder voltage, highest line loading (lines in `exclude`,
    the base-case overloads, left out), highest regulator/transformer loading and regulator taps."""
    v = flow["voltages"]
    b = flow["branches"]
    b = b[b.physical_phase]
    lines = b[(b.element_type=="line") & ~b.line.isin(list(exclude))].groupby("time").loading_pct.max()
    equipment = b[b.element_type=="trafo"].groupby("time").loading_pct.max()
    source = flow["source"].set_index("time")
    table = pd.DataFrame({"p_kw": source.p_kw,"v_min_pu": v[v.bus!=slack].groupby("time").v_pu.min(),
                          "line_max_pct": lines,"equipment_max_pct": equipment})
    taps = source.filter(like="tap_")
    return table.join(taps).reset_index()


def base_overloads(flow):
    """Lines above 100 % at some step of the base case (reported, not attributed to the stations)."""
    b = flow["branches"]
    b = b[b.physical_phase & (b.element_type=="line")]
    return sorted(b[b.loading_pct > 100].line.unique())


FLEET_COLORS = ["#9ec5f4","#5598e6","#2a6cc0","#123f7a","#0b2447"]  # light → dark = more cars


def plot_intraday_sensitivity(output):
    """Intraday curves of every fleet in `output` (evs_<n>/) against the base case S0 (s0_intraday.csv):
    station demand, substation power, minimum voltage and highest line loading, 15 min."""
    import matplotlib.pyplot as plt
    output = Path(output)
    s0 = pd.read_csv(output/"s0_intraday.csv")
    fleets = sorted(int(d.name.split("_")[1]) for d in output.glob("evs_*") if (d/"intraday.csv").exists())
    steps = len(s0)
    hours = np.arange(steps)*24/steps
    fig,axes = plt.subplots(4,1,figsize=(13,13),layout="constrained",sharex=True,height_ratios=(2,2,2,2))
    titles = ["Demanda total das estações (15 min)","Potência ativa na subestação (15 min)",
              "Tensão mínima do alimentador (15 min)","Maior carregamento de linha (15 min, sem sobrecargas do caso base)"]
    for ax,title in zip(axes,titles):
        _style(ax,title)
    for key,ax in (("p_kw",axes[1]),("v_min_pu",axes[2]),("line_max_pct",axes[3])):
        ax.step(hours,s0[key],where="post",color=S0_GRAY,lw=2.6,label="S0 (caso base)")
    for evs,color in zip(fleets,FLEET_COLORS[-len(fleets):] if len(fleets) <= len(FLEET_COLORS) else FLEET_COLORS*9):
        d = output/f"evs_{evs}"
        table = pd.read_csv(d/"intraday.csv")
        stations = pd.read_csv(next(d.glob("station_power_*min.csv"))).drop(columns="hour").sum(axis=1)
        label = f"{evs} carros"
        axes[0].step(np.arange(len(stations))*24/len(stations),stations,where="post",color=color,lw=1.8,label=label)
        for key,ax in (("p_kw",axes[1]),("v_min_pu",axes[2]),("line_max_pct",axes[3])):
            ax.step(hours,table[key],where="post",color=color,lw=1.6,label=label)
    axes[0].set_ylabel("kW",color=MUTED); axes[1].set_ylabel("kW",color=MUTED)
    axes[2].set_ylabel("tensão (pu)",color=MUTED); axes[3].set_ylabel("carregamento (%)",color=MUTED)
    for limit in V_LIMITS:
        axes[2].axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    axes[3].axhline(100,color=MUTED,lw=1,ls=(0,(4,3)))
    axes[0].legend(frameon=False,fontsize=9,ncol=len(fleets),loc="upper left")
    axes[1].legend(frameon=False,fontsize=9,ncol=len(fleets)+1,loc="upper left")
    _time_axis(axes[-1])
    return fig


def export_intraday_sensitivity(output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plot_intraday_sensitivity(output)
    fig.savefig(Path(output)/"intraday_sensitivity.png",dpi=200,facecolor=SURFACE)
    plt.close(fig)


def plot_sessions(s1):
    """When cars arrive, how long they charge, their SOC on arrival and how long they wait."""
    import matplotlib.pyplot as plt
    s, p = s1["sessions"], s1["params"]
    fig,axes = plt.subplots(2,2,figsize=(13,8),layout="constrained")
    (a1,a2),(a3,a4) = axes
    for key,t in p.types.items():
        part, st = s[s.type==key], TYPE_STYLE[key]
        label = f"{t.name} ({len(part)} sessões)"
        a1.hist(part.arrival_min/60,bins=np.arange(25),color=st["color"],alpha=.75,label=label,edgecolor=SURFACE)
        a2.hist(part.duration_min,bins=20,color=st["color"],alpha=.75,label=label,edgecolor=SURFACE)
        a3.hist(part.soc_arrival*100,bins=np.arange(0,101,5),color=st["color"],alpha=.75,label=label,edgecolor=SURFACE)
        a4.hist(part.wait_min.dropna(),bins=np.arange(0,max(31,t.max_wait_min+2),2),color=st["color"],alpha=.75,
                label=f"{t.name}: {(~part.served).sum()} desistiram",edgecolor=SURFACE)
    for ax,title,xl in ((a1,"Chegadas por hora","hora do dia"),(a2,"Tempo de recarga","minutos"),
                        (a3,"SOC na chegada","%"),(a4,"Espera na fila (atendidos)","minutos")):
        _style(ax,title); ax.set_xlabel(xl,color=MUTED); ax.set_ylabel("sessões",color=MUTED)
        ax.legend(frameon=False,fontsize=8.5)
    return fig


def plot_delta_v_map(s1, ax=None):
    """Voltage drop caused by the stations at the S1 peak step (one-hue sequential: darker = larger drop)."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    study = s1["study"]
    graph = study["graph"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    d = delta_v(s1)
    drop = -d[d.time==study["peak"]].groupby("bus").dv_pu.min()*100
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    _style(ax,f"Queda de tensão causada pelas estações — ponta {study['peak']:%H:%M} (% da nominal)")
    _edges(ax,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("drop",["#f0efec","#ec835a","#d03b3b","#7a1f1f"])
    xy = np.array([pos[b] for b in drop.index])
    points = ax.scatter(xy[:,0],xy[:,1],c=drop.to_numpy(),cmap=cmap,vmin=0,vmax=max(drop.max(),1e-6),s=50*marker_scale(graph),
                        edgecolor=MUTED,linewidth=.5,zorder=3)
    bar = plt.colorbar(points,ax=ax,shrink=.6,pad=.01)
    bar.set_label("queda de tensão (%)",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for bus,key in s1["sites"].items():
        ax.scatter(*pos[bus],marker=TYPE_STYLE[key]["marker"],s=220,color=INK,edgecolor=SURFACE,linewidth=1.2,zorder=5)
    worst = drop.idxmax()
    ax.annotate(f"maior queda: barra {worst}, {drop[worst]:.2f}%",pos[worst],xytext=(-10,-18),textcoords="offset points",
                ha="right",fontsize=9,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_profile_comparison(s1):
    """Minimum phase voltage vs. distance at the S1 peak step: S0 in gray, S1 in red."""
    import matplotlib.pyplot as plt
    study = s1["study"]
    d = delta_v(s1)
    d = d[d.time==study["peak"]].groupby(["bus","distance_km"])[["v_pu_s0","v_pu_s1"]].min().reset_index()
    fig,ax = plt.subplots(figsize=(10,4.8),layout="constrained")
    _style(ax,f"Tensão mínima por barra vs. distância — ponta {study['peak']:%H:%M}")
    ax.scatter(d.distance_km,d.v_pu_s0,s=26*marker_scale(s1["study"]["graph"]),color=S0_GRAY,label="S0 (sem estações)",zorder=2)
    ax.scatter(d.distance_km,d.v_pu_s1,s=22*marker_scale(s1["study"]["graph"]),color=S1_RED,marker="v",label="S1 (com estações)",zorder=3)
    ax.vlines(d.distance_km,d.v_pu_s0,d.v_pu_s1,color=S1_RED,lw=.8,alpha=.5)
    for _,r in d[d.bus.isin(list(s1["sites"]))].iterrows():
        ax.annotate(f'{s1["params"].types[s1["sites"][r.bus]].name} {r.bus}',(r.distance_km,r.v_pu_s1),
                    xytext=(4,-14),textcoords="offset points",fontsize=8,color=INK)
    for limit in V_LIMITS:
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_xlabel("distância elétrica da subestação (km)",color=MUTED); ax.set_ylabel("tensão (pu)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="lower right",ncol=2)
    return fig


def summary(s1):
    study, s0, s, p = s1["study"], s1["s0"], s1["sessions"], s1["params"]
    d = delta_v(s1)
    worst = d.loc[d.dv_pu.idxmin()]
    b = study["flow"]["branches"]; b = b[b.physical_phase & (b.time==study["peak"])]
    total = sum(s1["power"].values())
    per_type = {}
    for key,t in p.types.items():
        part = s[s.type==key]
        per_type[t.name] = dict(sites=[bus for bus,k in s1["sites"].items() if k==key],
                                chargers_per_site=t.chargers_per_site,charger_kw=t.charger_kw,sessions=len(part),
                                served_share=float(part.served.mean()) if len(part) else 1.,
                                energy_kwh=float(part.energy_kwh.sum()),
                                unserved_kwh=float(part.loc[~part.served,"energy_kwh"].sum()),
                                mean_duration_min=float(part.duration_min.mean()) if len(part) else 0.,
                                mean_wait_min=float(part.wait_min.mean()) if part.served.any() else 0.)
    (e0,l0),(e1,l1) = energy_kwh(s0),energy_kwh(study)
    return {"evs": s1["evs"], "battery_kwh_mean": p.fleet.battery_kwh, "resolution_min": p.resolution_min,
            "daily_public_kwh": p.fleet.daily_public_kwh, "types": per_type,
            "ev_peak_kw": {"1min": float(total.max()), f"{p.resolution_min}min": float(resample(total,p.resolution_min).max()),
                           "60min": float(resample(total,60).max())},
            "peak_s0_kw": float(s0["flow"]["source"].p_kw.max()), "peak_s1_kw": float(study["flow"]["source"].p_kw.max()),
            "energy_s0_kwh": e0, "energy_s1_kwh": e1, "losses_s0_kwh": l0, "losses_s1_kwh": l1,
            "max_drop_pu": float(-worst.dv_pu), "max_drop_at": f"{worst.bus}.{worst.phase} {worst.time}",
            "v_min_s0_pu": float(feeder_voltages(s0).v_pu.min()), "v_min_s1_pu": float(feeder_voltages(study).v_pu.min()),
            "buses_below_0.95": int(feeder_voltages(study).query("v_pu < .95").bus.nunique()),
            "max_equipment_loading_peak_pct": float(b[b.element_type=="trafo"].loading_pct.max()),
            "max_line_loading_peak_pct": float(b[b.element_type=="line"].loading_pct.max())}


def export_s1(output=None, config_dir="configs", screening_dir=None, fleets=None):
    """Writes to `output` (default results/<feeder>/s1)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = output or study_dir(config_dir,"s1")
    screening_dir = screening_dir or study_dir(config_dir)
    table = load_fleets(screening_dir)
    params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
    if not fleets:
        fleets = sorted({params.fleet.evs,*table.index})
    s0 = run_s0(config_dir,params.resolution_min,study_feeder(config_dir))
    overloads = base_overloads(s0["flow"])
    Path(output).mkdir(parents=True,exist_ok=True)
    intraday_table(s0["flow"],s0["network"].slack_bus,overloads).to_csv(Path(output)/"s0_intraday.csv",index=False)
    results = []
    for evs in fleets:
        s1 = run_s1(evs,config_dir,screening_dir,s0)
        out = Path(output)/f"evs_{evs}"; out.mkdir(parents=True,exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        for name in ("voltages","branches","source"):
            s1["study"]["flow"][name].to_csv(out/f"{name}.csv",index=False)
        intraday_table(s1["study"]["flow"],s0["network"].slack_bus,overloads).to_csv(out/"intraday.csv",index=False)
        delta_v(s1).to_csv(out/"delta_v.csv",index=False)
        s1["sessions"].to_csv(out/"sessions.csv",index=False)
        res = s1["params"].resolution_min
        pd.DataFrame(s1["power"]).assign(minute=range(1440)).to_csv(out/"station_power_1min.csv",index=False)
        pd.DataFrame({b: resample(kw,res) for b,kw in s1["power"].items()}).assign(
            hour=np.arange(1440//res)*res/60).to_csv(out/f"station_power_{res}min.csv",index=False)
        pd.DataFrame({b: resample(kw,60) for b,kw in s1["power"].items()}).assign(hour=range(24)).to_csv(
            out/"station_power_hourly.csv",index=False)
        figures = {"allocation": plot_allocation(s1).figure, "curves_daily": plot_daily_curves(s1),
                   "curves_intraday": plot_intraday_curves(s1), "voltage_intraday": plot_intraday_voltage(s1),
                   "sessions": plot_sessions(s1), "delta_v_map": plot_delta_v_map(s1).figure,
                   "voltage_comparison": plot_profile_comparison(s1),
                   "voltage_profile": plot_voltage_profile(s1["study"]), "voltage_envelope": plot_daily(s1["study"])}
        for name,fig in figures.items():
            fig.savefig(out/f"{name}.png",dpi=200,facecolor=SURFACE)
            plt.close(fig)
        result = summary(s1)
        (out/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n",encoding="utf-8")
        results.append(result)
    export_intraday_sensitivity(output)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",help="default: results/<feeder>/s1")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--screening",help="default: results/<feeder>/evcs_screening")
    parser.add_argument("--evs",type=int,nargs="*")
    parser.add_argument("--intraday-only",action="store_true",
                        help="only redraw intraday_sensitivity.png from the files already in --output")
    args = parser.parse_args()
    if args.intraday_only:
        export_intraday_sensitivity(args.output or study_dir(args.configs,"s1"))
        return
    print(json.dumps(export_s1(args.output,args.configs,args.screening,args.evs),indent=2,default=str))


if __name__ == "__main__":
    main()
