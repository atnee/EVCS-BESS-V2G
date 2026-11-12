"""Fig. 10 - OpenDSS hourly behaviour and 24-h scenario metrics.

Reads only the files written by prepare_fig10_data.py (stored OpenDSS outputs):
  (a)-(c) hourly traces whose sources are byte-identical to the audit files or come from
          the verified re-execution of the archived replays (status reproduced_verified);
          S0, S1 and the three S4 variants are drawn (S2/S3 hourly series are kept in the
          CSV but, as in the Rev09 text, quantified only in Table 13 / panels (d)-(f));
  (d)-(f) audited 24-h case metrics: distributed S0-S4 under native controls
          (Rev09 Table 13) and the historical S4 replay (Table 14).
Outputs: figures/Fig10_OpenDSS_Comparative_Results.{svg,pdf,png}
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / 'data', ROOT / 'figures'
STEM = 'Fig10_OpenDSS_Comparative_Results'

plt.rcParams.update({
    'font.family': 'Arial', 'font.size': 7, 'axes.titlesize': 7.5, 'axes.labelsize': 7,
    'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5, 'legend.fontsize': 6.5,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.linewidth': 0.6,
    'xtick.major.width': 0.6, 'ytick.major.width': 0.6, 'xtick.major.size': 2.5, 'ytick.major.size': 2.5,
    'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'path',
    'text.color': '#1a1a1a', 'axes.labelcolor': '#1a1a1a', 'axes.edgecolor': '#333333',
})

# Scenario identity colours (validated with the dataviz palette checker, light surface:
# lightness, chroma, CVD and normal-vision separation, contrast all PASS); neutrals for S0
# and for the historical replay, whose identity is carried by line style and labels.
C = {'S0': '#7F7F7F', 'S1': '#D55E00', 'S2': '#0072B2', 'S3': '#009E73', 'S4': '#AA3377', 'S4_TINT': '#E28FC0', 'HIST': '#222222'}
GRID, MUTED = '#E3E3E3', '#555555'

# case -> (legend label, colour, linestyle, linewidth, z-order)
STYLE = {
    'S0_native': ('S0, native controls', C['S0'], '-', 1.2, 2),
    'S1_native': ('S1, native controls', C['S1'], '-', 1.2, 3),
    'S2_dist_native': ('Distributed S2 (BESS only), native controls', C['S2'], '-', 1.2, 4),
    'S3_dist_native': ('Distributed S3 (V2G only), native controls', C['S3'], '-', 1.2, 4),
    'S4_hist_native': ('Historical S4 replay, native controls', C['HIST'], (0, (5, 1.6, 1.2, 1.6)), 1.1, 5),
    'S4_dist_native': ('Distributed S4 (BESS + V2G), native controls', C['S4'], '-', 1.4, 6),
    # Lighter tint of the S4 hue so the dashed trace stays visible where it overlaps the native one.
    'S4_dist_phaseC': ('Distributed S4 (BESS + V2G), saved phase-C policy', C['S4_TINT'], (0, (3.0, 1.6)), 1.25, 7),
}
PLOT_OK = {'audit_exact', 'archive_byte_identical', 'reproduced_verified'}
HOURLY = ('S0_native', 'S1_native', 'S4_hist_native', 'S4_dist_native', 'S4_dist_phaseC')


def step_xy(hours: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Interval-start hourly values drawn as steps over [h, h+1)."""
    return np.append(hours, hours[-1] + 1), np.append(values, values[-1])


def main() -> None:
    traces = pd.read_csv(DATA / 'fig10_hourly_traces.csv')
    summary = pd.read_csv(DATA / 'fig10_summary_metrics.csv')
    prov = json.loads((DATA / 'fig10_provenance.json').read_text(encoding='utf-8'))
    drawn = [c['case'] for c in prov['cases'] if c['plotted'] and c['status'] in PLOT_OK and c['case'] in HOURLY]

    fig = plt.figure(figsize=(7.48, 8.1))
    top, bottom = fig.subfigures(2, 1, height_ratios=[3.0, 1.62], hspace=0.0)

    # ------------------------------------------------------------ (a)-(c) hourly behaviour
    axs = top.subplots(3, 1, sharex=True, gridspec_kw=dict(hspace=0.32))
    top.subplots_adjust(left=0.085, right=0.985, top=0.885, bottom=0.075)
    cols = [('substation_input_kw', 1e-3), ('new_overloaded_lines', 1.0), ('min_mv_voltage_pu', 1.0)]
    handles = {}
    for case in STYLE:
        if case not in drawn:
            continue
        label, color, ls, lw, z = STYLE[case]
        f = traces[traces.case == case].sort_values('hour')
        for ax, (col, scale) in zip(axs, cols):
            x, y = step_xy(f.hour.to_numpy(), f[col].to_numpy() * scale)
            (h,) = ax.step(x, y, where='post', color=color, ls=ls, lw=lw, zorder=z, label=label,
                           solid_joinstyle='miter')
            handles[case] = h
    titles = ['(a) Substation active-power input', '(b) Newly overloaded lines (not overloaded in native S0)',
              '(c) Minimum MV physical-phase voltage']
    ylabels = ['Power (MW)', 'Lines (count)', 'Voltage (p.u.)']
    for ax, t, yl in zip(axs, titles, ylabels):
        ax.set_title(t, loc='left', fontweight='bold', pad=3)
        ax.set_ylabel(yl)
        ax.grid(True, color=GRID, lw=0.5)
        ax.set_axisbelow(True)
    axs[1].set_ylim(-1.5, 45)
    axs[1].set_yticks([0, 10, 20, 30, 40])
    axs[2].axhline(0.95, color='#333333', lw=0.8, ls=(0, (1, 1.5)), zorder=1)
    axs[2].text(0.3, 0.9488, '0.95 p.u. study screen', ha='left', va='top', fontsize=6.2, color=MUTED)
    axs[2].set_xlim(0, 24)
    axs[2].set_xticks(range(0, 25, 3))
    axs[2].set_xlabel('Hour of day (interval start, 1-h step)')
    order = [c for c in STYLE if c in handles]
    top.legend([handles[c] for c in order], [STYLE[c][0] for c in order], loc='upper left',
               bbox_to_anchor=(0.08, 0.995), ncol=2 if len(order) > 3 else len(order), frameon=False,
               handlelength=3.0, columnspacing=1.6, handletextpad=0.6)

    # ------------------------------------------------------------ (d)-(f) 24-h case metrics
    bx = bottom.subplots(1, 3, gridspec_kw=dict(wspace=0.42))
    bottom.subplots_adjust(left=0.085, right=0.985, top=0.90, bottom=0.22)
    dist = summary[summary.group == 'distributed_native'].set_index('scenario').loc[['S0', 'S1', 'S2', 'S3', 'S4']]
    hist = summary[summary.group == 'historical_replay'].iloc[0]
    xs, xh = np.arange(5), 5.75
    sc_colors = [C[s] for s in dist.index]
    ticks = [*xs, xh]
    ticklabels = ['S0\nbase', 'S1\nEV', 'S2\nBESS', 'S3\nV2G', 'S4\nBESS\n+V2G', 'Hist.\nS4\nreplay']

    def frame(ax, title):
        ax.set_title(title, loc='left', fontweight='bold', pad=3)
        ax.set_xticks(ticks, ticklabels)
        ax.set_xlim(-0.6, 6.35)
        ax.axvline(5.05, color='#9A9A9A', lw=0.6, ls=(0, (2, 2)))
        ax.grid(True, axis='y', color=GRID, lw=0.5)
        ax.set_axisbelow(True)
        ax.tick_params(axis='x', length=0, pad=2)

    # (d) newly overloaded lines: counts, bars from zero
    ax = bx[0]
    frame(ax, '(d) Newly overloaded lines')
    vals = dist.new_overloaded_lines.to_numpy()
    ax.bar(xs, vals, width=0.62, color=sc_colors, edgecolor='white', linewidth=0.6, zorder=3)
    ax.bar([xh], [hist.new_overloaded_lines], width=0.62, color='white', edgecolor=C['HIST'],
           hatch='//////', linewidth=0.7, zorder=3)
    for x, v in [*zip(xs, vals), (xh, hist.new_overloaded_lines)]:
        ax.text(x, v + 1.0, f'{int(v)}', ha='center', va='bottom', fontsize=6.3)
    ax.set_ylim(0, 48)
    ax.set_ylabel('Lines (count)')

    # (e) maximum branch loading: stems from the 100% reference
    ax = bx[1]
    frame(ax, '(e) Maximum branch loading')
    vals = dist.max_branch_loading_pct.to_numpy()
    ax.axhline(100, color='#333333', lw=0.8, ls=(0, (1, 1.5)), zorder=1)
    ax.text(6.3, 99.4, '100% rating', fontsize=6.0, color=MUTED, va='top', ha='right')
    ax.vlines(xs, 100, vals, colors=sc_colors, lw=1.6, zorder=3)
    ax.scatter(xs, vals, s=22, c=sc_colors, zorder=4, edgecolors='white', linewidths=0.6)
    ax.vlines([xh], 100, [hist.max_branch_loading_pct], colors=C['HIST'], lw=1.2, zorder=3)
    ax.scatter([xh], [hist.max_branch_loading_pct], s=22, facecolors='white', edgecolors=C['HIST'],
               linewidths=1.0, zorder=4)
    for x, v in [*zip(xs, vals), (xh, hist.max_branch_loading_pct)]:
        ax.text(x, v + 1.1, f'{v:.2f}', ha='center', va='bottom', fontsize=5.9)
    s2, s4 = dist.loc['S2', 'max_branch_loading_pct'], dist.loc['S4', 'max_branch_loading_pct']
    ax.plot([2.2, 4.52], [s2, s2], color=MUTED, lw=0.6, ls=(0, (1.5, 1.5)), zorder=2)
    ax.annotate('', xy=(4.52, s4), xytext=(4.52, s2),
                arrowprops=dict(arrowstyle='-|>', lw=0.7, color='#1a1a1a', mutation_scale=6, shrinkA=0, shrinkB=0))
    ax.text(4.36, 125.5, 'S4 vs S2:\n−6.06 pp', ha='left', va='bottom', fontsize=6.0,
            linespacing=1.1, bbox=dict(fc='white', ec='none', pad=0.6), zorder=6)
    ax.set_ylim(96, 138)
    ax.set_ylabel('Loading (%)')

    # (f) minimum MV physical-phase voltage: stems from the 0.95 p.u. screen
    ax = bx[2]
    frame(ax, '(f) Minimum MV voltage')
    vals = dist.min_voltage_pu.to_numpy()
    ax.axhline(0.95, color='#333333', lw=0.8, ls=(0, (1, 1.5)), zorder=1)
    ax.text(-0.5, 0.9503, '0.95 p.u. screen', fontsize=6.0, color=MUTED, va='bottom', ha='left')
    ax.vlines(xs, vals, 0.95, colors=sc_colors, lw=1.6, zorder=3)
    ax.scatter(xs, vals, s=22, c=sc_colors, zorder=4, edgecolors='white', linewidths=0.6)
    ax.vlines([xh], hist.min_voltage_pu, 0.95, colors=C['HIST'], lw=1.2, zorder=3)
    ax.scatter([xh], [hist.min_voltage_pu], s=22, facecolors='white', edgecolors=C['HIST'],
               linewidths=1.0, zorder=4)
    for x, v in [*zip(xs, vals), (xh, hist.min_voltage_pu)]:
        ax.text(x, v - 0.0011, f'{v:.5f}', ha='center', va='top', fontsize=5.6, rotation=90)
    s2, s4 = dist.loc['S2', 'min_voltage_pu'], dist.loc['S4', 'min_voltage_pu']
    ax.plot([2.2, 4.52], [s2, s2], color=MUTED, lw=0.6, ls=(0, (1.5, 1.5)), zorder=2)
    ax.annotate('', xy=(4.52, s4), xytext=(4.52, s2),
                arrowprops=dict(arrowstyle='-|>', lw=0.7, color='#1a1a1a', mutation_scale=6, shrinkA=0, shrinkB=0))
    ax.text(4.55, 0.9105, 'S4 vs S2:\n+0.01210 p.u.', ha='center', va='bottom', fontsize=6.0,
            linespacing=1.1, bbox=dict(fc='white', ec='none', pad=0.6), zorder=6)
    ax.set_ylim(0.903, 0.9545)
    ax.set_yticks([0.91, 0.92, 0.93, 0.94, 0.95])
    ax.set_ylabel('Voltage (p.u.)')

    OUT.mkdir(parents=True, exist_ok=True)
    for ext, kw in (('svg', {}), ('pdf', {}), ('png', {'dpi': 600})):
        fig.savefig(OUT / f'{STEM}.{ext}', facecolor='white', **kw)
    print('hourly traces drawn:', ', '.join(drawn))
    print('not drawn:', ', '.join(c['case'] + f" ({c['status']})" for c in prov['cases'] if c['case'] not in drawn))


if __name__ == '__main__':
    main()
