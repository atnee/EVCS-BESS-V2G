"""Fig. 7 of the paper: regulators acting versus taps frozen at S0 (voltage maps of S1 and S4 on one common
colour scale), minimum feeder voltage over the day, and the tap trajectories of the four regulators.

python -m integration.regulator_figure        # reads results/ieee8500/integration/dados -> results/ieee8500/paper/

No simulation: the saved voltage and source tables of the integration run (`--frozen-taps`) are read back.
Voltages follow the success criterion: lowest phase voltage of every bus except the source bus.
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd

MAPS = (("S1","acting"),("S1","frozen"),("S4","acting"),("S4","frozen"))
VIEW_LABEL = {"acting": "regulators acting", "frozen": "taps frozen at S0"}
# Common voltage scale (pu) for the four maps; 0.95 pu (ANSI C84.1 range A) is a band edge.
BOUNDS = [.93, .95, .97, .99, 1.01, 1.03, 1.05]
BAND_COLORS = ["#67000d", "#c0392b", "#ef8a62", "#f7c9ae", "#c9c9c9", "#92c5de", "#4393c3", "#1f4e8c"]  # under..over
SCENARIO_COLORS = {"S0": "#7a7a7a", "S1": "#d94f1f", "S4": "#2a78d6"}
REGULATORS = {"feeder_reg": "Substation", "vreg2": "VREG2", "vreg3": "VREG3", "vreg4": "VREG4"}
WIDTH_MM, HEIGHT_MM, SUPP_HEIGHT_MM = 190., 118., 62.
FONT, LABEL_PT, BODY_PT = "Arial", 8., 7.
INK, LIMIT = "#1a1a1a", .95


def read_tables(tables, slack):
    """{(scenario, view): voltages}, {scenario: source} for S0, S1 and S4. S0 has one view (its taps are
    the frozen ones)."""
    tables = Path(tables)
    volt, source = {}, {}
    for sc in ("S0","S1","S4"):
        for view,prefix in (("acting",f"{sc}_"),("frozen",f"{sc}_frozen_taps_")):
            path = tables/f"{prefix}voltages.csv"
            if sc == "S0" and view == "frozen":
                continue
            v = pd.read_csv(path)
            volt[(sc,view)] = v[v.bus != slack]
        source[sc] = pd.read_csv(tables/f"{sc}_source.csv")
    return volt, source


def bus_minimum(v):
    """Daily minimum voltage of each bus (pu), lowest phase."""
    return v.groupby("bus").v_pu.min()


def hourly_minimum(v):
    """Feeder minimum voltage at each step (pu)."""
    return v.groupby("time").v_pu.min().to_numpy()


def colour_scale():
    from matplotlib.colors import BoundaryNorm, ListedColormap
    cmap = ListedColormap(BAND_COLORS)
    return cmap, BoundaryNorm(BOUNDS,cmap.N,extend="both")


def tap_operations(source):
    taps = source.filter(like="tap_")
    return {c.removeprefix("tap_"): int(taps[c].diff().abs().sum()) for c in taps}


def regulator_figures(volt, source, graph, dt_h):
    """{'fig7_regulators_voltage': maps and minimum voltage, 'figS1_regulator_taps': tap trajectories}."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    lines = [(u,w) for u,w,d in graph.edges(data=True) if d["kind"] in ("line","switch")]
    regs = {d["id"]: pos[d["lv_bus"]] for _,_,d in graph.edges(data=True) if d["kind"] == "regulator"}
    xy = np.array([p for p in pos.values() if np.isfinite(p).all()])
    lo, hi = xy.min(0), xy.max(0)
    cmap, norm = colour_scale()
    with plt.rc_context({"font.family": FONT,"text.color": INK,"pdf.fonttype": 42,"axes.edgecolor": INK,
                         "axes.linewidth": .5,"xtick.major.width": .5,"ytick.major.width": .5,
                         "xtick.labelsize": BODY_PT,"ytick.labelsize": BODY_PT,"axes.labelsize": BODY_PT}):
        fig = plt.figure(figsize=(WIDTH_MM/25.4,HEIGHT_MM/25.4),facecolor="white")
        top = fig.add_gridspec(1,4,left=.005,right=.995,top=.955,bottom=.47,wspace=.02)
        for k,(sc,view) in enumerate(MAPS):
            ax = fig.add_subplot(top[0,k])
            vmin = bus_minimum(volt[(sc,view)])
            seg, val = [], []
            for u,w in lines:
                if u in vmin.index and w in vmin.index:
                    seg.append((pos[u],pos[w])); val.append(min(vmin[u],vmin[w]))
            val = np.asarray(val); order = np.argsort(-val)  # lowest voltages drawn on top
            ax.add_collection(LineCollection([seg[i] for i in order],colors=cmap(norm(val[order])),
                                             linewidths=np.where(val[order] < LIMIT,1.1,.55),capstyle="round"))
            for name,(x,y) in regs.items():
                ax.scatter(x,y,marker="^",s=22,facecolor="white",edgecolor=INK,linewidth=.7,zorder=5)
                if k == 0 and name != "feeder_reg":
                    ax.annotate(REGULATORS[name],(x,y),xytext=(3,3),textcoords="offset points",fontsize=BODY_PT,
                                zorder=6)
            pad = .025*(hi-lo).max()
            ax.set_xlim(lo[0]-pad,hi[0]+pad); ax.set_ylim(lo[1]-pad,hi[1]+pad); ax.set_aspect("equal"); ax.axis("off")
            ax.set_title(f"({'abcd'[k]})  {sc}, {VIEW_LABEL[view]}",loc="left",fontsize=LABEL_PT,weight="bold",pad=2)
            ax.text(.99,.02,f"min. {vmin.min():.3f} pu",transform=ax.transAxes,ha="right",va="bottom",
                    fontsize=BODY_PT)
        cax = fig.add_axes([.2,.425,.5,.017])
        bar = fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),cax=cax,orientation="horizontal",
                           extend="both",spacing="uniform",ticks=BOUNDS)
        bar.ax.tick_params(labelsize=BODY_PT,length=2,width=.5); bar.outline.set_linewidth(.5)
        bar.set_label("Daily minimum bus voltage (pu), common scale for (a)–(d)",fontsize=BODY_PT)
        fig.legend(handles=[Line2D([],[],marker="^",ls="",mfc="white",mec=INK,mew=.7,ms=5,label="Voltage regulator")],
                   loc="center left",bbox_to_anchor=(.74,.434),frameon=False,fontsize=BODY_PT,handletextpad=.3)

        # (e) minimum voltage over the day
        steps = len(source["S0"])
        hours = np.arange(steps+1)*dt_h
        ax = fig.add_axes([.075,.09,.55,.215])
        for (sc,view),v in volt.items():
            y = hourly_minimum(v)
            ax.step(hours,np.append(y,y[-1]),where="post",color=SCENARIO_COLORS[sc],lw=1.,
                    ls="-" if view == "acting" else (0,(3,1.5)))
        ax.axhline(LIMIT,color=INK,lw=.6,ls=(0,(1,1.5)))
        ax.set_xlim(0,24); ax.set_xticks(range(0,25,6)); ax.set_xlabel("Hour of day")
        ax.set_ylabel("Feeder minimum voltage (pu)")
        ax.set_title("(e)  Minimum voltage over the day",loc="left",fontsize=LABEL_PT,weight="bold",pad=3)
        handles = [Line2D([],[],color=c,lw=1.,label=sc) for sc,c in SCENARIO_COLORS.items()]
        handles += [Line2D([],[],color=INK,lw=1.,label="regulators acting"),
                    Line2D([],[],color=INK,lw=1.,ls=(0,(3,1.5)),label="taps frozen at S0"),
                    Line2D([],[],color=INK,lw=.6,ls=(0,(1,1.5)),label="0.95 pu")]
        ax.legend(handles=handles,fontsize=BODY_PT,frameon=False,ncol=2,loc="center left",bbox_to_anchor=(1.03,.5),
                  handlelength=2.2,columnspacing=1.2)
        ax.tick_params(length=2)
        for side in ("top","right"):
            ax.spines[side].set_visible(False)

        # Supplementary figure: tap trajectories, one panel per regulator (S0 taps = the frozen view)
        ops = {sc: tap_operations(s) for sc,s in source.items()}
        all_taps = pd.concat([s.filter(like="tap_") for s in source.values()])
        ylo, yhi = all_taps.min().min()-1, all_taps.max().max()+1
        sup = plt.figure(figsize=(WIDTH_MM/25.4,SUPP_HEIGHT_MM/25.4),facecolor="white")
        for k,name in enumerate(REGULATORS):
            col = f"tap_{name}"
            ax = sup.add_axes([.06+k*.235,.3,.2,.56])
            for sc,s in source.items():
                y = s[col].to_numpy()
                ax.step(hours,np.append(y,y[-1]),where="post",color=SCENARIO_COLORS[sc],lw=1.,
                        ls=(0,(3,1.5)) if sc == "S0" else "-")
            ax.set_xlim(0,24); ax.set_ylim(ylo,yhi); ax.set_xticks(range(0,25,6)); ax.set_xlabel("Hour of day")
            ax.tick_params(length=2)
            if k:
                ax.set_yticklabels([])
            else:
                ax.set_ylabel("Tap position")
            counts = " / ".join(str(ops[sc][name]) for sc in source)
            ax.set_title(f"({'abcd'[k]})  {REGULATORS[name]}",loc="left",fontsize=LABEL_PT,weight="bold",pad=10)
            ax.text(0,1.,f"{counts} operations",transform=ax.transAxes,ha="left",va="bottom",fontsize=BODY_PT)
            for side in ("top","right"):
                ax.spines[side].set_visible(False)
        sup.legend(handles=[Line2D([],[],color=SCENARIO_COLORS["S0"],lw=1.,ls=(0,(3,1.5)),label="S0 (= frozen view)"),
                            Line2D([],[],color=SCENARIO_COLORS["S1"],lw=1.,label="S1"),
                            Line2D([],[],color=SCENARIO_COLORS["S4"],lw=1.,label="S4")],
                   loc="lower center",bbox_to_anchor=(.5,.0),ncol=3,frameon=False,fontsize=BODY_PT,
                   title=f"Tap operations per day given as {' / '.join(source)}; 1-h step",title_fontsize=BODY_PT)
    return {"fig7_regulators_voltage": fig, "figS1_regulator_taps": sup}


def build(config_dir="configs", results="results"):
    from core.schemas import read_parameters
    from integration.scenarios import study_feeder
    from network.feeder_loader import read_feeder_data, load_feeder
    from network.topology import feeder_graph
    feeder = study_feeder(config_dir)
    volt, source = read_tables(Path(results)/feeder/"integration"/"dados",load_feeder(feeder).slack_bus)
    dt_h = read_parameters(Path(config_dir)/"evcs.yaml")["time"]["dt_h"]
    return regulator_figures(volt,source,feeder_graph(read_feeder_data(feeder)),dt_h)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--configs",default="configs")
    ap.add_argument("--results",default="results")
    ap.add_argument("--output",default="results/ieee8500/paper")
    a = ap.parse_args()
    from integration.framework_figure import save_paper_figure
    save_paper_figure(build(a.configs,a.results),a.output)
    print(Path(a.output)/"fig7_regulators_voltage.png")


if __name__ == "__main__":
    main()
