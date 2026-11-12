"""Fig. 1 (Rev11 layout) - methodological framework (schematic, no computed data).

High-resolution redraw of the compact Fig. 1 embedded in
EVCS_BESS_V2G_Applied_Energy_Rev11.docx (word/media/image1.png, which only exists
as a low-resolution raster). Layout and wording follow that image; visual language
(colour chips, grey stage panels, step chains) is shared with make_fig01.py.

Solid arrows: data flow. Dashed arrows: comparisons or diagnostics only.
Outputs: figures/Fig01_Rev11_Methodological_Framework.{svg,pdf,png,tif}
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

OUT = Path(__file__).resolve().parent.parent / 'figures'
STEM = 'Fig01_Rev11_Methodological_Framework'

plt.rcParams.update({'font.family': 'Arial', 'pdf.fonttype': 42, 'ps.fonttype': 42,
                     'svg.fonttype': 'path', 'text.color': '#1a1a1a'})

# functional categories: (edge, fill, title ink)
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

W, H = 7.48, 5.45
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
G = 0.14                       # gap between step boxes
BH = 0.50                      # step-box height


def grid(n):
    w = (IW - (n - 1) * G) / n
    return [IX + i * (w + G) for i in range(n)], w


# vertical layout (top -> bottom)
ya_t, ya_h = 5.18, 0.88
yb_t, yb_h = 4.20, 0.82
band_t, band_h = 3.30, 0.40
yc_t, yc_h = 2.78, 0.82
yd_t, yd_h = 1.66, 0.82
asm_t, asm_h = 0.74, 0.68


def row_y(top):
    return top - 0.25 - BH


# ------------------------------------------------------------------ (a) EVCS planning
panel(PX, ya_t - ya_h, PW, ya_h, '(a) EVCS planning and impact study (S0, S1) — pandapower',
      'Δt = 15 min, T = 96')
xs4, w4 = grid(4)
ya_box = row_y(ya_t)
row_a = [
    node(xs4[0], ya_box, w4, BH, 'ev', 'Sessions and sizing', 'Monte Carlo, 1-min resolution;\n95% quantile of concurrency'),
    node(xs4[1], ya_box, w4, BH, 'plan', 'Siting', 'DC: max. coverage\nAC: MILP'),
    node(xs4[2], ya_box, w4, BH, 'ev', 'Operation', 'FIFO queues,\n≥95% AC served'),
    node(xs4[3], ya_box, w4, BH, 'net', 'Power flow', 'two regulator views; voltage,\nloading, losses, taps'),
]
chain(row_a)
yl = ya_box - 0.08
poly([(row_a[2]['cx'], ya_box), (row_a[2]['cx'], yl), (row_a[1]['cx'], yl), (row_a[1]['cx'], ya_box)])
label((row_a[1]['cx'] + row_a[2]['cx']) / 2, yl, 'no: resize', size=5.6, bg=True)

# ------------------------------------------------------------------ (b) integrated S0-S4
panel(PX, yb_t - yb_h, PW, yb_h, '(b) Integrated scenarios S0–S4 — pandapower',
      'Δt = 1 h, T = 24; sensitivity: 63 configurations')
xs5, w5 = grid(5)
yb_box = row_y(yb_t)
row_b = [
    node(xs5[0], yb_box, w5, BH, 'ev', 'EVCS demand', '2,850 EVs,\n75 sites from (a)'),
    node(xs5[1], yb_box, w5, BH, 'v2g', 'V2G dispatch', 'LP, 150 EVs,\nexport 17–20 h'),
    node(xs5[2], yb_box, w5, BH, 'bess', 'BESS dispatch', '2.2 MW / 7.2 MWh;\ncalibrated line-limit LP'),
    node(xs5[3], yb_box, w5, BH, 'net', 'Power flow', 'S0 base; S1–S4: EVCS\n± BESS ± V2G; two views'),
    node(xs5[4], yb_box, w5, BH, 'net', 'Criterion', 'no new overload,\nmin V ≥ 0.95 p.u.'),
]
chain(row_b)
pf_b = row_b[3]

# ------------------------------------------------------------------ inputs (pandapower stages)
inp_h = BH
ins = [
    node(LX, row_a[0]['cy'] - inp_h / 2, LW, inp_h, 'net', 'IEEE 8500 feeder',
         'pandapower, 2,521 MV\nbuses; 24-h load curve', dsize=5.8),
    node(LX, row_b[0]['cy'] - inp_h / 2, LW, inp_h, 'ev', 'EV fleet',
         '3,000 EVs; 85% private,\n15% taxi/ride-hailing', dsize=5.8),
]
bus_x = LX + LW + 0.05
for n in ins:
    poly([(n['r'], n['cy']), (bus_x, n['cy'])], head=False)
poly([(bus_x, ins[0]['cy']), (bus_x, ins[1]['cy'])], head=False)
arrow((bus_x, row_a[0]['cy']), (row_a[0]['l'], row_a[0]['cy']))
arrow((bus_x, row_b[0]['cy']), (row_b[0]['l'], row_b[0]['cy']))

# ------------------------------------------------------------------ S0 paired comparison (diagnostic)
s0c = node(pf_b['cx'] - 0.10, band_t - band_h, PX + PW - (pf_b['cx'] - 0.10), band_h, 'data',
           'S0 paired comparison (diagnostic)', 'vs OpenDSS MV equivalent, fixed taps', tsize=6.6, dsize=5.8)
xd = pf_b['r'] - 0.16
arrow((xd, pf_b['b']), (xd, s0c['t']), dashed=True, both=True)

# ------------------------------------------------------------------ (c) model-transfer diagnostic
panel(PX, yc_t - yc_h, PW, yc_h, '(c) Model-transfer diagnostic — historical single-site resources',
      'Δt = 1 h, T = 24')
xs2, w2 = grid(2)
yc_box = row_y(yc_t)
row_c = [
    node(xs2[0], yc_box, w2, BH, 'data', 'Historical resources', 'one BESS site,\none V2G host (150 EVs)'),
    node(xs2[1], yc_box, w2, BH, 'dss', 'OpenDSS replay and indicators',
         'native and S0-frozen taps;\nnew vs pre-existing overloads, voltage range'),
]
chain(row_c)
# saved S0-S4 injections: (b) Power flow -> (c) OpenDSS replay (data flow)
xi = pf_b['l'] + 0.20
arrow((xi, pf_b['b']), (xi, row_c[1]['t']))

# ------------------------------------------------------------------ (d) distributed configuration
panel(PX, yd_t - yd_h, PW, yd_h, '(d) Distributed BESS–V2G configuration', 'Δt = 1 h, T = 24')
yd_box = row_y(yd_t)
row_d = [
    node(xs2[0], yd_box, w2, BH, 'data', 'Distributed resources',
         '3 BESS sites (2.2 MW / 7.2 MWh in total)\n3 V2G hosts (150 EVs)'),
    node(xs2[1], yd_box, w2, BH, 'dss', 'OpenDSS replay and indicators',
         'S2, S3, S4; native controls and phase-C policy\nincremental benefit, residual violations'),
]
chain(row_d)

# shared native S0/S1: (c) replay -> (d) replay (data flow)
xs01 = row_c[1]['l'] + 0.45
arrow((xs01, row_c[1]['b']), (xs01, row_d[1]['t']))
# configuration comparison (comparison only)
xcc = row_c[1]['l'] + 1.86
arrow((xcc, row_c[1]['b']), (xcc, row_d[1]['t']), dashed=True, both=True)
label(xcc - 0.07, (yc_t - yc_h + yd_t) / 2, 'configuration comparison\n(siting and dispatch differ)',
      ha='right', size=5.6)

# ------------------------------------------------------------------ input (OpenDSS stages)
feed_h = inp_h
feed_y = (row_c[0]['cy'] + row_d[0]['cy']) / 2 - feed_h / 2
feed = node(LX, feed_y, LW, feed_h, 'dss', 'IEEE 8500 feeder', 'OpenDSS, full\nnetwork', dsize=5.8)
fbus = LX + LW + 0.05
poly([(feed['r'], feed['cy']), (fbus, feed['cy'])], head=False)
poly([(fbus, row_c[0]['cy']), (fbus, row_d[0]['cy'])], head=False)
arrow((fbus, row_c[0]['cy']), (row_c[0]['l'], row_c[0]['cy']))
arrow((fbus, row_d[0]['cy']), (row_d[0]['l'], row_d[0]['cy']))

# ------------------------------------------------------------------ comparative assessment
asm_b = asm_t - asm_h
asm_r = PX + PW + 0.17
rbox(LX, asm_b, asm_r - LX, asm_h, 'white', '#555555', lw=1.0, r=0.07, z=1)
ax.text(LX + 0.10, asm_t - 0.07, 'Comparative assessment', ha='left', va='top', fontsize=7.0, weight='bold',
        color='#2b2b2b')
items = ['Voltage disagreement between models', 'New vs pre-existing overloads',
         'Maximum branch loading', 'Physical-phase voltage range',
         'BESS-only, V2G-only, combined', 'Limits of cross-model interpretation']
colx = [LX + 0.14, LX + 1.95, LX + 3.55]
for i, t in enumerate(items):
    cx_, cy_ = colx[i // 2], asm_t - 0.32 - (i % 2) * 0.19
    ax.text(cx_, cy_, '•  ' + t, ha='left', va='center', fontsize=6.2, color=INK)
concl_l = 5.62
concl_r = asm_r - 0.09
rbox(concl_l, asm_b + 0.08, concl_r - concl_l, asm_h - 0.16, 'white', '#1a1a1a', lw=1.3, r=0.06, z=2)
ax.text((concl_l + concl_r) / 2, asm_b + asm_h / 2,
        'Model-conditional findings,\nnot feeder-wide feasibility\ncertification', ha='center', va='center',
        fontsize=6.9, weight='bold', linespacing=1.15, zorder=3)
arrow((concl_l - 0.32, asm_b + asm_h / 2), (concl_l, asm_b + asm_h / 2))

# output bus in the right gutter: S0 diagnostic and OpenDSS indicators -> assessment (data flow)
gx = PX + PW + 0.09
for n in (s0c, row_c[1], row_d[1]):
    poly([(n['r'], n['cy']), (gx, n['cy'])], head=False)
arrow((gx, s0c['cy']), (gx, asm_t))

# ------------------------------------------------------------------ legend (top)
chips = [('net', 'Network model'), ('ev', 'EV charging'), ('plan', 'Station planning'), ('v2g', 'V2G'),
         ('bess', 'BESS'), ('data', 'Data / scenarios'), ('dss', 'OpenDSS')]
handles = [Patch(fc=CAT[c][1], ec=CAT[c][0], lw=1.0) for c, _ in chips]
labels = [t for _, t in chips]
handles += [Line2D([0], [0], color=INK, lw=0.9), Line2D([0], [0], color=INK, lw=0.9, ls=DASH)]
labels += ['Data flow', 'Comparison only']
fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 0.999), ncol=9, frameon=False, fontsize=6.3,
           handlelength=1.8, handleheight=0.9, columnspacing=1.2, handletextpad=0.45, borderaxespad=0.2)

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f'{STEM}.svg', facecolor='white')
    fig.savefig(OUT / f'{STEM}.pdf', facecolor='white')
    fig.savefig(OUT / f'{STEM}.png', dpi=600, facecolor='white')
    fig.savefig(OUT / f'{STEM}.tif', dpi=600, facecolor='white', pil_kwargs={'compression': 'tiff_lzw'})
    print('written', OUT / f'{STEM}.*')
