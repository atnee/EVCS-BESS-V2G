"""Fig. 1 - methodological framework (schematic, no computed data).

Visual language of the original Rev09 Fig. 1 (functional colour chips, grey stage
panels with horizontal step chains, input boxes on the left, time step on each
panel), extended with the two OpenDSS stages and the comparative assessment.

Every quantity is quoted from the Rev09 manuscript (Sections 2.1-2.7, Tables 1-3
and 15) or from configs/evcs.yaml (per-minute sessions, 95% sizing quantile,
>=95% AC sessions served). Solid arrows: data flow / connected steps. Dashed
arrows: comparisons or diagnostics only.
Outputs: figures/Fig01_Methodological_Framework.{svg,pdf,png}
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

OUT = Path(__file__).resolve().parent.parent / 'figures'
STEM = 'Fig01_Methodological_Framework'

plt.rcParams.update({'font.family': 'Arial', 'pdf.fonttype': 42, 'ps.fonttype': 42,
                     'svg.fonttype': 'path', 'text.color': '#1a1a1a'})

# functional categories: (edge, fill, title ink) - same scheme as the original figure
CAT = {
    'net': ('#3A73C4', '#EAF1FB', '#22549F'),     # network model (pandapower)
    'ev': ('#E07B39', '#FCEEE5', '#AE4E17'),      # EV charging
    'plan': ('#5A3FA6', '#EEEAF7', '#43308A'),    # station planning
    'v2g': ('#2EA77A', '#E6F5EE', '#1D7A56'),     # V2G
    'bess': ('#C23A8E', '#F9E8F2', '#962B6D'),    # BESS
    'data': ('#7A7A7A', '#EFEFEF', '#444444'),    # data / scenarios
    'dss': ('#0F7C86', '#E3F2F3', '#0A5C64'),     # OpenDSS full feeder
}
PANEL_FC, PANEL_EC, INK, MUTED = '#F6F6F6', '#BDBDBD', '#1a1a1a', '#4d4d4d'
DASH = (0, (3.0, 2.0))

W, H = 7.48, 5.24
fig = plt.figure(figsize=(W, H))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis('off')


def rbox(x, y, w, h, fc, ec, lw=0.9, r=0.06, z=1, ls='-'):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f'round,pad=0,rounding_size={r}',
                                fc=fc, ec=ec, lw=lw, ls=ls, zorder=z))


def node(x, y, w, h, cat, title, detail, tsize=6.9, dsize=5.9):
    ec, fc, ink = CAT[cat]
    rbox(x, y, w, h, fc, ec, lw=1.0, z=3)
    cx = x + w / 2
    ax.text(cx, y + h - 0.075, title, ha='center', va='top', fontsize=tsize, weight='bold', color=ink, zorder=4)
    ax.text(cx, y + h - 0.225, detail, ha='center', va='top', fontsize=dsize, color=INK,
            linespacing=1.15, zorder=4)
    return dict(l=x, r=x + w, t=y + h, b=y, cx=cx, cy=y + h / 2)


def panel(x, y, w, h, label, right):
    rbox(x, y, w, h, PANEL_FC, PANEL_EC, lw=0.8, r=0.07, z=1)
    ax.text(x + 0.09, y + h - 0.07, label, ha='left', va='top', fontsize=7.0, weight='bold', color='#2b2b2b', zorder=2)
    ax.text(x + w - 0.09, y + h - 0.07, right, ha='right', va='top', fontsize=6.4, color='#2b2b2b', zorder=2)


def arrow(p, q, dashed=False, both=False, lw=0.85, z=5):
    ax.add_patch(FancyArrowPatch(p, q, arrowstyle='<|-|>' if both else '-|>', mutation_scale=6.5, lw=lw,
                                 color=INK, linestyle=DASH if dashed else '-', shrinkA=0, shrinkB=0, zorder=z))


def poly(points, dashed=False, head=True, lw=0.85, z=5):
    xs, ys = zip(*points)
    if head and len(points) > 2:
        ax.plot(xs[:-1], ys[:-1], color=INK, lw=lw, ls=DASH if dashed else '-', zorder=z,
                solid_capstyle='butt', solid_joinstyle='miter')
        arrow(points[-2], points[-1], dashed=dashed, lw=lw, z=z)
    elif head:
        arrow(points[0], points[1], dashed=dashed, lw=lw, z=z)
    else:
        ax.plot(xs, ys, color=INK, lw=lw, ls=DASH if dashed else '-', zorder=z, solid_joinstyle='miter')


def label(x, y, text, ha='center', va='center', size=5.9, style='italic', bg=False):
    kw = dict(bbox=dict(fc='white', ec='none', pad=0.6)) if bg else {}
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, style=style, color=MUTED, zorder=7, **kw)


def chain(nodes):
    for a, b in zip(nodes[:-1], nodes[1:]):
        arrow((a['r'], a['cy']), (b['l'], b['cy']))


# ------------------------------------------------------------------ geometry
LX, LW = 0.06, 1.10            # input column
PX, PW = 1.26, 5.99            # stage panels (right edge 7.25; gutter for the output bus)
IX, IW = PX + 0.08, PW - 0.16  # inner area of the panels
G = 0.16                       # gap between step boxes
BH = 0.46                      # step-box height


def grid(n):
    w = (IW - (n - 1) * G) / n
    return [IX + i * (w + G) for i in range(n)], w


# vertical layout (top -> bottom)
ya_t, ya_h = 4.92, 0.84
yb_t, yb_h = 3.96, 0.76
band_t, band_h = 3.06, 0.36
yc_t, yc_h = 2.56, 0.76
yd_t, yd_h = 1.56, 0.76
asm_t, asm_h = 0.72, 0.66

# ------------------------------------------------------------------ inputs (pandapower stages)
inp_h = 0.58
inputs = [
    ('net', 'IEEE 8500 feeder', 'pandapower, 2,521 MV\nbuses; 24-h load curve'),
    ('ev', 'EV fleet', '3,000 EVs; 85% private,\n15% taxi/ride-hailing'),
]
ins = []
ins_cy = [ya_t - 0.24 - 0.46 / 2, yb_t - 0.24 - 0.46 / 2]  # centred on the first step of (a) and (b)
for (cat, t, d), cy in zip(inputs, ins_cy):
    ins.append(node(LX, cy - inp_h / 2, LW, inp_h, cat, t, d, tsize=6.8, dsize=5.7))

# ------------------------------------------------------------------ (a) EVCS planning
panel(PX, ya_t - ya_h, PW, ya_h, '(a) EVCS planning and impact study (S0, S1) — pandapower',
      'Δt = 15 min, T = 96')
xs4, w4 = grid(4)
ya_box = ya_t - 0.24 - BH
row_a = [
    node(xs4[0], ya_box, w4, BH, 'ev', 'Sessions and sizing', 'Monte Carlo, 1-min resolution;\n95% quantile of concurrency'),
    node(xs4[1], ya_box, w4, BH, 'plan', 'Siting', 'DC: max. coverage\nAC: MILP'),
    node(xs4[2], ya_box, w4, BH, 'ev', 'Operation', 'FIFO queues,\n≥95% AC served'),
    node(xs4[3], ya_box, w4, BH, 'net', 'Power flow', 'two regulator views; voltage,\nloading, losses, taps'),
]
chain(row_a)
yl = ya_box - 0.09
poly([(row_a[2]['cx'], ya_box), (row_a[2]['cx'], yl), (row_a[1]['cx'], yl), (row_a[1]['cx'], ya_box)])
label((row_a[1]['cx'] + row_a[2]['cx']) / 2, yl, 'no: resize', bg=True)

# ------------------------------------------------------------------ (b) integrated S0-S4
panel(PX, yb_t - yb_h, PW, yb_h, '(b) Integrated scenarios S0–S4 — pandapower',
      'Δt = 1 h, T = 24; sensitivity: 63 configurations')
xs5, w5 = grid(5)
yb_box = yb_t - 0.24 - BH
row_b = [
    node(xs5[0], yb_box, w5, BH, 'ev', 'EVCS demand', '2,850 EVs,\n75 sites from (a)'),
    node(xs5[1], yb_box, w5, BH, 'v2g', 'V2G dispatch', 'LP, 150 EVs,\nexport 17–20 h'),
    node(xs5[2], yb_box, w5, BH, 'bess', 'BESS dispatch', '2.2 MW / 7.2 MWh;\ncalibrated line-limit LP'),
    node(xs5[3], yb_box, w5, BH, 'net', 'Power flow', 'S0 base; S1–S4: EVCS\n± BESS ± V2G; two views'),
    node(xs5[4], yb_box, w5, BH, 'net', 'Criterion', 'no new overload,\nmin V ≥ 0.95 p.u.'),
]
chain(row_b)

# input bus -> first steps of (a) and (b)
bus_x = LX + LW + 0.06
for n in ins:
    poly([(n['r'], n['cy']), (bus_x, n['cy'])], head=False)
poly([(bus_x, row_a[0]['cy']), (bus_x, row_b[0]['cy'])], head=False)
arrow((bus_x, row_a[0]['cy']), (row_a[0]['l'], row_a[0]['cy']))
arrow((bus_x, row_b[0]['cy']), (row_b[0]['l'], row_b[0]['cy']))


def small(x, y, w, h, cat, title, detail):
    """Auxiliary box: title and one detail line."""
    ec, fc, ink = CAT[cat]
    rbox(x, y, w, h, fc, ec, lw=0.9, z=3)
    ax.text(x + w / 2, y + h - 0.06, title, ha='center', va='top', fontsize=6.4, weight='bold', color=ink, zorder=4)
    ax.text(x + w / 2, y + 0.07, detail, ha='center', va='bottom', fontsize=5.7, color=INK, zorder=4)
    return dict(l=x, r=x + w, t=y + h, b=y, cx=x + w / 2, cy=y + h / 2)


# ------------------------------------------------------------------ S0 paired comparison (diagnostic)
band_b = band_t - band_h
s0c = small(row_b[3]['cx'] + 0.05, band_b, PX + PW - row_b[3]['cx'] - 0.05, band_h, 'data',
            'S0 paired comparison (diagnostic)', 'vs OpenDSS MV equivalent, fixed taps')
xpf = row_b[3]['cx'] + 0.30
arrow((xpf, row_b[3]['b']), (xpf, s0c['t']), dashed=True, both=True)

# ------------------------------------------------------------------ OpenDSS feeder input
feed_h = 0.50
feed_y = ((yc_t - 0.24 - BH / 2) + (yd_t - 0.24 - BH / 2)) / 2 - feed_h / 2
feed = node(LX, feed_y, LW, feed_h, 'dss', 'IEEE 8500 feeder', 'OpenDSS, full\nnetwork', tsize=6.8, dsize=5.7)

# ------------------------------------------------------------------ (c) and (d): resources -> OpenDSS replay
wl, wr = 2 * w4 + G, 2 * w4 + G
xl, xr = xs4[0], xs4[2]
panel(PX, yc_t - yc_h, PW, yc_h, '(c) Model-transfer diagnostic — historical single-site resources',
      'Δt = 1 h, T = 24')
yc_box = yc_t - 0.24 - BH
row_c = [
    node(xl, yc_box, wl, BH, 'data', 'Historical resources', 'one BESS site,\none V2G host (150 EVs)'),
    node(xr, yc_box, wr, BH, 'dss', 'OpenDSS replay and indicators',
         'native and S0-frozen taps\nnew vs pre-existing overloads, voltage range'),
]
chain(row_c)
# saved historical injections: (b) Power flow -> (c) resources (data flow)
xi = row_b[3]['cx'] - 0.30
arrow((xi, row_b[3]['b']), (xi, row_c[1]['t']))

panel(PX, yd_t - yd_h, PW, yd_h, '(d) Distributed BESS–V2G configuration', 'Δt = 1 h, T = 24')
yd_box = yd_t - 0.24 - BH
row_d = [
    node(xl, yd_box, wl, BH, 'data', 'Distributed resources', '3 BESS sites (2.2 MW / 7.2 MWh in total)\n3 V2G hosts (150 EVs)'),
    node(xr, yd_box, wr, BH, 'dss', 'OpenDSS replay and indicators',
         'S2, S3, S4; native controls and phase-C policy\nincremental benefit, residual violations'),
]
chain(row_d)

# feeder input -> first steps of (c) and (d)
fbus = LX + LW + 0.06
poly([(feed['r'], feed['cy']), (fbus, feed['cy'])], head=False)
poly([(fbus, row_c[0]['cy']), (fbus, row_d[0]['cy'])], head=False)
arrow((fbus, row_c[0]['cy']), (row_c[0]['l'], row_c[0]['cy']))
arrow((fbus, row_d[0]['cy']), (row_d[0]['l'], row_d[0]['cy']))

# between the two replays: shared native S0/S1 (data flow) and configuration comparison (comparison only)
ymid = (yc_t - yc_h + yd_t) / 2
xs01 = row_c[1]['l'] + 0.45
arrow((xs01, row_c[1]['b']), (xs01, row_d[1]['t']))
xcc = row_c[1]['r'] - 1.00
arrow((xcc, row_c[1]['b']), (xcc, row_d[1]['t']), dashed=True, both=True)
label(xcc - 0.06, ymid, 'configuration comparison\n(siting and dispatch differ)', ha='right', size=5.6)

# ------------------------------------------------------------------ comparative assessment
asm_b = asm_t - asm_h
right = PX + PW + 0.17
rbox(LX, asm_b, right - LX, asm_h, 'white', '#555555', lw=1.0, r=0.07, z=1)
ax.text(LX + 0.10, asm_t - 0.07, 'Comparative assessment', ha='left', va='top', fontsize=7.0, weight='bold',
        color='#2b2b2b')
items = ['Voltage disagreement between models', 'New vs pre-existing overloads', 'Maximum branch loading',
         'Physical-phase voltage range', 'BESS-only, V2G-only, combined', 'Limits of cross-model interpretation']
colx = [LX + 0.14, LX + 1.90, LX + 3.55]
for i, t in enumerate(items):
    ax.text(colx[i // 2], asm_t - 0.33 - (i % 2) * 0.18, '•  ' + t, ha='left', va='center', fontsize=6.1, color=INK)
concl_l = 5.50
rbox(concl_l, asm_b + 0.10, right - concl_l - 0.10, asm_h - 0.20, 'white', '#1a1a1a', lw=1.3, r=0.06, z=2)
ax.text((concl_l + right - 0.10) / 2, asm_b + asm_h / 2,
        'Model-conditional findings,\nnot feeder-wide feasibility\ncertification', ha='center', va='center',
        fontsize=6.6, weight='bold', linespacing=1.12, zorder=3)
arrow((concl_l - 0.22, asm_b + asm_h / 2), (concl_l, asm_b + asm_h / 2))

# output bus in the right gutter: OpenDSS indicators and S0 diagnostic -> assessment (data flow)
gx = PX + PW + 0.10
for n in (s0c, row_c[1], row_d[1]):
    poly([(n['r'], n['cy']), (gx, n['cy'])], head=False)
poly([(gx, s0c['cy']), (gx, asm_t)])

# ------------------------------------------------------------------ legend (top)
chips = [('net', 'Network model'), ('ev', 'EV charging'), ('plan', 'Station planning'), ('v2g', 'V2G'),
         ('bess', 'BESS'), ('data', 'Data / scenarios'), ('dss', 'OpenDSS')]
handles = [Patch(fc=CAT[c][1], ec=CAT[c][0], lw=1.0) for c, _ in chips]
labels = [t for _, t in chips]
handles += [Line2D([0], [0], color=INK, lw=0.9), Line2D([0], [0], color=INK, lw=0.9, ls=DASH)]
labels += ['Data flow', 'Comparison only']
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.999), ncol=9, frameon=False, fontsize=6.2,
           handlelength=1.5, handleheight=0.9, columnspacing=1.0, handletextpad=0.4, borderaxespad=0.2)

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f'{STEM}.svg', facecolor='white')
    fig.savefig(OUT / f'{STEM}.pdf', facecolor='white')
    fig.savefig(OUT / f'{STEM}.png', dpi=600, facecolor='white')
    print('written', OUT / f'{STEM}.*')
