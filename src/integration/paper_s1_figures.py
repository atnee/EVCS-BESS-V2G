"""Figs. 2-5 of the paper, from saved results (no simulation):

- fig2_station_siting: public stations of the 3000-EV S1 case on the feeder, with coverage radii;
- fig3_ev_penetration: substation peak, minimum voltage and highest line loading against the fleet size
  (EVCS study, 15-min step);
- fig4_service_transformers: peak loading of the 5 most and 3 least loaded service transformers and the
  intraday power of the 5 most loaded (S1, 3000 EVs, 15-min step);
- fig5_substation_demand: substation active power of S0-S4 (integration, 1-h step).

python -m integration.paper_s1_figures        # -> results/ieee8500/paper/
"""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from integration.paper_style import (BODY_PT, DOUBLE_MM, INK, MUTED, ONE_HALF_MM, SCENARIO_COLORS,
                                     STATION_COLORS, figure_mm, panel, rc, scale_bar)

STATION_KEY = {"dc": ("DC hub", "H"), "ac": ("Three-phase AC station", "P"), "ac1": ("Single-phase AC station", "o")}
V_LIMIT, LOAD_LIMIT = .95, 100.


def _hours(n):
    return np.arange(n+1)*24/n


def _step(ax, y, **kw):
    y = np.asarray(y, float)
    return ax.step(_hours(len(y)), np.append(y, y[-1]), where="post", **kw)


def _day_axis(ax):
    ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 6)); ax.set_xlabel("Hour of day")


# ----------------------------------------------------------------------------------------------- Fig. 2
def station_siting_figure(graph, buses, sites, types, unit_m, evs):
    """sites: {bus: 'dc' | 'ac' | 'ac1'}; types: planning station types (charger_kw, chargers_per_site,
    coverage_radius_m); buses: bus_table (x, y, load_kw)."""
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle
    pos = {n: d["xy"] for n, d in graph.nodes(data=True)}
    seg = [(pos[u], pos[w]) for u, w, d in graph.edges(data=True) if d["kind"] in ("line", "switch")]
    xy = np.array([p for p in pos.values() if np.isfinite(p).all()])
    lo, hi = xy.min(0), xy.max(0)
    count = {k: sum(v == k for v in sites.values()) for k in STATION_KEY}
    with plt.rc_context(rc()):
        fig = figure_mm(ONE_HALF_MM, 142.)
        ax = fig.add_axes([.01, .19, .98, .8])
        ax.add_collection(LineCollection(seg, colors="#9a9a9a", linewidths=.45, zorder=1))
        load = buses[buses.load_kw > 0]
        ax.scatter(load.x, load.y, s=1.5+6*load.load_kw/load.load_kw.max(), color="#9ecae1", lw=0, zorder=2)
        hubs = sorted((b for b, k in sites.items() if k == "dc"), key=lambda b: (-pos[b][1], pos[b][0]))
        for key in ("dc", "ac", "ac1"):  # hubs first, so the small stations stay visible on top
            c = STATION_COLORS[key]
            for bus in (b for b, k in sites.items() if k == key):
                ax.add_patch(Circle(pos[bus], types[key]["coverage_radius_m"]/unit_m, fc=c, alpha=.06, ec="none",
                                    zorder=0))
        for key, size in (("dc", 46), ("ac", 34), ("ac1", 14)):
            pts = np.array([pos[b] for b, k in sites.items() if k == key])
            if len(pts):
                ax.scatter(pts[:, 0], pts[:, 1], marker=STATION_KEY[key][1], s=size, color=STATION_COLORS[key],
                           edgecolor="white", linewidth=.5, zorder=5)
        for i, bus in enumerate(hubs, 1):
            ax.annotate(f"H{i}", pos[bus], xytext=(4, 3), textcoords="offset points", fontsize=BODY_PT,
                        weight="bold", color=STATION_COLORS["dc"], zorder=6)
        pad = .02*(hi-lo).max()
        ax.set_xlim(lo[0]-pad, hi[0]+pad); ax.set_ylim(lo[1]-pad, hi[1]+pad)
        ax.set_aspect("equal"); ax.axis("off")
        scale_bar(ax, hi[0], lo[1], unit_m)
        handles = []
        for key, (label, marker) in STATION_KEY.items():
            t = types[key]
            handles.append(Line2D([], [], marker=marker, ls="", color=STATION_COLORS[key], mec="white", mew=.5,
                                  ms={"dc": 7, "ac": 6.5, "ac1": 4.5}[key],
                                  label=f"{label}: {count[key]} sites, {t['chargers_per_site']} × "
                                        f"{t['charger_kw']:g} kW, {t['coverage_radius_m']/1000:g} km coverage"))
        handles += [Line2D([], [], marker="o", ls="", color="#9ecae1", ms=3.5,
                           label="Customer load bus (size: nominal load)"),
                    Line2D([], [], color="#9a9a9a", lw=.8, label="MV line")]
        fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(.02, .035), ncol=1, frameon=False,
                   handletextpad=.5, labelspacing=.45)
        fig.text(.02, .012, f"Synthetic case: {sum(count.values())} sites chosen by the planning model for "
                            f"{evs:,} EVs on the IEEE 8500 test feeder; no real locations.",
                 fontsize=BODY_PT, color=MUTED, style="italic")
    return {"fig2_station_siting": fig}


# ----------------------------------------------------------------------------------------------- Fig. 3
def penetration_figure(fleets):
    """fleets: sobrecarga_por_frota.csv (carros, ponta_kw, vmin_pu, vmin_sem_reg_pu, linha_max_pct)."""
    import matplotlib.pyplot as plt
    t = fleets.sort_values("carros")
    s0 = t[t.carros == 0].iloc[0]
    with plt.rc_context(rc()):
        fig = figure_mm(DOUBLE_MM, 62.)
        axes = [fig.add_axes([.065+k*.33, .2, .26, .66]) for k in range(3)]
        ax = panel(axes[0], "(a)  Substation peak (MW)")
        ax.plot(t.carros, t.ponta_kw/1000, marker="o", ms=3.5, color=SCENARIO_COLORS["S1"])
        ax.axhline(s0.ponta_kw/1000, color=SCENARIO_COLORS["S0"], lw=.7, ls=(0, (3, 1.5)))
        ax.annotate("S0 peak", (t.carros.max(), s0.ponta_kw/1000), xytext=(0, 3), textcoords="offset points",
                    ha="right", va="bottom", fontsize=BODY_PT, color=SCENARIO_COLORS["S0"])
        top = t.iloc[-1]
        ax.annotate(f"+{(top.ponta_kw-s0.ponta_kw)/1000:.2f} MW at {int(top.carros):,} EVs,\nincrease relative to S0 peak",
                    (0, top.ponta_kw/1000), ha="left", va="top", fontsize=BODY_PT)
        ax.annotate("", (top.carros, top.ponta_kw/1000), (top.carros, s0.ponta_kw/1000),
                    arrowprops=dict(arrowstyle="<->,head_length=.3,head_width=.15", color=INK, lw=.6))
        ax = panel(axes[1], "(b)  Minimum voltage (pu)")
        ax.plot(t.carros, t.vmin_pu, marker="o", ms=3.5, color=SCENARIO_COLORS["S1"], label="regulators acting")
        ax.plot(t.carros, t.vmin_sem_reg_pu, marker="s", ms=3, mfc="white", color=SCENARIO_COLORS["S1"],
                ls=(0, (3, 1.5)), label="taps frozen at S0")
        ax.axhline(V_LIMIT, color=INK, lw=.6, ls=(0, (1, 1.5)))
        ax.annotate("0.95 pu", (t.carros.max(), V_LIMIT), xytext=(0, 2), textcoords="offset points", ha="right",
                    va="bottom", fontsize=BODY_PT)
        ax.legend(frameon=False, loc="lower left", handlelength=2.2)
        ax = panel(axes[2], "(c)  Highest line loading (%)")
        ax.plot(t.carros, t.linha_max_pct, marker="o", ms=3.5, color=SCENARIO_COLORS["S1"])
        ax.axhline(LOAD_LIMIT, color=INK, lw=.6, ls=(0, (1, 1.5)))
        ax.annotate("100 %", (0, LOAD_LIMIT), xytext=(2, 2), textcoords="offset points", va="bottom", fontsize=BODY_PT)
        for ax in axes:
            ax.set_xticks(t.carros); ax.set_xticklabels([f"{int(c):,}" for c in t.carros])
            ax.set_xlabel("EVs in the fleet")
        fig.text(.99, .015, "EVCS study, 15-min step, regulators acting unless stated", ha="right", fontsize=BODY_PT,
                 color=MUTED, style="italic")
    return {"fig3_ev_penetration": fig}


# ----------------------------------------------------------------------------------------------- Fig. 4
def transformers_figure(table, intraday, most=5, least=3):
    """table: service_transformers.csv sorted by S1 peak; intraday: service_transformers_intraday.csv (kW)."""
    import matplotlib.pyplot as plt
    t = table.sort_values(["peak_s1_pct", "id"], ascending=[False, True]).reset_index(drop=True)
    chosen = pd.concat([t.head(most), t.tail(least).iloc[::-1]]).reset_index(drop=True)
    chosen["tag"] = [f"T{i}" for i in range(1, len(chosen)+1)]
    chosen["has_station"] = chosen.station.fillna("").astype(str).str.len() > 0  # empty cell is NaN, not ""
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7", "#c2409a"]
    with plt.rc_context(rc()):
        fig = figure_mm(DOUBLE_MM, 80.)
        ax = panel(fig.add_axes([.06, .32, .4, .58]), "(a)  Peak apparent-power loading (% of kVA rating)")
        x = np.arange(len(chosen)); w = .38
        ax.bar(x-w/2, chosen.peak_s0_pct, w, color=SCENARIO_COLORS["S0"], label="S0")
        ax.bar(x+w/2, chosen.peak_s1_pct, w, color=SCENARIO_COLORS["S1"], label="S1, 3,000 EVs")
        ax.axhline(LOAD_LIMIT, color=INK, lw=.6, ls=(0, (1, 1.5)))
        ax.axvline(most-.5, color=MUTED, lw=.5)
        for xi, v in zip(x+w/2, chosen.peak_s1_pct):
            ax.annotate(f"{v:.0f}", (xi, v), xytext=(0, 1.5), textcoords="offset points", ha="center",
                        va="bottom", fontsize=BODY_PT)
        ax.set_xticks(x, [f"{r.tag}{'*' if r.has_station else ''}\n{r.kva:g} kVA" for r in chosen.itertuples()])
        top = 1.18*chosen.peak_s1_pct.max()
        ax.set_ylim(0, top)
        ax.text((most-1)/2, top, "5 most loaded", ha="center", va="top", fontsize=BODY_PT)
        ax.text(most+(least-1)/2, top, "3 least loaded", ha="center", va="top", fontsize=BODY_PT)
        ax.legend(frameon=False, loc="center right", bbox_to_anchor=(1., .62))
        ax = panel(fig.add_axes([.56, .32, .36, .58]), "(b)  Active power of T1–T5 (kW)")
        for c, r in zip(colors, chosen.head(most).itertuples()):
            _step(ax, intraday[r.id], color=c, lw=.9, label=f"{r.tag}: {r.id}, {r.kva:g} kVA")
        for kva in sorted(set(chosen.head(most).kva)):
            ax.axhline(kva, color=INK, lw=.6, ls=(0, (1, 1.5)))
            ax.annotate(f"{kva:g} kVA", (24, kva), xytext=(3, 0), textcoords="offset points", ha="left",
                        va="center", fontsize=BODY_PT, annotation_clip=False)
        _day_axis(ax); ax.set_ylim(0, None)
        ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(.5, -.18), ncol=3, columnspacing=1.,
                  handlelength=1.6)
        fig.text(.01, .015, "* transformer that also feeds a single-phase AC station (hypothesis: station on the "
                            "customer's transformer).\nDotted lines in (b): kVA rating as a reference for the kW "
                            "curves. S1, 3,000 EVs, 15-min step.", fontsize=BODY_PT, color=MUTED, style="italic")
    return {"fig4_service_transformers": fig}


# ----------------------------------------------------------------------------------------------- Fig. 5
def substation_figure(sources):
    """sources: {scenario: <scenario>_source.csv} of the integration run."""
    import matplotlib.pyplot as plt
    with plt.rc_context(rc()):
        fig = figure_mm(ONE_HALF_MM, 70.)
        ax = panel(fig.add_axes([.085, .17, .62, .72]), "Substation active power (MW)")
        for sc, s in sources.items():
            p = s.p_kw.to_numpy()/1000
            _step(ax, p, color=SCENARIO_COLORS[sc], lw=1.1 if sc in ("S0", "S4") else .9,
                  label=f"{sc}: peak {p.max():.2f} MW")
        _day_axis(ax)
        ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.02, .5), handlelength=1.8)
        fig.text(.99, .02, "Integration, 1-h step", ha="right", fontsize=BODY_PT, color=MUTED, style="italic")
    return {"fig5_substation_demand": fig}


# ----------------------------------------------------------------------------------------------- build
def build(config_dir="configs", results="results"):
    from core.schemas import read_parameters
    from integration.scenarios import study_feeder
    from network.feeder_loader import read_feeder_data
    from network.topology import feeder_graph, bus_table
    feeder = study_feeder(config_dir)
    root = Path(results)/feeder
    data = read_feeder_data(feeder); graph = feeder_graph(data)
    evcs = read_parameters(Path(config_dir)/"evcs.yaml")
    evs = int(evcs["planning"]["fleet"]["evs"])
    fleets = pd.read_csv(root/"evcs_screening"/"dados"/"fleet_scenarios.csv").set_index("evs")
    sites = json.loads(fleets.loc[evs].sites)
    s1 = root/"s1"/f"evs_{evs}"/"dados"
    figs = {**station_siting_figure(graph, bus_table(graph), sites, evcs["planning"]["station_types"],
                                    data["coordinate_unit_m"], evs),
            **penetration_figure(pd.read_csv(root/"s1"/"dados"/"sobrecarga_por_frota.csv")),
            **transformers_figure(pd.read_csv(s1/"service_transformers.csv"),
                                  pd.read_csv(s1/"service_transformers_intraday.csv")),
            **substation_figure({sc: pd.read_csv(root/"integration"/"dados"/f"{sc}_source.csv")
                                 for sc in SCENARIO_COLORS})}
    return figs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--configs", default="configs")
    ap.add_argument("--results", default="results")
    ap.add_argument("--output", default="results/ieee8500/paper")
    a = ap.parse_args()
    from integration.framework_figure import save_paper_figure
    save_paper_figure(build(a.configs, a.results), a.output)
    print(a.output)


if __name__ == "__main__":
    main()
