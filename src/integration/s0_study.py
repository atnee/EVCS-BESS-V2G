"""S0 baseline study: feeder without DER — topology, line parameters and voltage profile.

python -m integration.s0_study                      # feeder of configs/network.yaml (IEEE 8500) -> results/ieee8500/s0/
python -m integration.s0_study --feeder ieee123     # -> results/ieee123/s0/
"""
from pathlib import Path
import argparse
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import make_profile, read_parameters
from integration.scenarios import integrated_grid, study_feeder
from network.feeder_loader import load_feeder
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


def load_curve(config_dir, steps, feeder):
    """<feeder>.yaml hourly multipliers at `steps` per day: linear between hour centres, periodic."""
    hourly = np.asarray(read_parameters(Path(config_dir)/f"{feeder}.yaml").get("load_multipliers",[1.]*24),float)
    if steps == len(hourly):
        return hourly
    if len(hourly) != 24:
        raise ValueError("Intraday resampling needs 24 hourly load multipliers")
    return np.interp((np.arange(steps)+.5)*24/steps,np.arange(24)+.5,hourly,period=24)


def run_s0(config_dir="configs", resolution_min=None, feeder=None):
    """Daily S0 power flow (feeder loads only, no DER) with the <feeder>.yaml load curve.
    resolution_min=None keeps the integration grid; e.g. 15 gives 96 intraday steps."""
    grid = integrated_grid(config_dir)
    if resolution_min:
        grid = dataclasses.replace(grid,steps=1440//resolution_min,dt_h=resolution_min/60)
    feeder = feeder or study_feeder(config_dir)
    multipliers = load_curve(config_dir,grid.steps,feeder)
    network = load_feeder(feeder)
    no_der = make_profile(grid,"no-DER",network.slack_bus,"ABC",np.zeros(grid.steps))
    flow = PandapowerSolver(multipliers).solve(network,no_der)
    graph = feeder_graph(network.equipment["feeder_data"][0])
    buses = bus_table(graph)
    hours = flow["source"].time
    peak, valley = int(flow["source"].p_kw.idxmax()), int(flow["source"].p_kw.idxmin())
    return dict(flow=flow,graph=graph,buses=buses,network=network,multipliers=multipliers,
                peak=hours[peak],valley=hours[valley],feeder=feeder)


def feeder_voltages(study):
    """Voltages without the ideal source bus (fixed voltage upstream of the first regulator/transformer)."""
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


def marker_scale(graph):
    """1 on the IEEE123; smaller markers on feeders with many buses (IEEE8500: ~0.25)."""
    return float(min(1.,np.sqrt(150/max(graph.number_of_nodes(),1))))


def _edges(ax, graph, pos, emphasis=True):
    """Lines drawn by phase count; equipment and switches on top."""
    from matplotlib.collections import LineCollection
    k = marker_scale(graph)
    groups = {}
    for u,v,d in graph.edges(data=True):
        if d["kind"] == "line":
            style = {3:(2.6,"#3a3936"),2:(1.6,MUTED),1:(.9,"#8a8984")}[len(d["phases"])] if emphasis else (.8,"#b9b8b3")
            groups.setdefault((max(style[0]*k,.5),style[1],"-",1),[]).append([pos[u],pos[v]])
        elif d["kind"] == "switch":
            groups.setdefault((1.4*max(k,.5),INK,"-" if d["closed"] else (0,(2,2)),2),[]).append([pos[u],pos[v]])
        elif emphasis:
            # Regulators/transformer have zero length: both terminals share coordinates.
            (x0,y0),(x1,y1) = pos[u],pos[v]
            ax.scatter((x0+x1)/2,(y0+y1)/2,marker={"regulator":"s","transformer":"D"}[d["kind"]],s=95,
                       color=EQUIPMENT[d["kind"]],edgecolor=SURFACE,linewidth=1.5,zorder=4)
    for (width,color,style,z),segments in groups.items():
        ax.add_collection(LineCollection(segments,linewidths=width,colors=color,linestyles=style,capstyle="round",zorder=z))
    ax.autoscale_view()


def new_axes(figsize=(10,6)):
    """One figure with a single plot (every exported figure is a single plot)."""
    import matplotlib.pyplot as plt
    plt.rcParams["figure.max_open_warning"] = 0  # figure sets are built first, then saved and closed
    return plt.subplots(figsize=figsize,layout="constrained")[1]


def save_figures(figures, folder, dpi=200, clean=False):
    """Save {name: Figure} as folder/name.png, one file per plot, and close them. clean=True removes
    other PNGs in the folder (left by older versions) so it only holds current figures."""
    import matplotlib.pyplot as plt
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
    if clean:
        for old in folder.glob("*.png"):
            if old.stem not in figures:
                old.unlink()
    for name,fig in figures.items():
        fig.savefig(folder/f"{name}.png",dpi=dpi,facecolor=SURFACE)
        plt.close(fig)


def _map_frame(ax):
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)


def plot_topology(study, highlight=None, ax=None):
    """Feeder drawn at the IEEE bus coordinates with NetworkX graph data.
    highlight: buses to circle (default: bus 67, the EVCS/BESS bus of the IEEE123 integration)."""
    from matplotlib.lines import Line2D
    graph, buses = study["graph"], study["buses"].set_index("bus")
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    ax = ax or new_axes((11,8.5))
    k = marker_scale(graph)
    highlight = [b for b in (("67",) if highlight is None else highlight) if b in graph] if graph.graph["name"] == "IEEE 123" or highlight else []
    _style(ax,f'{graph.graph["name"]} feeder topology')
    _edges(ax,graph,pos)
    xy = np.array([pos[n] for n in graph])
    ax.scatter(xy[:,0],xy[:,1],s=10*k,color=INK,zorder=3,linewidths=0)
    source = graph.graph["source"]
    ax.scatter(*pos[source],marker="*",s=260,color=INK,zorder=5)
    caps = buses[buses.capacitor_kvar > 0]
    ax.scatter(caps.x,caps.y,marker="v",s=90,color=EQUIPMENT["capacitor"],edgecolor=SURFACE,linewidth=1.5,zorder=5)
    labels = {source: f'{source} substation {graph.nodes[source]["vn_kv"]:g} kV'}
    transformer = next((d for *_,d in graph.edges(data=True) if d["kind"]=="transformer"),{})
    for u,v,d in graph.edges(data=True):
        if d["kind"] in ("regulator","transformer") or (d["kind"]=="switch" and not d["closed"]):
            text = {"regulator": d["id"], "transformer": f'{d["id"]} {d.get("kv","").replace(".0/","/")} kV',
                    "switch": f'{d["id"]} open'}[d["kind"]]
            labels[v] = text
    for bus in caps.index:
        labels[bus] = f"C{bus} {caps.at[bus,'capacitor_kvar']:.0f} kvar"
    for bus in highlight:
        ax.scatter(*pos[bus],s=220,facecolor="none",edgecolor=INK,linewidth=1.8,zorder=6)
        labels[bus] = f"{bus} (EVCS/BESS)"
    # Manual offsets where IEEE coordinates crowd the labels.
    offsets = {"610": (8,-16), "88": (-78,6), "94": (-70,-4), "92": (8,-14), "90": (8,4)}
    # Labels closer than 1.5 % of the drawing (substation, transformer and regulator terminals) share one box.
    tol = .015*float(np.ptp(xy,axis=0).max())
    merged = []
    for bus,text in labels.items():
        near = next((m for m in merged if np.hypot(*(np.asarray(pos[m[0]])-pos[bus])) <= tol),None)
        if near:
            near[1] += "\n"+text
        else:
            merged.append([bus,text])
    for bus,text in merged:
        ax.annotate(text,pos[bus],xytext=offsets.get(bus,(6,6)),textcoords="offset points",fontsize=8,color=INK,zorder=7,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    _map_frame(ax)
    handles = [Line2D([],[],color="#3a3936",lw=2.6,label="three-phase line"),
               Line2D([],[],color=MUTED,lw=1.6,label="two-phase line"),
               Line2D([],[],color="#8a8984",lw=.9,label="single-phase line"),
               Line2D([],[],color=INK,lw=1.4,label="closed switch"),
               Line2D([],[],color=INK,lw=1.4,ls=(0,(2,2)),label="open switch"),
               Line2D([],[],marker="s",ls="",color=EQUIPMENT["regulator"],ms=9,label="voltage regulator"),
               Line2D([],[],marker="D",ls="",color=EQUIPMENT["transformer"],ms=8,
                      label=f'transformer {transformer.get("kv","").replace(".0/","/")} kV'),
               Line2D([],[],marker="v",ls="",color=EQUIPMENT["capacitor"],ms=9,label="capacitor bank"),
               Line2D([],[],marker="*",ls="",color=INK,ms=13,label="substation (source)")]
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
    ax = ax or new_axes((11,8.5))
    _style(ax,f"Minimum bus voltage at {time:%H:%M} (pu)")
    _edges(ax,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("v",["#e34948","#f0efec","#2a78d6"])
    norm = TwoSlopeNorm(vcenter=1.,vmin=min(V_LIMITS[0],vmin.min()),vmax=max(V_LIMITS[1],vmin.max()))
    xy = np.array([pos[b] for b in vmin.index])
    k = marker_scale(graph)
    points = ax.scatter(xy[:,0],xy[:,1],c=vmin.to_numpy(),cmap=cmap,norm=norm,s=46*k,edgecolor=MUTED,linewidth=.6*k,zorder=3)
    bar = plt.colorbar(points,ax=ax,shrink=.6,pad=.01)
    bar.set_label("minimum phase voltage (pu)",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for bus in (vmin.idxmin(),vmin.idxmax()):
        ax.annotate(f"{bus}: {vmin[bus]:.3f} pu",pos[bus],xytext=(6,-12),textcoords="offset points",fontsize=8,color=INK,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    _map_frame(ax)
    return ax


def plot_voltage_profile(study, time=None, ax=None):
    """Voltage vs. electrical distance from the substation at one step, drawn along each feeder branch."""
    from matplotlib.collections import LineCollection
    time = study["peak"] if time is None else time
    ax = ax or new_axes((10,5.4))
    mult = dict(zip(study["flow"]["source"].time,study["multipliers"]))
    k = marker_scale(study["graph"])
    _style(ax,f"Voltage along the feeder at {time:%H:%M} (load {mult[time]:.0%} of nominal)")
    v = voltages_at(study,time).set_index(["bus","phase"])
    for phase,style in PHASE_STYLE.items():
        part = v.xs(phase,level="phase")
        parents = [(p,phase) in v.index for p in part.parent]
        child = part[parents]
        parent = v.loc[[(p,phase) for p in child.parent]]
        segments = np.stack([np.column_stack([parent.distance_km,parent.v_pu]),
                             np.column_stack([child.distance_km,child.v_pu])],axis=1)
        ax.add_collection(LineCollection(segments,colors=style["color"],linewidths=1.2*max(k,.6),alpha=.85))
        ax.scatter(part.distance_km,part.v_pu,s=18*k,color=style["color"],marker=style["marker"],
                   edgecolor=SURFACE,linewidth=.5*k,zorder=3,label=f"phase {phase}")
    ax.autoscale_view()
    for limit in V_LIMITS:
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
        ax.annotate(f"limit {limit:.2f} pu",(1,limit),xycoords=("axes fraction","data"),xytext=(-2,3),
                    textcoords="offset points",fontsize=8,color=MUTED,ha="right")
    # Vertical steps are regulator boosts (zero length, so same distance).
    for u,w,d in study["graph"].edges(data=True):
        if d["kind"] == "regulator" and len(d["phases"]) == 3:
            at = v.xs(d["lv_bus"],level="bus")
            ax.annotate(d["id"],(at.distance_km.iloc[0],at.v_pu.max()),xytext=(4,6),textcoords="offset points",
                        fontsize=8,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax.set_xlabel("electrical distance from the substation (km)",color=MUTED)
    ax.set_ylabel("voltage (pu)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="lower left",markerscale=2)
    return ax


def plot_voltage_envelope(study, ax=None):
    """Lowest and highest voltage per phase over the day."""
    from matplotlib.lines import Line2D
    flow = study["flow"]
    ax = ax or new_axes((10,5))
    v = feeder_voltages(study)
    steps = len(flow["source"])
    hours = np.arange(steps)*24/steps  # time of day, any resolution
    every = max(1,steps//24)           # one marker per hour
    _style(ax,"Voltage range per phase over the day")
    for phase,style in PHASE_STYLE.items():
        g = v[v.phase==phase].groupby("time").v_pu
        ax.fill_between(hours,g.min(),g.max(),color=style["color"],alpha=.06,linewidth=0)
        ax.plot(hours,g.min(),color=style["color"],marker=style["marker"],ms=4,markevery=every,lw=2,label=f"phase {phase}, minimum")
        ax.plot(hours,g.max(),color=style["color"],lw=1.2,ls=(0,(3,2)))
    for limit in V_LIMITS:
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3))
    ax.set_xlabel("hour of day",color=MUTED); ax.set_ylabel("voltage (pu)",color=MUTED)
    ax.legend(handles=[*ax.get_lines()[0:6:2],Line2D([],[],color=MUTED,lw=1.2,ls=(0,(3,2)),label="maximum (dashed)")],
              frameon=False,fontsize=8,loc="lower left",ncol=2)
    return ax


def plot_substation_power(study, ax=None):
    """Active and reactive power at the substation over the day."""
    flow = study["flow"]
    ax = ax or new_axes((10,5))
    steps = len(flow["source"])
    hours = np.arange(steps)*24/steps
    every = max(1,steps//24)
    _style(ax,"Substation power over the day")
    ax.plot(hours,flow["source"].p_kw,color="#2a78d6",lw=2,marker="o",ms=4,markevery=every,label="P (kW)")
    ax.plot(hours,flow["source"].q_kvar,color="#eb6834",lw=2,marker="s",ms=4,markevery=every,label="Q (kvar)")
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3))
    ax.set_xlabel("hour of day",color=MUTED); ax.set_ylabel("kW / kvar",color=MUTED)
    ax.legend(frameon=False,fontsize=9)
    return ax


def _linecode_axis(table, top):
    table = linecode_table() if table is None else table
    if len(table) > top:
        table = table.nlargest(top,"total_km").reset_index(drop=True)
    names = [f"{c}\n{p}φ" for c,p in zip(table.linecode,table.phases)]
    rotation = dict(rotation=60,ha="right",fontsize=7) if max(map(len,table.linecode)) > 6 else {}
    return table,np.arange(len(table)),names,rotation


def plot_linecode_impedance(table=None, top=15, ax=None):
    """Positive-sequence R and X per line configuration (the `top` longest in use)."""
    table,x,names,rotation = _linecode_axis(table,top)
    ax = ax or new_axes((11,5))
    _style(ax,"Positive-sequence impedance per line configuration")
    ax.bar(x-.2,table.r1_ohm_km,.38,color="#2a78d6",label="R1 (Ω/km)")
    ax.bar(x+.2,table.x1_ohm_km,.38,color="#eb6834",label="X1 (Ω/km)")
    ax.set_xticks(x,names,**rotation); ax.set_ylabel("Ω/km",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


def plot_linecode_capacitance(table=None, top=15, ax=None):
    """Positive-sequence capacitance per line configuration."""
    table,x,names,rotation = _linecode_axis(table,top)
    ax = ax or new_axes((11,5))
    _style(ax,"Positive-sequence capacitance per line configuration")
    ax.bar(x,table.c1_nf_km,.6,color="#2a78d6")
    for i,c in enumerate(table.c1_nf_km):
        ax.annotate(f"{c:.0f}",(i,c),xytext=(0,3),textcoords="offset points",ha="center",fontsize=8,color=INK)
    ax.set_xticks(x,names,**rotation); ax.set_ylabel("C1 (nF/km)",color=MUTED)
    return ax


def s0_figures(study, data):
    """Every S0 figure, one plot each: {file name: Figure}."""
    codes = linecode_table(data)
    return {"network_topology": plot_topology(study).figure,
            "voltage_map_peak": plot_voltage_map(study).figure,
            "voltage_profile_peak": plot_voltage_profile(study,study["peak"]).figure,
            "voltage_profile_valley": plot_voltage_profile(study,study["valley"]).figure,
            "voltage_range_day": plot_voltage_envelope(study).figure,
            "demand_substation_power_day": plot_substation_power(study).figure,
            "lines_impedance": plot_linecode_impedance(codes).figure,
            "lines_capacitance": plot_linecode_capacitance(codes).figure}


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


def export_s0(output=None, config_dir="configs", feeder=None):
    """Writes to `output` (default results/<feeder>/s0); feeder default from configs/network.yaml."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    feeder = feeder or study_feeder(config_dir)
    output = Path(output or Path("results")/feeder/"s0"); output.mkdir(parents=True,exist_ok=True)
    study = run_s0(config_dir,feeder=feeder)
    data = study["network"].equipment["feeder_data"][0]
    peak = voltages_at(study,study["peak"]).pivot_table(index="bus",columns="phase",values="v_pu")
    peak.columns = [f"v_{p.lower()}_pu_peak" for p in peak.columns]
    tables = output/"dados"; tables.mkdir(exist_ok=True)  # CSV tables apart from the figures
    study["buses"].merge(peak,left_on="bus",right_index=True,how="left").to_csv(tables/"buses.csv",index=False)
    line_table(data).to_csv(tables/"lines.csv",index=False)
    linecode_table(data).to_csv(tables/"linecodes.csv",index=False)
    for name,table in electrical_inventory(data).items():
        table.to_csv(tables/f"inventory_{name}.csv",index=name=="totals")
    for table in ("voltages","branches","source"):
        study["flow"][table].to_csv(tables/f"{table}.csv",index=False)
    save_figures(s0_figures(study,data),output,clean=True)
    result = summary(study)
    (output/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n",encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",help="default: results/<feeder>/s0")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--feeder",help="ieee8500 or ieee123 (default: configs/network.yaml)")
    args = parser.parse_args()
    print(json.dumps(export_s0(args.output,args.configs,args.feeder),indent=2,default=str))


if __name__ == "__main__":
    main()
