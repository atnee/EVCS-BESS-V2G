"""Fig. 6 of the paper: daily maximum line loading of S1-S4 on one common colour scale (2 x 2 panels).

python -m integration.loading_maps            # reads results/ieee8500/integration/dados -> results/ieee8500/paper/

No simulation: the saved branch tables of the integration run are read back. The indicator is the one of the
success criterion (integration.resumo.line_peaks): highest physical-phase loading of each line over the day,
with the regulators acting.
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd

SCENARIOS = {"S1": "EVCS", "S2": "EVCS + BESS", "S3": "EVCS + V2G", "S4": "EVCS + BESS + V2G"}
# Common colour scale (%): one set of bands for every panel; the last band is open (> 110 %).
BOUNDS = [0, 50, 80, 90, 100, 110]
BAND_COLORS = ["#8f8f8f", "#f2c14e", "#f08a3c", "#d94f1f", "#b0151d", "#4d0008"]
LINE_WIDTHS = [.55, .8, 1.0, 1.2, 1.7, 1.7]  # pt per band: heavier lines where the loading is higher
WIDTH_MM, HEIGHT_MM = 190., 196.
FONT, LABEL_PT, BODY_PT = "Arial", 8., 7.
INK = "#1a1a1a"
BESS_COLOR, V2G_COLOR = "#c2409a", "#1baf7a"  # as integration.impact.SITE_STYLE


def colour_scale(peaks):
    """(cmap, norm) shared by all panels; checks that the bands cover every value shown."""
    from matplotlib.colors import BoundaryNorm, ListedColormap
    top = max(float(p.max()) for p in peaks.values())
    if min(float(p.min()) for p in peaks.values()) < BOUNDS[0]:
        raise ValueError("Loading below the first band")
    cmap = ListedColormap(BAND_COLORS)  # last colour = the open band above BOUNDS[-1]
    return cmap, BoundaryNorm(BOUNDS,cmap.N,extend="max"), top


def read_peaks(tables):
    """{scenario: daily maximum loading per line (%)} for S0-S4, from <scenario>_branches.csv."""
    from integration.resumo import line_peaks
    cols = ["time","line","element_type","physical_phase","loading_pct"]
    return {sc: line_peaks(pd.read_csv(Path(tables)/f"{sc}_branches.csv",usecols=cols))[0]
            for sc in ("S0",*SCENARIOS)}


def panel_numbers(s0, peak):
    """New overloads (lines at most 100 % in S0 above 100 %) and the lines already above 100 % in S0."""
    s0 = s0.reindex(peak.index)
    new = peak[(s0 <= 100) & (peak > 100)]
    base = peak[s0 > 100]
    return dict(new=len(new),new_max=float(new.max()) if len(new) else None,
                base={line: float(v) for line,v in base.items()})


# The three corridors of the text (docs/S4_integracao.md): lines above 100 % in the 15-min S1 study, grouped by
# phase conductor.
CORRIDORS = {"397": ("C1", "exit trunk, 397 ACSR"), "4": ("C2", "#4 ACSR branch"), "2/0": ("C3", "south trunk, 2/0 ACSR")}


def corridors(line_peak, linecodes):
    """{tag: (description, [lines])} from a {line: daily max loading} series and {line: linecode}."""
    import re
    groups = {}
    for line in line_peak[line_peak > 100].index:
        m = re.match(r"3ph_h-([^_]+)_acsr", str(linecodes.get(line, "")))
        if m and m.group(1) in CORRIDORS:
            tag, text = CORRIDORS[m.group(1)]
            groups.setdefault(tag, (text, []))[1].append(line)
    return dict(sorted(groups.items()))


def loading_map_figures(peaks, graph, slack, sites, unit_m, corridor_lines=None):
    """{'fig6_line_loading_maps': Figure}. peaks from read_peaks; sites: {scenario: {bus: 'bess' | 'v2g'}};
    unit_m: metres per coordinate unit (scale bar); corridor_lines from corridors() (grey halo + tag)."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    segments = {d["id"]: (pos[u],pos[w]) for u,w,d in graph.edges(data=True) if d["kind"] == "line"}
    shown = {sc: peaks[sc] for sc in SCENARIOS}
    cmap, norm, top = colour_scale(shown)
    xy = np.array([p for p in pos.values() if np.isfinite(p).all()])
    lo, hi = xy.min(0), xy.max(0)
    pad = .01*(hi-lo).max()
    with plt.rc_context({"font.family": FONT,"text.color": INK,"pdf.fonttype": 42}):
        fig = plt.figure(figsize=(WIDTH_MM/25.4,HEIGHT_MM/25.4),facecolor="white")
        grid = fig.add_gridspec(2,2,left=.01,right=.99,top=.952,bottom=.115,wspace=.03,hspace=.09)
        for k,(sc,label) in enumerate(SCENARIOS.items()):
            ax = fig.add_subplot(grid[k//2,k % 2])
            peak = shown[sc]
            ids = [i for i in peak.index if i in segments]
            load = peak.reindex(ids).to_numpy()
            order = np.argsort(load)  # most loaded drawn on top
            widths = np.asarray(LINE_WIDTHS)[np.digitize(load[order],BOUNDS[1:],right=True)]
            ax.add_collection(LineCollection([segments[ids[i]] for i in order],colors=cmap(norm(load[order])),
                                             linewidths=widths,capstyle="round",zorder=2))
            for tag,(_,members) in (corridor_lines or {}).items():  # halo under the corridor and its tag
                seg = [segments[l] for l in members if l in segments]
                ax.add_collection(LineCollection(seg,colors="#cfcfcf",linewidths=5.,capstyle="round",zorder=1))
                ends = np.array([p for s in seg for p in s])
                far = ends[np.argmin(ends[:,0])]  # west end of the corridor
                ax.annotate(tag,far,xytext=(-4,4),textcoords="offset points",ha="right",va="bottom",
                            fontsize=BODY_PT,weight="bold",zorder=8,
                            bbox=dict(boxstyle="round,pad=.15",fc="white",ec="#9a9a9a",lw=.5))
            nums = panel_numbers(peaks["S0"],peak)
            for line in nums["base"]:  # already above 100 % in S0: ring, so it is not read as a new overload
                if line in segments:
                    (x0,y0),(x1,y1) = segments[line]
                    ax.scatter((x0+x1)/2,(y0+y1)/2,s=60,facecolor="none",edgecolor=INK,linewidth=.8,zorder=5)
            ax.scatter(*pos[slack],marker="s",s=22,color=INK,zorder=6)
            for bus,kind in sites.get(sc,{}).items():
                if kind == "bess":
                    ax.scatter(*pos[bus],marker="D",s=70,color=BESS_COLOR,edgecolor="white",linewidth=.8,zorder=7)
                    tag = "BESS"
                else:  # host station: filled when V2G is active, open when its cars charge without control
                    ax.scatter(*pos[bus],marker="s",s=64,color=V2G_COLOR if kind == "v2g" else "white",
                               edgecolor=V2G_COLOR if kind == "host" else "white",linewidth=1.3 if kind == "host" else .8,
                               zorder=7)
                    tag = "V2G"
                ax.annotate(tag,pos[bus],xytext=(6,-6),textcoords="offset points",ha="left",va="top",fontsize=BODY_PT,
                            weight="bold",color=BESS_COLOR if kind == "bess" else V2G_COLOR,zorder=8)
            ax.set_xlim(lo[0]-pad,hi[0]+pad); ax.set_ylim(lo[1]-pad,hi[1]+pad)
            ax.set_aspect("equal"); ax.axis("off")
            ax.set_title(f"({'abcd'[k]})  {sc}: {label}",loc="left",fontsize=LABEL_PT,weight="bold",pad=2)
            new = (f"{nums['new']} new lines > 100 %\nmax. {nums['new_max']:.1f} %" if nums["new"]
                   else "no new line > 100 %")
            base = "\n".join(f"ringed line: {v:.1f} %" for v in nums["base"].values())
            # bottom right is empty on this feeder, so the numbers never cover a line
            ax.text(.995,.085,f"{new}\n{base}",transform=ax.transAxes,ha="right",va="bottom",fontsize=BODY_PT,
                    linespacing=1.3)
            if k == 0:  # scale bar, 2 km
                L = 2000/unit_m
                x0, y0 = hi[0]-L, lo[1]
                ax.plot([x0,x0+L],[y0,y0],color=INK,lw=1.2,solid_capstyle="butt")
                ax.text(x0+L/2,y0+.012*(hi-lo).max(),"2 km",ha="center",va="bottom",fontsize=BODY_PT)

        # one colour bar for the four panels, and the symbol key
        cax = fig.add_axes([.03,.065,.42,.016])
        sm = plt.cm.ScalarMappable(norm=norm,cmap=cmap)
        bar = fig.colorbar(sm,cax=cax,orientation="horizontal",extend="max",spacing="uniform",ticks=BOUNDS)
        bar.ax.tick_params(labelsize=BODY_PT,length=2,width=.5); bar.outline.set_linewidth(.5)
        bar.set_label(f"Daily maximum line loading (%), common scale; > 110 % up to {top:.1f} %",fontsize=BODY_PT)
        handles = [Line2D([],[],marker="s",ls="",color=INK,ms=4,label="Substation"),
                   Line2D([],[],marker="D",ls="",color=BESS_COLOR,ms=4.5,label="BESS (S2, S4)"),
                   Line2D([],[],marker="o",ls="",mfc="none",mec=INK,ms=6,label="Line already > 100 % in S0"),
                   Line2D([],[],marker="s",ls="",mfc="white",mec=V2G_COLOR,ms=4.5,
                          label="V2G station, uncontrolled (S1, S2)"),
                   Line2D([],[],marker="s",ls="",color=V2G_COLOR,ms=4.5,label="V2G station, V2G active (S3, S4)")]
        handles += [Line2D([],[],color="#cfcfcf",lw=5,label=f"{t}: {d}")
                    for t,(d,_) in (corridor_lines or {}).items()]
        fig.legend(handles=handles,loc="lower left",bbox_to_anchor=(.47,.008),ncol=2,frameon=False,handlelength=1.6,
                   fontsize=BODY_PT,handletextpad=.4,columnspacing=1.2)
    return {"fig6_line_loading_maps": fig}


def scenario_sites(config_dir="configs"):
    """BESS on S2/S4; the V2G host station on S1-S4, active (bidirectional) only on S3/S4. From the configs."""
    from core.schemas import read_parameters
    bess = str(read_parameters(Path(config_dir)/"bess.yaml")["connection"]["bus"])
    host = str(read_parameters(Path(config_dir)/"evcs.yaml")["station"]["bus"])
    return {"S1": {host: "host"},"S2": {bess: "bess",host: "host"},"S3": {host: "v2g"},
            "S4": {bess: "bess",host: "v2g"}}


def build(config_dir="configs", results="results"):
    from integration.scenarios import study_feeder
    from network.feeder_loader import read_feeder_data, load_feeder
    from network.topology import feeder_graph
    feeder = study_feeder(config_dir)
    data = read_feeder_data(feeder)
    from core.schemas import read_parameters
    from integration.resumo import line_peaks
    peaks = read_peaks(Path(results)/feeder/"integration"/"dados")
    evs = int(read_parameters(Path(config_dir)/"evcs.yaml")["planning"]["fleet"]["evs"])
    s1 = Path(results)/feeder/"s1"/f"evs_{evs}"/"dados"/"branches.csv"
    groups = None
    if s1.exists():
        cols = ["time","line","element_type","physical_phase","loading_pct"]
        groups = corridors(line_peaks(pd.read_csv(s1,usecols=cols))[0],{l["id"]: l["linecode"] for l in data["lines"]})
    return loading_map_figures(peaks,feeder_graph(data),load_feeder(feeder).slack_bus,scenario_sites(config_dir),
                               data["coordinate_unit_m"],groups)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--configs",default="configs")
    ap.add_argument("--results",default="results")
    ap.add_argument("--output",default="results/ieee8500/paper")
    a = ap.parse_args()
    from integration.framework_figure import save_paper_figure
    save_paper_figure(build(a.configs,a.results),a.output)
    print(Path(a.output)/"fig6_line_loading_maps.png")


if __name__ == "__main__":
    main()
