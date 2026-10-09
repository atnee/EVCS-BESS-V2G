"""Impact of a scenario on the network against the base case S0 (S1 vs S0 now; S2/S3 vs S0 later).

Two views of the voltage change:
- with regulators acting (what the feeder really shows: taps react and partly hide the new load);
- with the regulator taps locked at the S0 values (`frozen`): the effect of the new load/injection alone.
All tables come from PandapowerSolver.solve outputs (voltages, branches, source).
"""
import numpy as np
import pandas as pd
from integration.s0_study import _style, _edges, marker_scale, INK, MUTED, SURFACE, V_LIMITS

REGULATED, FROZEN = "#2a78d6", "#d03b3b"
BASE_GRAY = "#b9b8b3"


def voltage_change(base, case, buses, slack):
    """case - base voltage per time, bus and phase (negative = voltage fell), with distance and coordinates."""
    a, b = base["voltages"], case["voltages"]
    d = a.merge(b,on=["time","bus","phase"],suffixes=("_s0","_case"))
    d = d[d.bus != slack]
    d["dv_pu"] = d.v_pu_case-d.v_pu_s0
    return d.merge(buses[["bus","distance_km","x","y"]],on="bus")


def peak_step(case):
    source = case["source"]
    return source.time.iloc[int(source.p_kw.idxmax())]


def tap_operations(flow):
    """Tap changes over the day per regulator (sum of |tap step| between consecutive steps)."""
    taps = flow["source"].filter(like="tap_")
    return {c.removeprefix("tap_"): int(taps[c].diff().abs().sum()) for c in taps}


def impact_summary(base, case, buses, slack, frozen=None, drop_threshold=.01):
    """Numbers for reports: voltage change (regulated and, if given, frozen taps), source peak, losses, taps."""
    out = {}
    views = {"regulated": case} | ({"frozen_taps": frozen} if frozen is not None else {})
    for name,flow in views.items():
        d = voltage_change(base,flow,buses,slack)
        worst = d.loc[d.dv_pu.idxmin()]
        per_bus = d.groupby("bus").dv_pu.min()
        out[name] = {"max_drop_pct": float(-d.dv_pu.min()*100), "max_drop_at": f"{worst.bus}.{worst.phase} {worst.time}",
                     "max_rise_pct": float(d.dv_pu.max()*100),
                     "buses_drop_over_1pct": int((per_bus < -drop_threshold).sum()),
                     "median_change_pct": float(d.dv_pu.median()*100),
                     "v_min_pu": float(flow["voltages"].query("bus != @slack").v_pu.min())}
    src0, src1 = base["source"], case["source"]
    times = pd.DatetimeIndex(pd.to_datetime(src1.time))
    dt_h = (times[1]-times[0]).total_seconds()/3600 if len(times) > 1 else 1.  # loss_kw is a mean power per step
    out.update(peak_source_kw_s0=float(src0.p_kw.max()), peak_source_kw=float(src1.p_kw.max()),
               losses_kwh_s0=float(base["branches"].loss_kw.sum()*dt_h), losses_kwh=float(case["branches"].loss_kw.sum()*dt_h),
               v_min_pu_s0=float(base["voltages"].query("bus != @slack").v_pu.min()),
               tap_operations_s0=tap_operations(base), tap_operations=tap_operations(case))
    return out


def plot_impact(base, case, buses, graph, slack, frozen=None, sites=None, label="S1", demand_kw=None):
    """Four panels: voltage change vs distance at the case peak, map of the change, worst change per step,
    regulator taps over the day. sites: {bus: label} to mark (stations, BESS...). demand_kw: optional
    per-step power of the new assets, drawn under the intraday panel."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    peak = peak_step(case)
    fig = plt.figure(figsize=(15,12),layout="constrained")
    grid = fig.add_gridspec(3,2,height_ratios=(1.15,1,1))
    ax_dist, ax_map = fig.add_subplot(grid[0,0]), fig.add_subplot(grid[0:2,1])
    ax_day, ax_tap = fig.add_subplot(grid[1,0]), fig.add_subplot(grid[2,:])
    views = [("com reguladores atuando",case,REGULATED)]
    if frozen is not None:
        views.append((f"taps travados no S0 (só o efeito do {label})",frozen,FROZEN))
    k = marker_scale(graph)

    _style(ax_dist,f"Variação de tensão {label} − S0 por barra, ponta {peak:%H:%M}")
    for name,flow,color in views:
        d = voltage_change(base,flow,buses,slack)
        at = d[d.time==peak].groupby(["bus","distance_km"]).dv_pu.min().reset_index()
        ax_dist.scatter(at.distance_km,at.dv_pu*100,s=14*k+4,color=color,alpha=.75,label=name,linewidths=0)
    ax_dist.axhline(0,color=MUTED,lw=1)
    if sites:
        dist = buses.set_index("bus").distance_km
        for bus,text in sites.items():
            ax_dist.axvline(dist[bus],color=INK,lw=.8,ls=(0,(2,3)),alpha=.5)
    ax_dist.set_xlabel("distância elétrica da subestação (km)",color=MUTED)
    ax_dist.set_ylabel("variação de tensão (%)",color=MUTED)
    ax_dist.legend(frameon=False,fontsize=9,loc="lower left")

    shown = views[-1]
    d = voltage_change(base,shown[1],buses,slack)
    drop = -d[d.time==peak].groupby("bus").dv_pu.min()*100
    pos = {n: data["xy"] for n,data in graph.nodes(data=True)}
    _style(ax_map,f"Queda de tensão na ponta {peak:%H:%M} — {shown[0]}")
    _edges(ax_map,graph,pos,emphasis=False)
    cmap = LinearSegmentedColormap.from_list("drop",["#f0efec","#ec835a","#d03b3b","#7a1f1f"])
    xy = np.array([pos[b] for b in drop.index])
    points = ax_map.scatter(xy[:,0],xy[:,1],c=drop.clip(lower=0).to_numpy(),cmap=cmap,vmin=0,vmax=max(drop.max(),1e-6),
                            s=50*k,edgecolor=MUTED,linewidth=.4*k,zorder=3)
    bar = plt.colorbar(points,ax=ax_map,shrink=.55,pad=.01)
    bar.set_label("queda de tensão (%)",color=MUTED); bar.ax.tick_params(colors=MUTED,labelsize=8)
    for bus,text in (sites or {}).items():
        ax_map.scatter(*pos[bus],marker="P",s=170,color=INK,edgecolor=SURFACE,linewidth=1.2,zorder=5)
    worst = drop.idxmax()
    ax_map.annotate(f"maior queda: {worst}, {drop[worst]:.2f} %",pos[worst],xytext=(-10,-18),textcoords="offset points",
                    ha="right",fontsize=9,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85))
    ax_map.set_aspect("equal"); ax_map.set_xticks([]); ax_map.set_yticks([]); ax_map.grid(False)

    _style(ax_day,f"Maior queda de tensão causada pelo {label} em cada passo"+
           (" (azul inclui a banda morta dos reguladores)" if frozen is not None else ""))
    steps = len(case["source"])
    hours = np.arange(steps+1)*24/steps  # step edges: the last value holds until 24 h
    last = lambda y: np.append(np.asarray(y,float),np.asarray(y,float)[-1])
    for name,flow,color in views:
        worst_step = -voltage_change(base,flow,buses,slack).groupby("time").dv_pu.min().to_numpy()*100
        ax_day.step(hours,last(worst_step),where="post",color=color,lw=2,label=name)
    ax_day.set_ylabel("queda máxima (%)",color=MUTED)
    if demand_kw is not None:
        twin = ax_day.twinx()
        twin.fill_between(hours,last(demand_kw),step="post",color=BASE_GRAY,alpha=.35,linewidth=0)
        twin.set_ylabel(f"potência do {label} (kW)",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8)
        twin.set_ylim(0,max(np.max(demand_kw),1)*2.2)
        for side in ("top",):
            twin.spines[side].set_visible(False)
        ax_day.set_zorder(twin.get_zorder()+1); ax_day.patch.set_visible(False)
    ax_day.set_xlim(0,24); ax_day.set_xticks(range(0,25,3)); ax_day.set_xlabel("hora do dia",color=MUTED)
    ax_day.legend(frameon=False,fontsize=9,loc="upper left")

    ops0, ops1 = tap_operations(base), tap_operations(case)
    _style(ax_tap,"Taps dos reguladores ao longo do dia (tracejado: S0) — operações no dia: "+
           ", ".join(f"{n} {ops0[n]}→{ops1[n]}" for n in ops1))
    taps0, taps1 = base["source"].filter(like="tap_"), case["source"].filter(like="tap_")
    colors = ["#2a78d6","#eb6834","#1baf7a","#4a3aa7","#c2409a","#7a7a7a"]
    for color,col in zip(colors,taps1):
        ax_tap.step(hours,last(taps0[col]),where="post",color=color,lw=1.3,ls=(0,(3,2)),alpha=.8)
        ax_tap.step(hours,last(taps1[col]),where="post",color=color,lw=2.2,label=col.removeprefix("tap_"))
    ax_tap.set_ylabel("posição do tap",color=MUTED)
    ax_tap.set_xlim(0,24); ax_tap.set_xticks(range(0,25,3)); ax_tap.set_xlabel("hora do dia",color=MUTED)
    ax_tap.legend(frameon=False,fontsize=9,ncol=len(colors),loc="upper left")
    return fig
