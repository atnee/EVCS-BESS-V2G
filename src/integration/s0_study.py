"""S0 baseline study: IEEE123 without DER — topology, line parameters and voltage profile.

python -m integration.s0_study --output results/s0
"""
from pathlib import Path
import argparse
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import make_profile, read_parameters
from integration.scenarios import integrated_grid
from network.ieee123_loader import load_ieee123
from network.pandapower_solver import PandapowerSolver
from network.topology import (feeder_graph, bus_table, graph_metrics, line_table, linecode_table,
                              electrical_inventory)

# Reference palette: categorical slots 1-3 (validated all-pairs) plus a marker per phase,
# so phase identity never depends on color alone.
PHASE_STYLE = {"A": dict(color="#2a78d6",marker="o"), "B": dict(color="#eb6834",marker="s"),
               "C": dict(color="#1baf7a",marker="^")}
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
EQUIPMENT = {"regulator": "#eb6834", "transformer": "#4a3aa7", "capacitor": "#2a78d6"}
V_LIMITS = (.95, 1.05)  # ANSI C84.1 range A


def load_curve(config_dir, steps):
    """ieee123.yaml hourly multipliers at `steps` per day: linear between hour centres, periodic."""
    hourly = np.asarray(read_parameters(Path(config_dir)/"ieee123.yaml").get("load_multipliers",[1.]*24),float)
    if steps == len(hourly):
        return hourly
    if len(hourly) != 24:
        raise ValueError("Intraday resampling needs 24 hourly load multipliers")
    return np.interp((np.arange(steps)+.5)*24/steps,np.arange(24)+.5,hourly,period=24)


def run_s0(config_dir="configs", resolution_min=None):
    """Daily S0 power flow (IEEE123 loads only, no DER) with the ieee123.yaml load curve.
    resolution_min=None keeps the integration grid; e.g. 15 gives 96 intraday steps."""
    grid = integrated_grid(config_dir)
    if resolution_min:
        grid = dataclasses.replace(grid,steps=1440//resolution_min,dt_h=resolution_min/60)
    multipliers = load_curve(config_dir,grid.steps)
    network = load_ieee123()
    no_der = make_profile(grid,"no-DER",network.slack_bus,"ABC",np.zeros(grid.steps))
    flow = PandapowerSolver(multipliers).solve(network,no_der)
    graph = feeder_graph(network.equipment["ieee123_data"][0])
    buses = bus_table(graph)
    hours = flow["source"].time
    peak, valley = int(flow["source"].p_kw.idxmax()), int(flow["source"].p_kw.idxmin())
    return dict(flow=flow,graph=graph,buses=buses,network=network,multipliers=multipliers,
                peak=hours[peak],valley=hours[valley])


def feeder_voltages(study):
    """Voltages without the ideal source bus, which is fixed at 1.0 pu upstream of reg1."""
    v = study["flow"]["voltages"]
    return v[v.bus != study["network"].slack_bus]


def voltages_at(study, time):
    v = study["flow"]["voltages"]
    v = v[v.time == time].merge(study["buses"][["bus","distance_km","parent"]],on="bus")
    return v


def _style(ax, title=None):
    ax.set_facecolor(SURFACE)
    for side in ("top","right"):
        ax.spines[side].set_visible(False)
    for side in ("left","bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED,labelsize=9)
    ax.grid(color=GRID,linewidth=.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title,loc="left",fontsize=11,color=INK)


def _edges(ax, graph, pos, emphasis=True):
    """Lines drawn by phase count; equipment and switches on top."""
    for u,v,d in graph.edges(data=True):
        (x0,y0),(x1,y1) = pos[u],pos[v]
        if d["kind"] == "line":
            width,color = {3:(2.6,"#3a3936"),2:(1.6,MUTED),1:(.9,"#8a8984")}[len(d["phases"])] if emphasis else (.8,"#b9b8b3")
            ax.plot([x0,x1],[y0,y1],color=color,lw=width,solid_capstyle="round",zorder=1)
        elif d["kind"] == "switch":
            ax.plot([x0,x1],[y0,y1],color=INK,lw=1.4,ls="-" if d["closed"] else (0,(2,2)),zorder=2)
        elif emphasis:
            # Regulators/transformer have zero length: both terminals share coordinates.
            ax.scatter((x0+x1)/2,(y0+y1)/2,marker={"regulator":"s","transformer":"D"}[d["kind"]],s=95,
                       color=EQUIPMENT[d["kind"]],edgecolor=SURFACE,linewidth=1.5,zorder=4)


def plot_topology(study, highlight=("67",), ax=None):
    """Feeder drawn at the IEEE bus coordinates with NetworkX graph data."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    graph, buses = study["graph"], study["buses"].set_index("bus")
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    _style(ax,"IEEE123 — topologia (NetworkX, coordenadas originais)")
    _edges(ax,graph,pos)
    xy = np.array([pos[n] for n in graph])
    ax.scatter(xy[:,0],xy[:,1],s=10,color=INK,zorder=3,linewidths=0)
    source = graph.graph["source"]
    ax.scatter(*pos[source],marker="*",s=260,color=INK,zorder=5)
    caps = buses[buses.capacitor_kvar > 0]
    ax.scatter(caps.x,caps.y,marker="v",s=90,color=EQUIPMENT["capacitor"],edgecolor=SURFACE,linewidth=1.5,zorder=5)
    labels = {source: "150 subestação 4,16 kV"}
    for u,v,d in graph.edges(data=True):
        if d["kind"] in ("regulator","transformer") or (d["kind"]=="switch" and not d["closed"]):
            text = {"regulator": d["id"], "transformer": f'{d["id"]} {d.get("kv","")} kV',
                    "switch": f'{d["id"]} aberta'}[d["kind"]]
            labels[v] = text
    for bus in caps.index:
        labels[bus] = f"C{bus} {caps.at[bus,'capacitor_kvar']:.0f} kvar"
    for bus in highlight:
        ax.scatter(*pos[bus],s=220,facecolor="none",edgecolor=INK,linewidth=1.8,zorder=6)
        labels[bus] = f"{bus} (EVCS/BESS)"
    # Manual offsets where IEEE coordinates crowd the labels.
    offsets = {"610": (8,-16), "88": (-78,6), "94": (-70,-4), "92": (8,-14), "90": (8,4)}
    for bus,text in labels.items():
        ax.annotate(text,pos[bus],xytext=offsets.get(bus,(6,6)),textcoords="offset points",fontsize=8,color=INK,zorder=7,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    handles = [Line2D([],[],color="#3a3936",lw=2.6,label="linha trifásica"),
               Line2D([],[],color=MUTED,lw=1.6,label="linha bifásica"),
               Line2D([],[],color="#8a8984",lw=.9,label="linha monofásica"),
               Line2D([],[],color=INK,lw=1.4,label="chave fechada"),
               Line2D([],[],color=INK,lw=1.4,ls=(0,(2,2)),label="chave aberta"),
               Line2D([],[],marker="s",ls="",color=EQUIPMENT["regulator"],ms=9,label="regulador de tensão"),
               Line2D([],[],marker="D",ls="",color=EQUIPMENT["transformer"],ms=8,label="transformador 4,16/0,48 kV"),
               Line2D([],[],marker="v",ls="",color=EQUIPMENT["capacitor"],ms=9,label="banco de capacitores"),
               Line2D([],[],marker="*",ls="",color=INK,ms=13,label="subestação (fonte)")]
    ax.legend(handles=handles,loc="lower left",fontsize=8,frameon=False,ncol=3)
    return ax


def plot_voltage_map(study, time=None, ax=None):
    """Minimum phase voltage of each bus on the topology (diverging around 1.0 pu)."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    time = study["peak"] if time is None else time
    graph = study["graph"]
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    vmin = voltages_at(study,time).groupby("bus").v_pu.min()
    ax = ax or plt.subplots(figsize=(11,8.5),layout="constrained")[1]
    _style(ax,f"Tensão mínima por barra (pu) — {time:%H:%M}")
    _edges(ax,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("v",["#e34948","#f0efec","#2a78d6"])
    norm = TwoSlopeNorm(vcenter=1.,vmin=min(V_LIMITS[0],vmin.min()),vmax=max(V_LIMITS[1],vmin.max()))
    xy = np.array([pos[b] for b in vmin.index])
    points = ax.scatter(xy[:,0],xy[:,1],c=vmin.to_numpy(),cmap=cmap,norm=norm,s=46,edgecolor=MUTED,linewidth=.6,zorder=3)
    bar = plt.colorbar(points,ax=ax,shrink=.6,pad=.01)
    bar.set_label("V mín. entre fases (pu)",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for bus in (vmin.idxmin(),vmin.idxmax()):
        ax.annotate(f"{bus}: {vmin[bus]:.3f} pu",pos[bus],xytext=(6,-12),textcoords="offset points",fontsize=8,color=INK,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    return ax


def plot_voltage_profile(study, times=None):
    """Voltage vs. electrical distance from the substation, drawn along each feeder branch."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    times = times or (study["peak"],study["valley"])
    fig,axes = plt.subplots(1,len(times),figsize=(6.5*len(times),4.8),sharey=True,layout="constrained")
    axes = np.atleast_1d(axes)
    mult = dict(zip(study["flow"]["source"].time,study["multipliers"]))
    for ax,time in zip(axes,times):
        _style(ax,f"{time:%H:%M} — carga {mult[time]:.0%} da nominal")
        v = voltages_at(study,time).set_index(["bus","phase"])
        for phase,style in PHASE_STYLE.items():
            part = v.xs(phase,level="phase")
            for bus,row in part.iterrows():
                if pd.notna(row.parent) and (row.parent,phase) in v.index:
                    ax.plot([v.at[(row.parent,phase),"distance_km"],row.distance_km],
                            [v.at[(row.parent,phase),"v_pu"],row.v_pu],color=style["color"],lw=1.2,alpha=.85)
            ax.scatter(part.distance_km,part.v_pu,s=18,color=style["color"],marker=style["marker"],
                       edgecolor=SURFACE,linewidth=.5,zorder=3)
        for limit in V_LIMITS:
            ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
            ax.annotate(f"limite {limit:.2f} pu",(1,limit),xycoords=("axes fraction","data"),xytext=(-2,3),
                        textcoords="offset points",fontsize=8,color=MUTED,ha="right")
        # Vertical steps are regulator boosts (zero length, so same distance).
        for u,w,d in study["graph"].edges(data=True):
            if d["kind"] == "regulator" and len(d["phases"]) == 3:
                lv = w if w.endswith("r") else u
                at = v.xs(lv,level="bus")
                ax.annotate(d["id"],(at.distance_km.iloc[0],at.v_pu.max()),xytext=(4,6),textcoords="offset points",
                            fontsize=8,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
        ax.set_xlabel("distância elétrica da subestação (km)",color=MUTED)
    axes[0].set_ylabel("tensão (pu)",color=MUTED)
    fig.legend(handles=[Line2D([],[],color=s["color"],marker=s["marker"],lw=1.2,label=f"fase {p}")
                        for p,s in PHASE_STYLE.items()],frameon=False,fontsize=9,loc="outside lower center",ncol=3)
    return fig


def plot_daily(study):
    """Hourly voltage envelope per phase and substation P/Q (same unit scale, one axis each panel)."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    flow = study["flow"]
    fig,(ax1,ax2) = plt.subplots(1,2,figsize=(13,4.6),layout="constrained")
    v = feeder_voltages(study)
    steps = len(flow["source"])
    hours = np.arange(steps)*24/steps  # time of day, any resolution
    every = max(1,steps//24)           # one marker per hour
    _style(ax1,"Faixa de tensão por fase ao longo do dia")
    for phase,style in PHASE_STYLE.items():
        g = v[v.phase==phase].groupby("time").v_pu
        ax1.fill_between(hours,g.min(),g.max(),color=style["color"],alpha=.06,linewidth=0)
        ax1.plot(hours,g.min(),color=style["color"],marker=style["marker"],ms=4,markevery=every,lw=2,label=f"fase {phase} — mín.")
        ax1.plot(hours,g.max(),color=style["color"],lw=1.2,ls=(0,(3,2)))
    for limit in V_LIMITS:
        ax1.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax1.set_xlabel("hora",color=MUTED); ax1.set_ylabel("tensão (pu)",color=MUTED)
    ax1.legend(handles=[*ax1.get_lines()[0:6:2],Line2D([],[],color=MUTED,lw=1.2,ls=(0,(3,2)),label="máx. (tracejado)")],
               frameon=False,fontsize=8,loc="upper center",bbox_to_anchor=(.5,-.16),ncol=4)
    _style(ax2,"Potência na subestação")
    ax2.plot(hours,flow["source"].p_kw,color="#2a78d6",lw=2,marker="o",ms=4,markevery=every,label="P (kW)")
    ax2.plot(hours,flow["source"].q_kvar,color="#eb6834",lw=2,marker="s",ms=4,markevery=every,label="Q (kvar)")
    ax2.set_xlabel("hora",color=MUTED); ax2.set_ylabel("kW / kvar",color=MUTED)
    ax2.legend(frameon=False,fontsize=9)
    return fig


def plot_linecodes(table=None):
    """Positive-sequence R, X (and L = X/ω) and C per IEEE configuration."""
    import matplotlib.pyplot as plt
    table = linecode_table() if table is None else table
    fig,(ax1,ax2) = plt.subplots(1,2,figsize=(13,4.2),layout="constrained")
    x = np.arange(len(table))
    names = [f"{c}\n{p}φ" for c,p in zip(table.linecode,table.phases)]
    _style(ax1,"Impedância de sequência positiva por configuração")
    ax1.bar(x-.2,table.r1_ohm_km,.38,color="#2a78d6",label="R1 (Ω/km)")
    ax1.bar(x+.2,table.x1_ohm_km,.38,color="#eb6834",label="X1 (Ω/km)")
    ax1.set_xticks(x,names); ax1.set_ylabel("Ω/km",color=MUTED); ax1.legend(frameon=False,fontsize=9)
    _style(ax2,"Capacitância de sequência positiva")
    ax2.bar(x,table.c1_nf_km,.6,color="#2a78d6")
    for i,c in enumerate(table.c1_nf_km):
        ax2.annotate(f"{c:.0f}",(i,c),xytext=(0,3),textcoords="offset points",ha="center",fontsize=8,color=INK)
    ax2.set_xticks(x,names); ax2.set_ylabel("C1 (nF/km)",color=MUTED)
    return fig


def summary(study):
    flow, peak = study["flow"], study["peak"]
    v = feeder_voltages(study)
    b = flow["branches"]
    b = b[b.physical_phase]
    at_peak = v[v.time==peak]
    worst = at_peak.loc[at_peak.v_pu.idxmin()]
    loading = b[b.time==peak].groupby("line").loading_pct.max()
    return {"peak_time": str(peak), "valley_time": str(study["valley"]),
            "peak_source_kw": float(flow["source"].p_kw.max()), "peak_source_kvar": float(flow["source"].q_kvar[flow["source"].p_kw.idxmax()]),
            "daily_energy_kwh": float(flow["source"].p_kw.sum()),
            "peak_losses_kw": float(flow["branches"][flow["branches"].time==peak].loss_kw.sum()),
            "daily_losses_kwh": float(flow["branches"].loss_kw.sum()),
            "peak_vmin_pu": float(worst.v_pu), "peak_vmin_bus": f'{worst.bus}.{worst.phase}',
            "peak_vmax_pu": float(at_peak.v_pu.max()),
            "day_vmin_pu": float(v.v_pu.min()), "day_vmax_pu": float(v.v_pu.max()),
            "buses_below_0.95_at_peak": int((at_peak.groupby("bus").v_pu.min() < V_LIMITS[0]).sum()),
            "buses_above_1.05_any_hour": int(v[v.v_pu > V_LIMITS[1]].bus.nunique()),
            "max_line_loading_pct": float(loading.max()), "max_loaded_line": loading.idxmax(),
            "graph": {k: v for k,v in graph_metrics(study["graph"]).items() if k != "main_path"}}


def export_s0(output="results/s0", config_dir="configs"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    study = run_s0(config_dir)
    data = study["network"].equipment["ieee123_data"][0]
    peak = voltages_at(study,study["peak"]).pivot_table(index="bus",columns="phase",values="v_pu")
    peak.columns = [f"v_{p.lower()}_pu_peak" for p in peak.columns]
    study["buses"].merge(peak,left_on="bus",right_index=True,how="left").to_csv(output/"buses.csv",index=False)
    line_table(data).to_csv(output/"lines.csv",index=False)
    linecode_table(data).to_csv(output/"linecodes.csv",index=False)
    for name,table in electrical_inventory(data).items():
        table.to_csv(output/f"inventory_{name}.csv",index=name=="totals")
    for table in ("voltages","branches","source"):
        study["flow"][table].to_csv(output/f"{table}.csv",index=False)
    figures = {"topology": plot_topology(study).figure, "voltage_map": plot_voltage_map(study).figure,
               "voltage_profile": plot_voltage_profile(study), "daily": plot_daily(study),
               "linecodes": plot_linecodes(linecode_table(data))}
    for name,fig in figures.items():
        fig.savefig(output/f"{name}.png",dpi=200,facecolor=SURFACE)
        plt.close(fig)
    result = summary(study)
    (output/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n",encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",default="results/s0")
    parser.add_argument("--configs",default="configs")
    args = parser.parse_args()
    print(json.dumps(export_s0(args.output,args.configs),indent=2,default=str))


if __name__ == "__main__":
    main()
