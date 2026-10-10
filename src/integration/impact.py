"""Impact of a scenario on the network against the base case S0 (S1 vs S0 now; S2/S3 vs S0 later).

Two views of the voltage change:
- with regulators acting (what the feeder really shows: taps react and partly hide the new load);
- with the regulator taps locked at the S0 values (`frozen`): the effect of the new load/injection alone.
All tables come from PandapowerSolver.solve outputs (voltages, branches, source).
"""
from pathlib import Path
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


SITE_STYLE = {"dc": dict(color="#4a3aa7",marker="H",s=260,label="hub DC"),
              "ac": dict(color="#eb6834",marker="P",s=200,label="eletroposto AC"),
              "host": dict(color="#1baf7a",marker="s",s=150,label="estação anfitriã V2G"),
              "bess": dict(color="#c2409a",marker="D",s=160,label="BESS")}
LOADING_BANDS = [(0,50,"< 50 %"),(50,80,"50–80 %"),(80,100,"80–100 %"),(100,1e9,"> 100 %")]
VOLTAGE_BANDS = [(0,.95,"< 0,95"),(.95,.97,"0,95–0,97"),(.97,1.03,"0,97–1,03"),(1.03,1.05,"1,03–1,05"),(1.05,9,"> 1,05")]


def flow_from_csv(folder, prefix=""):
    """Saved solver tables (voltages, branches, source) back as a flow dict, times parsed."""
    folder = Path(folder)
    flow = {name: pd.read_csv(folder/f"{prefix}{name}.csv") for name in ("voltages","branches","source")}
    for table in flow.values():
        table["time"] = pd.to_datetime(table.time)  # keeps the saved UTC offset (local time)
    return flow


def network_snapshot(flow, slack, time):
    """Minimum phase voltage per bus and highest phase loading per line/transformer at one step."""
    v = flow["voltages"]
    v = v[(v.time==time) & (v.bus!=slack)].groupby("bus").v_pu.min()
    b = flow["branches"]
    b = b[(b.time==time) & b.physical_phase]
    lines = b[b.element_type=="line"].groupby("line").loading_pct.max()
    trafos = b[b.element_type=="trafo"].groupby("line").loading_pct.max()
    return v, lines, trafos


def plot_network_state(base, case, buses, graph, slack, sites=None, label="S1", title=None):
    """S0 and the case side by side, each at its own peak and on the same scales: bus voltage map,
    line loading map (stations marked), equipment and most loaded lines, buses/lines per band.
    sites: {bus: 'dc' | 'ac' | 'host' | 'bess'}."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm, Normalize
    from matplotlib.lines import Line2D
    sites = sites or {}
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    segments = {d["id"]: (pos[u],pos[w]) for u,w,d in graph.edges(data=True) if d["kind"] == "line"}
    states = {}
    for name,flow in (("S0",base),(label,case)):
        peak = peak_step(flow)
        states[name] = (peak,*network_snapshot(flow,slack,peak),float(flow["source"].p_kw.max()))
    v_all = pd.concat([s[1] for s in states.values()])
    vnorm = TwoSlopeNorm(vcenter=1.,vmin=min(V_LIMITS[0]-.005,v_all.min()),vmax=max(V_LIMITS[1],v_all.max()))
    vcmap = LinearSegmentedColormap.from_list("v",["#b2182b","#e34948","#f0efec","#86b6ef","#2a78d6"])
    lnorm = Normalize(0,110)
    lcmap = LinearSegmentedColormap.from_list("load",["#dcdbd6","#f2c14e","#ec835a","#d03b3b","#7a1f1f"])
    k = marker_scale(graph)

    fig = plt.figure(figsize=(18,19),layout="constrained")
    gs = fig.add_gridspec(3,2,height_ratios=(1,1,.62))
    if title:
        fig.suptitle(title,fontsize=14,color=INK,x=.01,ha="left")

    def mark_sites(ax):
        for bus,kind in sites.items():
            st = SITE_STYLE.get(kind,SITE_STYLE["ac"])
            ax.scatter(*pos[bus],marker=st["marker"],s=st["s"],color=st["color"],edgecolor=SURFACE,linewidth=1.3,zorder=6)

    def frame(ax):
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)

    for col,(name,(peak,v,lines,trafos,p)) in enumerate(states.items()):
        ax = fig.add_subplot(gs[0,col])
        _style(ax,f"{name} — tensão mínima por barra, ponta {peak:%H:%M} ("+f"{p:,.0f}".replace(",",".")+" kW)")
        _edges(ax,graph,pos,emphasis=False)
        xy = np.array([pos[b] for b in v.index])
        pts = ax.scatter(xy[:,0],xy[:,1],c=v.to_numpy(),cmap=vcmap,norm=vnorm,s=46*k,edgecolor=MUTED,linewidth=.3*k,zorder=3)
        if name != "S0":
            mark_sites(ax)
        worst = v.idxmin()
        ax.annotate(f"mín. {v.min():.3f} pu",pos[worst],xytext=(8,-14),textcoords="offset points",fontsize=9,color=INK,
                    bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85),zorder=7)
        frame(ax)
        if col == 1:
            bar = fig.colorbar(pts,ax=ax,shrink=.7,pad=.01); bar.set_label("tensão (pu)",color=MUTED)

        ax = fig.add_subplot(gs[1,col])
        _style(ax,f"{name} — carregamento das linhas na ponta {peak:%H:%M}")
        ids = [i for i in lines.index if i in segments]
        load = lines.reindex(ids).to_numpy()
        order = np.argsort(load)  # most loaded drawn on top
        coll = LineCollection([segments[ids[i]] for i in order],colors=lcmap(lnorm(load[order])),
                              linewidths=.5+3.2*np.clip(load[order],0,110)/110,capstyle="round",zorder=2)
        ax.add_collection(coll); ax.autoscale_view()
        if name != "S0":
            mark_sites(ax)
        base_over = states["S0"][2][states["S0"][2] > 100].index if "S0" in states else []
        top = lines.drop(base_over,errors="ignore").idxmax()  # base-case overloads are not the stations' doing
        if top in segments:
            (x0,y0),(x1,y1) = segments[top]
            ax.annotate(f"{top}: {lines[top]:.0f} %",((x0+x1)/2,(y0+y1)/2),xytext=(10,10),textcoords="offset points",
                        fontsize=9,color=INK,bbox=dict(boxstyle="round,pad=.15",fc=SURFACE,ec="none",alpha=.85),zorder=7)
        frame(ax)
        if col == 1:
            sm = plt.cm.ScalarMappable(norm=lnorm,cmap=lcmap); sm.set_array([])
            bar = fig.colorbar(sm,ax=ax,shrink=.7,pad=.01); bar.set_label("carregamento (%)",color=MUTED)
            handles = [Line2D([],[],marker=SITE_STYLE[kd]["marker"],ls="",color=SITE_STYLE[kd]["color"],ms=11,
                              label=SITE_STYLE[kd]["label"]) for kd in dict.fromkeys(sites.values()) if kd in SITE_STYLE]
            if handles:
                ax.legend(handles=handles,loc="lower left",frameon=False,fontsize=9)

    (p0,v0,l0,t0,_),(p1,v1,l1,t1,_) = states.values()
    ax = fig.add_subplot(gs[2,0])
    _style(ax,"Carregamento na ponta: transformador, reguladores e linhas mais carregadas")
    preexisting = l0[l0 > 100].index
    top_lines = l1.drop(preexisting,errors="ignore").nlargest(4).index.tolist()
    names = list(t1.sort_values(ascending=False).index)+top_lines
    s0 = [t0.get(n,np.nan) if n in t1.index else l0.get(n,np.nan) for n in names]
    s1 = [t1.get(n,np.nan) if n in t1.index else l1.get(n,np.nan) for n in names]
    y = np.arange(len(names))
    ax.barh(y+.2,s0,.38,color=BASE_GRAY,label="S0")
    ax.barh(y-.2,s1,.38,color=REGULATED,label=label)
    for i,val in enumerate(s1):
        ax.annotate(f"{val:.0f} %",(val,i-.2),xytext=(3,0),textcoords="offset points",va="center",fontsize=8,color=INK)
    ax.axvline(100,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_yticks(y,[f"{n} (linha)" if n in top_lines else n for n in names]); ax.invert_yaxis()
    if len(preexisting):
        ax.annotate("sem as linhas já acima de 100 % no S0: "+", ".join(preexisting),(0,-.12),xycoords="axes fraction",
                    fontsize=8,color=MUTED)
    ax.set_xlabel("carregamento (%)",color=MUTED); ax.legend(frameon=False,fontsize=9,loc="center right")

    ax = fig.add_subplot(gs[2,1])
    _style(ax,"Quantas barras e linhas em cada faixa (na ponta)")
    groups = [("tensão "+lab,(v0>=lo)&(v0<hi),(v1>=lo)&(v1<hi)) for lo,hi,lab in VOLTAGE_BANDS] + \
             [("linhas "+lab,(l0>=lo)&(l0<hi),(l1>=lo)&(l1<hi)) for lo,hi,lab in LOADING_BANDS[1:]]
    y = np.arange(len(groups))
    a = [int(g[1].sum()) for g in groups]; b = [int(g[2].sum()) for g in groups]
    ax.barh(y+.2,a,.38,color=BASE_GRAY,label="S0"); ax.barh(y-.2,b,.38,color=REGULATED,label=label)
    for i,(x0,x1) in enumerate(zip(a,b)):
        ax.annotate(f"{x0} → {x1}",(max(x0,x1),i),xytext=(4,0),textcoords="offset points",va="center",fontsize=8,color=INK)
    ax.set_yticks(y,[g[0] for g in groups]); ax.invert_yaxis(); ax.set_xscale("symlog",linthresh=10)
    ax.set_xlabel("quantidade (escala log)",color=MUTED); ax.legend(frameon=False,fontsize=9,loc="lower right")
    return fig
