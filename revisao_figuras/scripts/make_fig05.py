"""Fig. 5 - hourly substation demand S0-S4 (pandapower integration, 1-h step), redrawn.

Data (no simulation): EVCS-BESS-V2G/results/ieee8500/integration/dados/<S>_source.csv, column p_kw
(substation active power, kW; one row per hour, t = start of interval). Same data as
paper_s1_figures.substation_figure; only the presentation changes:
  (a) absolute power, with the S1 - S0 EVCS increment shaded and peaks marked;
  (b) hourly change against S1 (S2, S3, S4 minus S1), which separates the BESS charging,
      the V2G recharge and the evening peak reduction that overlap in (a).
Scenario colours: integration.paper_style.SCENARIO_COLORS.
Outputs: figures/Fig05_Substation_Demand.{svg,pdf,png,tif}
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent.parent
DATA = HERE.parent / 'results' / 'ieee8500' / 'integration' / 'dados'
OUT = HERE / 'figures'
STEM = 'Fig05_Substation_Demand'

SC = {'S0': '#7a7a7a', 'S1': '#d94f1f', 'S2': '#c2409a', 'S3': '#1baf7a', 'S4': '#2a78d6'}
NAME = {'S0': 'S0 base', 'S1': 'S1 EVCS', 'S2': 'S2 EVCS + BESS', 'S3': 'S3 EVCS + V2G',
        'S4': 'S4 EVCS + BESS + V2G'}
INK, MUTED, GRID = '#1a1a1a', '#555555', '#e6e6e6'
V2G_WINDOW = (17, 20)   # V2G export window of the integration (manuscript Sec. 2 / Fig. 1)
LABEL_PT, BODY_PT = 8.0, 7.0

plt.rcParams.update({
    'font.family': 'Arial', 'pdf.fonttype': 42, 'svg.fonttype': 'path', 'text.color': INK,
    'axes.edgecolor': INK, 'axes.labelcolor': INK, 'axes.linewidth': .5, 'xtick.major.width': .5,
    'ytick.major.width': .5, 'xtick.major.size': 2, 'ytick.major.size': 2, 'xtick.labelsize': BODY_PT,
    'ytick.labelsize': BODY_PT, 'axes.labelsize': BODY_PT, 'legend.fontsize': BODY_PT})

p = pd.DataFrame({s: pd.read_csv(DATA / f'{s}_source.csv').p_kw.to_numpy() / 1000 for s in SC})
assert len(p) == 24, 'expected 24 hourly steps'
h = np.arange(25)


def stairs(y):
    y = np.asarray(y, float)
    return np.append(y, y[-1])


def style(ax, title):
    ax.set_title(title, loc='left', fontsize=LABEL_PT, weight='bold', pad=3)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.grid(axis='y', color=GRID, lw=.5)
    ax.set_axisbelow(True)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3))
    ax.axvspan(*V2G_WINDOW, color='#1baf7a', alpha=.07, lw=0, zorder=0)


fig = plt.figure(figsize=(140 / 25.4, 108 / 25.4), facecolor='white')
axa = fig.add_axes([.085, .42, .895, .47])
axb = fig.add_axes([.085, .085, .895, .25], sharex=axa)

# ------------------------------------------------------------------ (a) absolute power
style(axa, '(a)  Substation active power (MW)')
axa.fill_between(h, stairs(p.S0), stairs(p.S1), step='post', color=SC['S1'], alpha=.10, lw=0, zorder=1)
order = ['S0', 'S1', 'S3', 'S2', 'S4']           # S4 last: on top where it coincides with S2/S3
lw = {'S0': 1.1, 'S1': 1.1, 'S2': .9, 'S3': .9, 'S4': 1.5}
for s in order:
    axa.step(h, stairs(p[s]), where='post', color=SC[s], lw=lw[s], zorder=3, solid_joinstyle='miter')
for s in SC:                                      # peak marker at the middle of the peak hour
    k = int(p[s].idxmax())
    axa.plot(k + .5, p[s][k], 'o', ms=3.6, mfc=SC[s], mec='white', mew=.7, zorder=4)
axa.set_ylim(5.5, 15.7)
axa.set_yticks(range(6, 16, 2))
axa.text(sum(V2G_WINDOW) / 2, 15.6, 'V2G export 17–20 h', ha='center', va='top', fontsize=BODY_PT - .5,
         color='#1D7A56', style='italic')
axa.text(12.8, 10.3, 'EVCS increment\n(S1 − S0)', ha='center', va='center', fontsize=BODY_PT - .5,
         color='#AE4E17', style='italic')
# peak reduction S1 -> S4 at the common peak hour
k1, k4 = int(p.S1.idxmax()), int(p.S4.idxmax())
xr = 20.6
axa.annotate('', (xr, p.S4[k4]), (xr, p.S1[k1]),
             arrowprops=dict(arrowstyle='-|>', color=INK, lw=.6, mutation_scale=6, shrinkA=0, shrinkB=0))
for k, s in ((k1, 'S1'), (k4, 'S4')):
    axa.plot([k + .5 + .25, xr + .25], [p[s][k]] * 2, color=MUTED, lw=.4, ls=(0, (1, 1.2)), zorder=2)
axa.text(xr + .25, (p.S1[k1] + p.S4[k4]) / 2 + .25,f'−{p.S1[k1] - p.S4[k4]:.2f} MW\nS4 vs S1 peak',
         ha='left', va='center', fontsize=BODY_PT - .5, color=INK)
plt.setp(axa.get_xticklabels(), visible=False)

# legend: scenario + daily peak (identity never colour-alone)
handles = [Line2D([0], [0], color=SC[s], lw=lw[s], marker='o', ms=3.2, mfc=SC[s], mec='white', mew=.6)
           for s in SC]
labels = [f'{NAME[s]} (peak {p[s].max():.2f} MW)' for s in SC]
fig.legend(handles, labels, loc='upper left', bbox_to_anchor=(.075, .995), ncol=3, frameon=False,
           fontsize=BODY_PT - .5, handlelength=1.6, columnspacing=1.0, handletextpad=.5, labelspacing=.35)

# ------------------------------------------------------------------ (b) change against S1
style(axb, '(b)  Change against S1 (MW)')
w = .27
for i, s in enumerate(['S2', 'S3', 'S4']):
    d = (p[s] - p.S1).to_numpy()
    d[np.abs(d) < 5e-3] = 0.0                     # solver round-off (< 5 kW) drawn as zero
    axb.bar(np.arange(24) + .5 + (i - 1) * w, d, w * .88, color=SC[s], lw=0, zorder=3)
axb.axhline(0, color=INK, lw=.5, zorder=4)
axb.set_ylim(-1.9, 1.9)
axb.set_yticks([-1.5, -1, -.5, 0, .5, 1, 1.5])
axb.set_yticklabels(['−1.5', '−1.0', '−0.5', '0', '0.5', '1.0', '1.5'])
axb.set_xlabel('Hour of day (interval start)')
note = dict(fontsize=BODY_PT - .5, style='italic', color=MUTED, va='center')
axb.text(4.75, 1.3, 'BESS charging (S2, S4)', ha='left', **note)
axb.text(13.5, .62, 'V2G fleet recharge (S3, S4)', ha='center', **note)
axb.text(16.8, -1.0, 'evening peak\nreduction', ha='right', **note)
axb.legend([Patch(color=SC[s]) for s in ('S2', 'S3', 'S4')], ['S2 − S1', 'S3 − S1', 'S4 − S1'],
           loc='lower left', ncol=3, frameon=False, handlelength=1.0, columnspacing=1.0,
           bbox_to_anchor=(0, -.02))

fig.text(.985, .005, 'pandapower integration, 1-h step', ha='right', va='bottom', fontsize=BODY_PT - .5,
         color=MUTED, style='italic')

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f'{STEM}.svg', facecolor='white')
    fig.savefig(OUT / f'{STEM}.pdf', facecolor='white')
    fig.savefig(OUT / f'{STEM}.png', dpi=600, facecolor='white')
    fig.savefig(OUT / f'{STEM}.tif', dpi=600, facecolor='white', pil_kwargs={'compression': 'tiff_lzw'})
    print('written', OUT / f'{STEM}.*')
