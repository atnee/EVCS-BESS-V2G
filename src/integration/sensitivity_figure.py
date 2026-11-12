"""Fig. 8 of the paper: joint thermal and voltage sensitivity of S4 over all sensitivity cases.

python -m integration.sensitivity_figure      # reads results/ieee8500/sensitivity -> results/ieee8500/paper/

One row per case (dot plot), two panels on the same rows: (a) S4 minimum voltage with the regulators acting and
(b) S4 highest loading among the lines that were at most 100 % in the case's own S0, i.e. the two halves of the
success criterion. On the matched seeds and the 15-min case a segment joins the current configuration to the
combined one (half-size BESS + 1 V regulator band) for the same seed.

The thermal value is read from each case's line_peaks.csv (the base case from the integration run) and must
reproduce the new-overload counts of sensitivity.csv; cases left out of the rows must equal a drawn case.
build() stops otherwise. No simulation.
"""
from pathlib import Path
import argparse
import re
import numpy as np
import pandas as pd

V_LIMIT, LOAD_LIMIT = .95, 100.
V_RANGE, LOAD_RANGE = (.928, .962), (97., 103.)  # loading beyond the range is drawn at the edge with its value
WIDTH_MM = 190.
ROW_MM, TOP_MM, BOTTOM_MM = 3.15, 9., 21.
FONT, LABEL_PT, BODY_PT = "Arial", 8., 7.
INK, GRID_C, OK_FILL = "#1a1a1a", "#e3e3e3", "#e8f3e8"
CURRENT, COMBINED, OTHER, YES, NO = "#2a78d6", "#00897b", "#555555", "#00897b", "#b0151d"

# (group, [(label, case, half-size BESS case, combined case)]); None where the row has no such variant
ROWS = [("Base seed", [("Official configuration", "base", None, "band1v_bess050")]),
        ("Matched seeds", [(f"Seed {k}", f"seed_{k:02d}", f"bess050_seed_{k:02d}", f"combo_seed_{k:02d}")
                           for k in range(1, 11)]),
        ("15-min step", [("15 min", "resolution_15min", None, "combo_15min"),
                         ("15 min, BESS recalibrated", "resolution_15min_recal", None, None)]),
        ("Dispatch rules", [("BESS peak shaving", "conv_bess_peak_shave", None, None),
                            ("V2G heuristic", "conv_v2g_heuristic", None, None),
                            ("Both conventional", "conv_both", None, None)]),
        ("Regulators", [("Set point 1.02 pu", "reg_vreg_102", None, None),
                        ("Set point 1.04 pu", "reg_vreg_104", None, None),
                        ("Band 1 V", "reg_band_1v", None, None),
                        ("Band 3 V", "reg_band_3v", None, None)]),
        ("Load", [("Load × 0.9 †", "load_090", None, None), ("Load × 1.1", "load_110", None, None)]),
        ("Sizing", [(f"BESS × {s:g}", f"bess_{round(100*s):03d}", None, None) for s in (.5, .75, 1.25, 1.5)]
                   + [(f"{n} V2G EVs", f"v2g_{n:03d}", None, None) for n in (50, 100, 200, 250)])]
# Cases not drawn because their S4 result equals a drawn case (checked in case_rows)
SAME_AS = {**{f"band1v_seed_{k:02d}": f"combo_seed_{k:02d}" for k in range(1, 11)},
           "band1v_15min": "combo_15min"}
COMPARED = ("v_min_pu", "line_max_pct", "new_overloads", "success")


def family(case):
    """('seed', 3) for seed_03, ('combo_seed', 3) for combo_seed_03, (None, None) otherwise."""
    m = re.fullmatch(r"(.*seed)_(\d+)", case)
    known = ("seed", "bess050_seed", "band1v_seed", "combo_seed")
    return (m.group(1), int(m.group(2))) if m and m.group(1) in known else (None, None)


def thermal_peaks(sens_dir, base_tables):
    """{case: (S4 highest loading among lines at most 100 % in that case's S0 (%), new-overload count)}."""
    from integration.resumo import line_peaks
    cols = ["time", "line", "element_type", "physical_phase", "loading_pct"]
    out = {}
    for folder in sorted(p for p in Path(sens_dir).iterdir() if (p/"dados"/"line_peaks.csv").exists()):
        p = pd.read_csv(folder/"dados"/"line_peaks.csv", index_col="line")
        out[folder.name] = p.S4[p.S0 <= LOAD_LIMIT]
    base = {sc: line_peaks(pd.read_csv(Path(base_tables)/f"{sc}_branches.csv", usecols=cols))[0] for sc in ("S0", "S4")}
    out["base"] = base["S4"][base["S0"].reindex(base["S4"].index) <= LOAD_LIMIT]
    return {case: (float(s.max()), int((s > LOAD_LIMIT).sum())) for case, s in out.items()}


def case_table(sens_csv, peaks):
    """S4 row of every case with the thermal peak; raises if a count differs from sensitivity.csv."""
    s4 = pd.read_csv(sens_csv)
    s4 = s4[(s4.scenario == "S4") & (s4.status == "ok")].set_index("case")
    missing = sorted(set(s4.index)-set(peaks))
    if missing:
        raise ValueError(f"No line peaks for {missing}")
    s4["line_max_pct"] = [peaks[c][0] for c in s4.index]
    counts = pd.Series({c: peaks[c][1] for c in s4.index})
    wrong = counts[counts != s4.new_overloads]
    if len(wrong):
        raise ValueError(f"Line peaks do not reproduce sensitivity.csv new_overloads for {list(wrong.index)}")
    return s4.reset_index()


def case_rows(cases):
    """Rows of the figure; checks that every case is drawn or equals a drawn case (SAME_AS)."""
    t = cases.set_index("case")
    drawn = {c for _, rows in ROWS for r in rows for c in r[1:] if c}
    for case, twin in SAME_AS.items():
        if case in t.index and not all(np.isclose(t.at[case, k], t.at[twin, k]) if k != "success"
                                       else t.at[case, k] == t.at[twin, k] for k in COMPARED):
            raise ValueError(f"{case} differs from {twin}; draw it")
    left = sorted(set(t.index)-drawn-set(SAME_AS))
    if left:
        raise ValueError(f"Cases neither drawn nor equal to a drawn case: {left}")
    rows = []
    for group, items in ROWS:
        # a variant missing from the table is dropped from its row, so the row shows only what was run
        present = [(label,case,*(v if v in t.index else None for v in variants))
                   for label,case,*variants in items if case in t.index]
        if present:
            rows.append(("header", group, None, None, None))
            rows += [("case", *r) for r in present]
    return rows, len(drawn & set(t.index))+sum(c in t.index for c in SAME_AS)


def _limit_panel(ax, xlim, limit, ok_side, title):
    from matplotlib.patches import Rectangle
    lo, hi = xlim
    x0, x1 = (limit, hi) if ok_side == "above" else (lo, limit)
    ax.axvspan(x0, x1, color=OK_FILL, zorder=0, lw=0)
    ax.axvline(limit, color=INK, lw=.6, ls=(0, (3, 1.5)), zorder=1)
    ax.set_xlim(*xlim)
    ax.set_title(title, loc="left", fontsize=LABEL_PT, weight="bold", pad=3)
    ax.tick_params(axis="x", length=2, labelsize=BODY_PT)
    ax.tick_params(axis="y", length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)


MAIN_GROUPS = ("Base seed", "Matched seeds", "15-min step")  # main text; the one-at-a-time cases go to the supplement


def sensitivity_figures(cases):
    """{'fig8_sensitivity_s4_seeds': base seed, matched seeds and 15-min cases,
    'figS2_sensitivity_s4_one_at_a_time': the other cases} from case_table()."""
    rows, _ = case_rows(cases)
    groups, current = {}, None
    for r in rows:
        current = r[1] if r[0] == "header" else current
        groups.setdefault(current, []).append(r)
    main = [r for g in MAIN_GROUPS for r in groups.get(g, [])]
    other = [r for g, rs in groups.items() if g not in MAIN_GROUPS for r in rs]
    t = cases.set_index("case")
    figs = {"fig8_sensitivity_s4_seeds": _dot_figure(t, main, seeded=True)}
    if other:
        figs["figS2_sensitivity_s4_one_at_a_time"] = _dot_figure(t, other, seeded=False)
    return figs


def _covered(t, rows):
    """Cases a figure accounts for: drawn ones plus the SAME_AS twins of drawn ones."""
    drawn = {c for r in rows if r[0] == "case" for c in r[2:] if c}
    return drawn | {c for c, twin in SAME_AS.items() if twin in drawn and c in t.index}


def _dot_figure(t, rows, seeded):
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    covered = _covered(t, rows)
    n = len(rows)
    height = TOP_MM+BOTTOM_MM+n*ROW_MM
    top, bottom = 1-TOP_MM/height, BOTTOM_MM/height
    with plt.rc_context({"font.family": FONT, "text.color": INK, "pdf.fonttype": 42, "axes.edgecolor": INK,
                         "axes.linewidth": .5, "xtick.major.width": .5, "axes.labelsize": BODY_PT}):
        fig = plt.figure(figsize=(WIDTH_MM/25.4, height/25.4), facecolor="white")
        axv = fig.add_axes([.215, bottom, .30, top-bottom])
        axl = fig.add_axes([.545, bottom, .30, top-bottom], sharey=axv)
        axc = fig.add_axes([.87, bottom, .12, top-bottom], sharey=axv)
        axv.set_ylim(n-.5, -.5)
        _limit_panel(axv, V_RANGE, V_LIMIT, "above", "(a)  S4 minimum voltage (pu)")
        _limit_panel(axl, LOAD_RANGE, LOAD_LIMIT, "below", "(b)  S4 worst line loading (%)")
        axv.set_xlabel("regulators acting", fontsize=BODY_PT)
        axl.set_xlabel("lines at most 100 % in S0", fontsize=BODY_PT)
        axc.axis("off")
        axc.set_title("Meets criterion", loc="left", fontsize=LABEL_PT, weight="bold", pad=3)
        axv.set_yticks(range(n), [r[1] for r in rows], fontsize=BODY_PT)
        for tick, r in zip(axv.get_yticklabels(), rows):
            if r[0] == "header":
                tick.set_fontweight("bold")
        plt.setp(axl.get_yticklabels(), visible=False)
        lo, hi = LOAD_RANGE
        for y, (kind, label, case, half, combo) in enumerate(rows):
            if kind == "header":
                continue
            for ax in (axv, axl):
                ax.axhline(y, color=GRID_C, lw=.4, zorder=0)
            paired = combo is not None  # this row joins the current and the combined case
            for ax, col in ((axv, "v_min_pu"), (axl, "line_max_pct")):
                x0 = t.at[case, col]
                clip = lambda x: min(max(x, lo), hi) if col == "line_max_pct" else x
                if paired:
                    x1 = t.at[combo, col]
                    ax.plot([clip(x0), clip(x1)], [y, y], color="#9a9a9a", lw=.9, zorder=2, solid_capstyle="butt")
                    if half:
                        ax.scatter(clip(t.at[half, col]), y, marker="|", s=40, color=CURRENT, lw=1., zorder=3)
                    ax.scatter(clip(x1), y, marker="o", s=16, color=COMBINED, zorder=4)
                    ax.scatter(clip(x0), y, marker="o", s=18, facecolors="white", edgecolors=CURRENT, lw=.9, zorder=5)
                else:
                    style = dict(marker="o", s=18, facecolors="white", edgecolors=CURRENT, lw=.9) if case.startswith(
                        "resolution") else dict(marker="D", s=11, color=OTHER)
                    ax.scatter(clip(x0), y, zorder=4, clip_on=False, **style)
                if col == "line_max_pct" and x0 > hi:  # off-scale: marker at the edge with the value
                    ax.annotate(f"{x0:.1f} →", (hi, y), xytext=(-5, 0), textcoords="offset points", ha="right",
                                va="center", fontsize=BODY_PT)
                elif col == "line_max_pct" and x0 < lo:
                    ax.annotate(f"← {x0:.1f}", (lo, y), xytext=(5, 0), textcoords="offset points", ha="left",
                                va="center", fontsize=BODY_PT)
            def verdict(c):
                return ("yes", YES) if t.at[c, "success"] else ("no", NO)
            word, color = verdict(case)
            axc.text(0, y, word, va="center", fontsize=BODY_PT, color=color)
            if paired:
                w2, c2 = verdict(combo)
                axc.text(.27, y, "→", va="center", fontsize=BODY_PT)
                axc.text(.45, y, w2, va="center", fontsize=BODY_PT, color=c2, weight="bold")
        axv.set_xticks([.93, .94, .95, .96])
        axl.set_xticks([97, 98, 99, 100, 101, 102, 103])

        if seeded:
            handles = [Line2D([], [], marker="o", ls="", mfc="white", mec=CURRENT, mew=.9, ms=4.5,
                              label="Current configuration"),
                       Line2D([], [], marker="|", ls="", color=CURRENT, mew=1., ms=6, label="BESS × 0.5, same seed"),
                       Line2D([], [], marker="o", ls="-", color="#9a9a9a", lw=.9, mfc=COMBINED, mec=COMBINED, ms=4,
                              label="Combined: BESS × 0.5 + 1 V band, same seed")]
        else:
            handles = [Line2D([], [], marker="D", ls="", color=OTHER, ms=3.5, label="One change from the base case")]
        handles.append(Patch(fc=OK_FILL, ec="none", label="Side that meets the criterion"))
        fig.legend(handles=handles, loc="lower left", bbox_to_anchor=(.01, -.005), ncol=2,
                   frameon=False, fontsize=BODY_PT, handletextpad=.4, columnspacing=1.5)
        ok = int(t.loc[sorted(covered), "success"].sum())
        fig.text(.99, .012, f"{len(covered)} cases; {ok} meet the criterion", ha="right", va="bottom",
                 fontsize=BODY_PT)
    return fig


def build(results="results", feeder="ieee8500"):
    root = Path(results)/feeder
    peaks = thermal_peaks(root/"sensitivity", root/"integration"/"dados")
    return sensitivity_figures(case_table(root/"sensitivity"/"sensitivity.csv", peaks))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default="results")
    ap.add_argument("--output", default="results/ieee8500/paper")
    a = ap.parse_args()
    from integration.framework_figure import save_paper_figure
    save_paper_figure(build(a.results), a.output)
    print(Path(a.output)/"fig8_sensitivity_s4_seeds.png")


if __name__ == "__main__":
    main()
