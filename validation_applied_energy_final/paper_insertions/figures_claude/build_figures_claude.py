"""Redraw candidate Figs. 9-11 of the Applied Energy manuscript from saved results.

Reads only saved CSV/JSON outputs: no power flow and no optimisation is run. Every plotted number is recomputed
from the raw phase-level files and checked against the tables of the paper_insertions package and against the
check values stated for the revision. A figure whose check fails is not drawn; the failing source and calculation
are written to verification.json and the script exits with status 1.

Run from anywhere (paths are resolved from this file):

    python validation_applied_energy_final/paper_insertions/figures_claude/build_figures_claude.py [--replace]

Outputs are written only to this folder. Without --replace the script refuses to overwrite existing outputs.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import hashlib
import importlib.util
import json
import math
import platform
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator
import numpy as np
import pandas as pd
from PIL import Image

OUT = Path(__file__).resolve().parent                 # .../paper_insertions/figures_claude
PKG = OUT.parent                                      # .../paper_insertions
VAL = PKG.parent                                      # .../validation_applied_energy_final
B = VAL.parent                                        # project B (Distributed-Stochastic)
A = B.parent / 'EVCS-BESS-V2G'                        # project A (historical)
DATA = OUT / 'data'

PILOT, CONTROLS = VAL / 'exact_profile_pilot', VAL / 'alternative_controls'
DT_H = 1.0                                            # every OpenDSS replay uses 1-h intervals
V_LOW, V_HIGH, LOAD_LIMIT = .95, 1.05, 100.           # study screens (ANSI C84.1 range A; 100 % rating)
PNG_DPI, TIFF_DPI = 600, 1000                         # Elsevier: line art >= 1000 dpi; PNG for review only
FIG9_HOUR_COLOURS = {3: '#9ab8d8', 18: '#2a5f99'}     # light/dark blue: ordered hours, distinct in greyscale
FIG9_MEDIAN_COLOURS = {3: '#1a1a1a', 18: '#ffffff'}   # median line contrasts with its box fill

# Check values given for the revision (rounded as stated): a mismatch stops the figure.
CHECK = dict(rmse_h03=.02270, rmse_h18=.07277, pairs=3820, new_s1=41, new_s4_single=23, new_dist=0,
             vmin_s4_single=.92357, vmin_dist_native=.93648, vmin_dist_policy=.93752)
CHECK_TOL = 5e-6                                      # half a unit of the fifth decimal

INPUTS: dict[str, dict[str, str]] = {}


class CheckFailed(Exception):
    """Numerical divergence: the figure is not drawn."""


# ------------------------------------------------------------------------------------------------ io helpers
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def register(key: str, path: Path) -> Path:
    path = path.resolve(strict=True)
    INPUTS[key] = {'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size}
    return path


def read_csv(key: str, path: Path) -> pd.DataFrame:
    return pd.read_csv(register(key, path), encoding='utf-8-sig')


def load_style():
    """Common style of Figs. 1-8 (src/integration/paper_style.py), loaded by path so that importing it does not
    import the rest of the integration package."""
    path = register('paper_style', B / 'src' / 'integration' / 'paper_style.py')
    spec = importlib.util.spec_from_file_location('paper_style', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str, **evidence) -> None:
    if not condition:
        raise CheckFailed(json.dumps({'check': message, **{k: _plain(v) for k, v in evidence.items()}},
                                     ensure_ascii=False))


def _plain(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def close(a: float, b: float, tol: float = 1e-9) -> bool:
    """Equality up to the 10 significant digits written in the package tables."""
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=tol)


# ----------------------------------------------------------------------------------------------- outputs
def save_figure(fig: plt.Figure, stem: str, produced: list[Path]) -> None:
    """Vector PDF (TrueType fonts embedded, no raster), RGB PNG for review and RGB LZW TIFF for submission.
    No bbox cropping: the files keep the designed print size in mm."""
    pdf = OUT / f'{stem}.pdf'
    fig.savefig(pdf, format='pdf', facecolor='white', metadata={'Creator': 'build_figures_claude.py',
                                                                 'Producer': None, 'CreationDate': None})
    for dpi, suffix, fmt, extra in ((PNG_DPI, 'png', 'PNG', {}),
                                     (TIFF_DPI, 'tif', 'TIFF', {'compression': 'tiff_lzw'})):
        buffer = BytesIO()
        fig.savefig(buffer, format='png', dpi=dpi, facecolor='white')
        buffer.seek(0)
        with Image.open(buffer) as image:
            image.convert('RGB').save(OUT / f'{stem}.{suffix}', fmt, dpi=(dpi, dpi), **extra)
    plt.close(fig)
    produced += [OUT / f'{stem}.{s}' for s in ('pdf', 'png', 'tif')]


def mm(width: float, height: float) -> tuple[float, float]:
    return width / 25.4, height / 25.4


def words(n: int) -> str:
    return {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}.get(int(n), f'{int(n):,}')


def thousands(x, _pos=None) -> str:
    return f'{x:,.0f}'


# ------------------------------------------------------------------------------------- phase-level replays
def read_replay(key: str, folder: Path) -> dict[str, pd.DataFrame]:
    """source.csv, voltages.csv and branches.csv of one 24-h OpenDSS replay, with structural checks."""
    s = read_csv(f'{key}:source', folder / 'source.csv')
    v = read_csv(f'{key}:voltages', folder / 'voltages.csv')
    b = read_csv(f'{key}:branches', folder / 'branches.csv')
    hours = list(range(24))
    require(sorted(s.hour) == hours, f'{key}: source must have one row per hour 0-23', hours=sorted(s.hour))
    for name, frame, keys in (('voltages', v, ['hour', 'bus', 'phase']), ('branches', b, ['hour', 'line', 'phase'])):
        require(sorted(frame.hour.unique()) == hours, f'{key}: {name} missing hours')
        dup = int(frame.duplicated(keys).sum())
        require(dup == 0, f'{key}: duplicated {"-".join(keys)} keys in {name}', duplicates=dup)
        require(frame.notna().all().all(), f'{key}: missing values in {name}')
        per_hour = frame.groupby('hour').size()
        require(per_hour.nunique() == 1, f'{key}: {name} rows differ between hours', rows=per_hour.to_dict())
    require(np.allclose(b.loading_pct, b.current_a / b.ampacity_a * 100, rtol=1e-9),
            f'{key}: loading_pct != current_a / ampacity_a * 100')
    return dict(source=s.sort_values('hour').reset_index(drop=True), voltages=v, branches=b)


def hourly_indicators(r: dict[str, pd.DataFrame], base_lines: set[str]) -> pd.DataFrame:
    """Hourly series. Severities sum positive phase-level excesses over all physical bus-phases or line-phases of
    the hour; daily totals multiply by DT_H. New lines: line identities above 100 % in the hour that were never
    above 100 % in any hour of native-control S0 (identity comparison, not a difference of counts)."""
    v, b = r['voltages'], r['branches']
    idx = range(24)
    over = b[b.loading_pct > LOAD_LIMIT]
    return pd.DataFrame(dict(
        hour=list(idx),
        source_kw=r['source'].p_kw.to_numpy(),
        min_voltage_pu=v.groupby('hour').v_pu.min().reindex(idx).to_numpy(),
        new_overloaded_lines=over[~over.line.isin(base_lines)].groupby('hour').line.nunique()
                                 .reindex(idx, fill_value=0).to_numpy(),
        thermal_excess_pp=(b.loading_pct - LOAD_LIMIT).clip(lower=0).groupby(b.hour).sum().reindex(idx).to_numpy(),
        undervoltage_deficit_pu=(V_LOW - v.v_pu).clip(lower=0).groupby(v.hour).sum().reindex(idx).to_numpy(),
        overvoltage_excess_pu=(v.v_pu - V_HIGH).clip(lower=0).groupby(v.hour).sum().reindex(idx).to_numpy(),
    ))


def daily_summary(r: dict[str, pd.DataFrame], hours: pd.DataFrame, base_lines: set[str]) -> dict:
    b = r['branches']
    return dict(peak_kw=float(r['source'].p_kw.max()), min_voltage_pu=float(r['voltages'].v_pu.min()),
                max_voltage_pu=float(r['voltages'].v_pu.max()), max_loading_pct=float(b.loading_pct.max()),
                new_overloaded_lines_vs_s0=len(set(b.loc[b.loading_pct > LOAD_LIMIT, 'line']) - base_lines),
                thermal_excess_pph=float(hours.thermal_excess_pp.sum() * DT_H),
                undervoltage_deficit_puh=float(hours.undervoltage_deficit_pu.sum() * DT_H),
                overvoltage_excess_puh=float(hours.overvoltage_excess_pu.sum() * DT_H),
                loss_kwh=float(r['source'].loss_kw.sum() * DT_H))


def compare_to_table(label: str, got: dict, table: pd.DataFrame, case: str, columns: list[str]) -> None:
    row = table[table.case == case]
    require(len(row) == 1, f'{label}: case {case!r} not found once in table')
    row = row.iloc[0]
    for col in columns:
        require(close(got[col], row[col]), f'{label}: {col} differs from table', case=case,
                recomputed=got[col], table=float(row[col]))


# ---------------------------------------------------------------------------------------------- Fig. 9
def figure_9(style, produced: list[Path], report: dict) -> list[str]:
    paired = A / 'results' / 'validacao_fases_s0_ae'
    organised = read_csv('fig9_voltage_pairs', PKG / 'figure_data' / 'fig9_voltage_pairs.csv')
    table_s1 = read_csv('table_S1_paired_voltage_error', PKG / 'tables' / 'table_S1_paired_voltage_error.csv')
    frames, stats = {}, []
    for hour in (3, 18):
        raw = read_csv(f'paired_h{hour:02d}', paired / f's0_h{hour:02d}_mt_equivalente_comparacao_tensoes.csv')
        require(len(raw) == CHECK['pairs'], f'{hour:02d}h: pair count', pairs=len(raw))
        require(int(raw.duplicated(['bus', 'phase']).sum()) == 0, f'{hour:02d}h: duplicated bus-phase pairs')
        require(set(raw.phase) == {'A', 'B', 'C'}, f'{hour:02d}h: phases other than A/B/C', phases=set(raw.phase))
        require(raw[['pandapower_pu', 'opendss_pu', 'delta_pu', 'abs_delta_pu']].notna().all().all(),
                f'{hour:02d}h: missing values')
        require(np.allclose(raw.delta_pu, raw.opendss_pu - raw.pandapower_pu, rtol=0, atol=1e-12),
                f'{hour:02d}h: delta_pu is not opendss_pu - pandapower_pu')
        require(np.allclose(raw.abs_delta_pu, raw.delta_pu.abs(), rtol=0, atol=1e-12),
                f'{hour:02d}h: abs_delta_pu is not |delta_pu|')
        org = organised[organised.hour == hour].merge(raw, on=['bus', 'phase'], suffixes=('', '_raw'),
                                                      validate='one_to_one')
        require(len(org) == len(raw) and np.allclose(org.delta_pu, org.delta_pu_raw, rtol=0, atol=1e-9),
                f'{hour:02d}h: figure_data/fig9_voltage_pairs.csv differs from the raw pairs')
        frames[hour] = raw
        for phase, block in [*raw.groupby('phase'), ('ALL', raw)]:
            e = block.delta_pu.to_numpy()
            q1, q3 = np.percentile(abs(e), [25, 75])
            whisker = 1.5 * (q3 - q1)
            stats.append(dict(hour=hour, phase=phase, pairs=len(e), mae_pu=float(np.mean(abs(e))),
                              rmse_pu=float(np.sqrt(np.mean(e**2))), max_abs_error_pu=float(np.max(abs(e))),
                              median_abs_pu=float(np.median(abs(e))), q1_abs_pu=float(q1), q3_abs_pu=float(q3),
                              beyond_whiskers=int(((abs(e) < q1 - whisker) | (abs(e) > q3 + whisker)).sum()),
                              opendss_lower=int((e < 0).sum()), opendss_higher=int((e > 0).sum())))
    stats = pd.DataFrame(stats)
    for r in stats.itertuples():
        t = table_s1[(table_s1.hour == r.hour) & (table_s1.phase == r.phase)]
        require(len(t) == 1 and int(t.pairs.iloc[0]) == r.pairs, 'Table S1 pair count', hour=r.hour, phase=r.phase)
        for col in ('mae_pu', 'rmse_pu', 'max_abs_error_pu'):
            require(close(getattr(r, col), t[col].iloc[0]), f'Table S1 {col}', hour=r.hour, phase=r.phase,
                    recomputed=getattr(r, col), table=float(t[col].iloc[0]))
    rmse = stats[stats.phase == 'ALL'].set_index('hour')
    for hour, key in ((3, 'rmse_h03'), (18, 'rmse_h18')):
        require(abs(rmse.loc[hour, 'rmse_pu'] - CHECK[key]) <= CHECK_TOL, f'RMSE {hour:02d}h vs check value',
                recomputed=rmse.loc[hour, 'rmse_pu'], check=CHECK[key],
                calculation='sqrt(mean((opendss_pu - pandapower_pu)^2)) over all 3,820 bus-phase pairs')
    stats.to_csv(DATA / 'fig9_summary_by_phase.csv', index=False, float_format='%.10g')
    report['fig9'] = stats.to_dict('records')

    # Two panels, (a) 03:00 and (b) 18:00, common y axis. Whiskers span minimum to maximum (whis=(0, 100)), so
    # every pair lies inside the drawn range and no outlier is hidden.
    n = {p: int((frames[3].phase == p).sum()) for p in 'ABC'}
    fig, axes = plt.subplots(1, 2, figsize=mm(style.DOUBLE_MM, 70), sharey=True, layout='constrained')
    for ax, hour, tag in zip(axes, (3, 18), 'ab'):
        raw, fill = frames[hour], FIG9_HOUR_COLOURS[hour]
        ax.boxplot([raw.loc[raw.phase == p, 'abs_delta_pu'].to_numpy() for p in 'ABC'], positions=range(3),
                   widths=.5, whis=(0, 100), showfliers=False, patch_artist=True,
                   boxprops=dict(facecolor=fill, edgecolor=style.INK, linewidth=.6),
                   whiskerprops=dict(color=style.INK, linewidth=.6), capprops=dict(color=style.INK, linewidth=.6),
                   medianprops=dict(color=FIG9_MEDIAN_COLOURS[hour], linewidth=1.2))
        ax.set_xticks(range(3), [f'Phase {p}' for p in 'ABC'])
        ax.tick_params(axis='x', length=0)
        ax.grid(axis='y', color=style.GRID, linewidth=.5)
        ax.set_axisbelow(True)
        ax.text(.98, .97, f'RMSE {rmse.loc[hour, "rmse_pu"]:.5f} pu', transform=ax.transAxes, ha='right',
                va='top', fontsize=style.BODY_PT)
        style.panel(ax, f'({tag})  S0, {hour:02d}:00')
    axes[0].set_ylim(0, .15)
    axes[0].yaxis.set_major_locator(MultipleLocator(.025))
    axes[0].yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f'{v:.3f}'))
    axes[0].set_ylabel('|V$_{\\mathrm{OpenDSS}}$ − V$_{\\mathrm{pandapower}}$| (pu)')
    save_figure(fig, 'fig9_model_voltage_discrepancy_claude', produced)

    s = stats.set_index(['hour', 'phase'])
    a_lower = min(s.loc[(h, 'A'), 'opendss_lower'] / s.loc[(h, 'A'), 'pairs'] for h in (3, 18))
    c_higher = min(s.loc[(h, 'C'), 'opendss_higher'] / s.loc[(h, 'C'), 'pairs'] for h in (3, 18))
    return [
        '**Fig. 9.** Paired physical-phase voltage differences |V_OpenDSS − V_pandapower| between the '
        'historical pandapower medium-voltage reduction and a matched OpenDSS medium-voltage equivalent in S0 at '
        '(a) 03:00 and (b) 18:00. Both models use fixed regulator taps, connected capacitors, the adjusted study '
        'source and primary-equivalent loads. Each hour has '
        f'{int(rmse.loc[3, "pairs"]):,} matched bus-phase pairs (phase A {n["A"]:,}, B {n["B"]:,}, C {n["C"]:,}). '
        'Boxes span the interquartile range, the horizontal line marks the median and whiskers extend to the '
        'minimum and maximum, so every pair lies within the drawn range and none is excluded from the figure or '
        'from the metrics. '
        f'Global RMSE: {rmse.loc[3, "rmse_pu"]:.5f} pu at 03:00 and {rmse.loc[18, "rmse_pu"]:.5f} pu at 18:00 '
        f'(maximum absolute difference {rmse.loc[3, "max_abs_error_pu"]:.5f} and {rmse.loc[18, "max_abs_error_pu"]:.5f} pu); '
        'per-phase statistics are in Table S1. The difference is systematic in sign: OpenDSS is lower on phase A '
        f'in at least {100 * a_lower:.1f} % of pairs and higher on phase C in at least {100 * c_higher:.1f} % of '
        'pairs at both hours. This test diagnoses model discrepancy under matched S0 conditions; it does not '
        'establish equivalence to the complete IEEE 8500-node feeder.'
    ]


# --------------------------------------------------------------------------------------------- Fig. 10
CASES_10 = [  # key, label, folder, colour, dashes, line width
    ('S0', 'S0', PILOT / 'S0_auto', '#7a7a7a', '-', .9),
    ('S1', 'S1', PILOT / 'S1_auto', '#d94f1f', '-', 1.1),
    ('S4_single', 'S4, historical single BESS', PILOT / 'S4_auto', '#2a78d6', (0, (4, 1.2, 1, 1.2)), 1.1),
    ('S4_dist_native', 'S4, distributed, native controls', CONTROLS / 'distributed_native', '#7b3fa8', '-', 1.1),
    ('S4_dist_policy', 'S4, distributed, saved regulator policy', CONTROLS / 'distributed_saved_policy', '#1e9b6a',
     (0, (2.2, 1.3)), 1.1),
]
TABLE13_CASE = {'S0': 'S0', 'S1': 'S1', 'S4_single': 'S4 single BESS', 'S4_dist_native': 'S4 distributed',
                'S4_dist_policy': 'S4 distributed + control'}
SUMMARY_COLUMNS = ['peak_kw', 'min_voltage_pu', 'max_voltage_pu', 'max_loading_pct', 'new_overloaded_lines_vs_s0',
                   'thermal_excess_pph', 'undervoltage_deficit_puh', 'overvoltage_excess_puh', 'loss_kwh']


def base_overloaded_lines() -> set[str]:
    b = read_csv('S0:branches', PILOT / 'S0_auto' / 'branches.csv')
    return set(b.loc[b.loading_pct > LOAD_LIMIT, 'line'])


def resource_totals() -> dict:
    """Ratings quoted in the captions: historical single BESS (project A config) and the later distributed
    BESS sites and V2G fleet (project B saved solution). Equal totals are required, not assumed."""
    import yaml
    battery = yaml.safe_load(register('historical_bess_config', A / 'configs' / 'bess.yaml')
                             .read_text(encoding='utf-8'))['battery']
    best = B / 'results' / 'v301_continuacao_ac_segura' / 'best'
    sites = read_csv('distributed_bess_sizing', best / 'bess_siting_sizing.csv')
    fleet = read_csv('distributed_vehicle_dispatch', best / 'vehicle_dispatch.csv')
    per_vehicle = fleet.groupby('vehicle_id').site.nunique()
    totals = dict(single_power_kw=float(battery['power_kw']), single_energy_kwh=float(battery['capacity_kwh']),
                  dist_sites=len(sites), dist_power_kw=float(sites.power_kw.sum()),
                  dist_energy_kwh=float(sites.energy_kwh.sum()), v2g_vehicles=int(len(per_vehicle)),
                  v2g_sites=int(fleet.site.nunique()))
    require(math.isclose(totals['dist_power_kw'], totals['single_power_kw'], abs_tol=1e-3)
            and math.isclose(totals['dist_energy_kwh'], totals['single_energy_kwh'], abs_tol=1e-3),
            'distributed BESS totals differ from the historical single BESS', **totals)
    require(int(per_vehicle.max()) == 1, 'a V2G vehicle is assigned to more than one site')
    return totals


def step(ax, y, **kw):
    """Hourly value held over [t, t+1): the instant t is the start of the interval."""
    y = np.asarray(y, float)
    return ax.step(np.arange(25), np.append(y, y[-1]), where='post', **kw)


def figure_10(style, produced: list[Path], report: dict) -> list[str]:
    base = base_overloaded_lines()
    table13 = read_csv('table_13', PKG / 'tables' / 'table_13_opendss_operational_comparison.csv')
    organised = read_csv('fig10_hourly_profiles', PKG / 'figure_data' / 'fig10_hourly_profiles.csv')
    hours, summary, nodes = {}, {}, None
    for key, _label, folder, *_ in CASES_10:
        r = read_replay(key, folder)
        node_set = set(map(tuple, r['voltages'][['bus', 'phase']].drop_duplicates().to_numpy()))
        nodes = node_set if nodes is None else nodes
        require(node_set == nodes, f'{key}: physical bus-phase set differs from S0')
        hours[key] = hourly_indicators(r, base)
        summary[key] = daily_summary(r, hours[key], base)
        compare_to_table('Table 13', summary[key], table13, TABLE13_CASE[key], SUMMARY_COLUMNS)
        org = organised[organised.case == TABLE13_CASE[key]].sort_values('hour')
        for col in ('source_kw', 'min_voltage_pu', 'new_overloaded_lines'):
            require(len(org) == 24 and np.allclose(org[col].to_numpy(), hours[key][col].to_numpy(), rtol=1e-9),
                    f'{key}: {col} differs from figure_data/fig10_hourly_profiles.csv')
    for key, check in (('S1', 'new_s1'), ('S4_single', 'new_s4_single'), ('S4_dist_native', 'new_dist'),
                       ('S4_dist_policy', 'new_dist')):
        require(summary[key]['new_overloaded_lines_vs_s0'] == CHECK[check], f'{key}: new lines vs check value',
                recomputed=summary[key]['new_overloaded_lines_vs_s0'], check=CHECK[check],
                calculation='len({line: loading_pct > 100 in any hour} - {line: loading_pct > 100 in any hour of S0_auto})')
    for key, check in (('S4_single', 'vmin_s4_single'), ('S4_dist_native', 'vmin_dist_native'),
                       ('S4_dist_policy', 'vmin_dist_policy')):
        require(abs(summary[key]['min_voltage_pu'] - CHECK[check]) <= CHECK_TOL, f'{key}: Vmin vs check value',
                recomputed=summary[key]['min_voltage_pu'], check=CHECK[check], calculation='min(v_pu) over 24 h')
    pd.concat([h.assign(case=k) for k, h in hours.items()]).to_csv(DATA / 'fig10_plotted_series.csv', index=False,
                                                                     float_format='%.10g')
    report['fig10'] = {k: summary[k] for k in summary}
    report['fig10_s0_overloaded_line_identities'] = sorted(base)

    fig, axs = plt.subplots(3, 1, figsize=mm(style.DOUBLE_MM, 150), sharex=True, layout='constrained')
    for key, label, _f, color, dashes, lw in CASES_10:
        h = hours[key]
        step(axs[0], h.source_kw, color=color, ls=dashes, lw=lw, label=label)
        if key != 'S0':  # zero by construction in S0
            step(axs[1], h.new_overloaded_lines, color=color, ls=dashes, lw=lw)
        step(axs[2], h.min_voltage_pu, color=color, ls=dashes, lw=lw)
    style.panel(axs[0], '(a)  Substation import')
    style.panel(axs[1], '(b)  Newly overloaded lines')
    style.panel(axs[2], '(c)  Minimum MV voltage')
    axs[0].set_ylabel('Import (kW)')
    axs[0].yaxis.set_major_formatter(FuncFormatter(thousands))
    axs[1].set_ylabel('Lines above 100 % (count)')
    axs[1].yaxis.set_major_locator(MaxNLocator(integer=True))
    axs[1].set_ylim(-1.5, None)
    axs[1].annotate('Distributed S4, both regulator policies: 0 in every hour', (.5, 0), xytext=(0, 4),
                    textcoords='offset points', ha='left', va='bottom', fontsize=style.BODY_PT, color=style.MUTED)
    axs[2].set_ylabel('Voltage (pu)')
    axs[2].axhline(V_LOW, color=style.INK, lw=.7, ls=(0, (1, 1.5)))
    axs[2].annotate('0.95 pu study screen', (.5, V_LOW), xytext=(0, -3), textcoords='offset points', ha='left',
                    va='top', fontsize=style.BODY_PT)
    axs[2].yaxis.set_major_formatter(FuncFormatter(lambda x, _p: f'{x:.2f}'))
    axs[2].set_xlim(0, 24)
    axs[2].set_xticks(range(0, 25, 3))
    axs[2].set_xlabel('Hour of day (interval start, 1-h step)')
    for ax in axs:
        ax.grid(color=style.GRID, linewidth=.5)
        ax.set_axisbelow(True)
    fig.legend(*axs[0].get_legend_handles_labels(), loc='outside upper center', ncol=3, frameon=False,
               fontsize=style.BODY_PT, handlelength=3.2, columnspacing=1.6)
    save_figure(fig, 'fig10_native_operational_replay_claude', produced)

    def peak_text(key):
        h = hours[key]
        top = int(h.new_overloaded_lines.max())
        at = [f'{int(x):02d}:00' for x in h.hour[h.new_overloaded_lines == top]]
        return f'{top} in the interval{"s" if len(at) > 1 else ""} starting {", ".join(at)}'
    gap = float((hours['S4_dist_native'].source_kw - hours['S4_dist_policy'].source_kw).abs().max())
    res = resource_totals()
    report['resources'] = res
    s = summary
    return [
        '**Fig. 10.** Hourly operation of the complete IEEE 8500-node feeder in OpenDSS (phase-explicit, 1-h '
        'step, native automatic regulator and capacitor controls unless stated): (a) substation active-power '
        'import, (b) number of newly overloaded line identities and (c) minimum medium-voltage bus-phase voltage. '
        'S0, S1 and the historical single-BESS S4 replay the saved hourly injections of the pandapower campaign; '
        f'the distributed S4 is a later, separate configuration with {words(res["dist_sites"])} BESS units of the same '
        f'total {res["dist_power_kw"] / 1000:.1f} MW/{res["dist_energy_kwh"] / 1000:.1f} MWh as the single BESS and '
        f'{res["v2g_vehicles"]} V2G vehicles at {words(res["v2g_sites"])} sites, and is not one of the 63 historical '
        'configurations. The '
        'saved-regulator-policy trace lowers the set point of the four phase-C regulators by 0.5 V on the '
        'potential-transformer secondary base. A newly overloaded line exceeds 100 % of its study ampacity in the '
        'plotted hour and never exceeded it in any hour of native-control S0; S0 is therefore zero by construction '
        'and is omitted from (b). Over the day, '
        f'{s["S1"]["new_overloaded_lines_vs_s0"]} distinct lines are newly overloaded in S1 and '
        f'{s["S4_single"]["new_overloaded_lines_vs_s0"]} in the historical S4 (hourly maximum: S1, '
        f'{peak_text("S1")}; historical S4, {peak_text("S4_single")}); the distributed S4 has none under either '
        'policy. Daily minimum voltages are '
        f'{s["S4_single"]["min_voltage_pu"]:.5f}, {s["S4_dist_native"]["min_voltage_pu"]:.5f} and '
        f'{s["S4_dist_policy"]["min_voltage_pu"]:.5f} pu for the historical, distributed-native and '
        f'distributed-policy S4 (S0: {s["S0"]["min_voltage_pu"]:.5f} pu; S1: {s["S1"]["min_voltage_pu"]:.5f} pu). '
        f'The two distributed import traces differ by at most {gap:.1f} kW and overlap in (a). The dotted line at '
        '0.95 pu is the engineering screen of the study (ANSI C84.1 range A), not a Honduran regulatory limit.'
    ]


# --------------------------------------------------------------------------------------------- Fig. 11
CASES_11 = [  # key, axis label, folder, table-14 case, colour, hatched (taps frozen)
    ('single', 'Historical S4,\nsingle BESS', PILOT / 'S4_auto', 'Historical single BESS', '#2a78d6', False),
    ('native', 'Distributed,\nnative controls', CONTROLS / 'distributed_native', 'Distributed, native controls',
     '#7b3fa8', False),
    ('policy', 'Distributed,\nsaved regulator policy', CONTROLS / 'distributed_saved_policy',
     'Distributed, changed controls', '#1e9b6a', False),
    ('native_frozen', 'Distributed, taps frozen\nat native S0', CONTROLS / 'distributed_native_frozen',
     'Distributed, native S0 taps', '#7b3fa8', True),
    ('policy_frozen', 'Distributed, taps frozen\nat saved-policy S0', CONTROLS / 'distributed_policy_frozen',
     'Distributed, changed S0 taps', '#1e9b6a', True),
]
COMPARISON_CASE = {'native': 'distributed_native', 'policy': 'distributed_saved_policy',
                   'native_frozen': 'distributed_native_frozen', 'policy_frozen': 'distributed_policy_frozen'}


def check_control_provenance() -> dict:
    """Policy = -0.5 V on four phase-C regulators; frozen taps equal the hourly taps of the matching S0;
    capacitors still switch in the frozen views."""
    def taps(key, folder):
        return read_csv(f'{key}:regulators', folder / 'regulators.csv').sort_values(['hour', 'regulator']) \
            .reset_index(drop=True)
    policies = {}
    for key, folder in (('policy', CONTROLS / 'distributed_saved_policy'), ('policy_frozen', CONTROLS /
                        'distributed_policy_frozen'), ('S0_policy', CONTROLS / 'S0_saved_policy')):
        policies[key] = json.loads(register(f'{key}:applied_policy', folder / 'applied_policy.json')
                                   .read_text(encoding='utf-8'))
    require(all(p == policies['policy'] for p in policies.values()), 'applied_policy.json differs between cases')
    regs = policies['policy']['regulators']
    require(len(regs) == 4 and all(name.lower().endswith('c') for name in regs)
            and all(math.isclose(r['delta_v'], -.5) and math.isclose(r['new_v'] - r['original_v'], -.5)
                    for r in regs.values()) and not policies['policy']['capcontrols'],
            'saved policy is not -0.5 V on four phase-C regulators with unchanged capacitor controls', policy=regs)
    require(taps('native_frozen', CONTROLS / 'distributed_native_frozen').equals(taps('S0', PILOT / 'S0_auto')),
            'native frozen taps differ from native S0 hourly taps')
    require(taps('policy_frozen', CONTROLS / 'distributed_policy_frozen')
            .equals(taps('S0_policy', CONTROLS / 'S0_saved_policy')),
            'policy frozen taps differ from saved-policy S0 hourly taps')
    switching = {}
    for key, folder in (('native_frozen', CONTROLS / 'distributed_native_frozen'),
                        ('policy_frozen', CONTROLS / 'distributed_policy_frozen')):
        caps = read_csv(f'{key}:capacitors', folder / 'capacitors.csv')
        switching[key] = int((caps.groupby('capacitor').states.nunique() > 1).sum())
        require(switching[key] > 0, f'{key}: no capacitor changes state, automatic control not evident')
    return dict(policy=regs, capacitors_switching_in_frozen_views=switching)


def figure_11(style, produced: list[Path], report: dict) -> list[str]:
    base = base_overloaded_lines()
    table14 = read_csv('table_14', PKG / 'tables' / 'table_14_controls_and_frozen_taps.csv')
    comparison = read_csv('alternative_controls_comparison', CONTROLS / 'comparison.csv').set_index('case')
    provenance = check_control_provenance()
    res = resource_totals()
    rows = []
    for key, label, folder, case, colour, hatched in CASES_11:
        r = read_replay(key, folder)
        h = hourly_indicators(r, base)
        s = daily_summary(r, h, base)
        compare_to_table('Table 14', s, table14, case, SUMMARY_COLUMNS)
        if key in COMPARISON_CASE:
            c = comparison.loc[COMPARISON_CASE[key]]
            for mine, theirs in (('thermal_excess_pph', 'thermal_pph'), ('undervoltage_deficit_puh', 'under_puh'),
                                 ('overvoltage_excess_puh', 'over_puh')):
                require(close(s[mine], c[theirs]), f'{key}: {mine} differs from alternative_controls/comparison.csv',
                        recomputed=s[mine], saved=float(c[theirs]))
        rows.append(dict(key=key, label=label.replace('\n', ' '), case_table14=case, **s))
    rows = pd.DataFrame(rows)
    rows.to_csv(DATA / 'fig11_plotted_severity.csv', index=False, float_format='%.10g')
    report['fig11'] = rows.to_dict('records')
    report['fig11_provenance'] = provenance
    s0_replay = read_replay('S0', PILOT / 'S0_auto')
    s0 = daily_summary(s0_replay, hourly_indicators(s0_replay, base), base)
    compare_to_table('Table 13', s0, table13 := read_csv('table_13', PKG / 'tables' /
                     'table_13_opendss_operational_comparison.csv'), 'S0', SUMMARY_COLUMNS)

    fig, axs = plt.subplots(1, 3, figsize=mm(style.DOUBLE_MM, 70), sharey=True, layout='constrained')
    y = np.arange(len(CASES_11))[::-1].astype(float)
    y[1:] -= .35                                          # small gap between historical and distributed groups
    panels = (('thermal_excess_pph', '(a)  Thermal overload', 'Excess above 100 % loading (pp·h)', '{:.1f}'),
              ('undervoltage_deficit_puh', '(b)  Undervoltage', 'Deficit below 0.95 pu (pu·h)', '{:.2f}'),
              ('overvoltage_excess_puh', '(c)  Overvoltage', 'Excess above 1.05 pu (pu·h)', '{:.2f}'))
    with plt.rc_context({'hatch.linewidth': .6}):
        for ax, (col, title, xlabel, fmt) in zip(axs, panels):
            for yi, (key, _l, _f, _c, colour, hatched) in zip(y, CASES_11):
                value = float(rows.loc[rows.key == key, col].iloc[0])
                ax.barh(yi, value, height=.66, color='white' if hatched else colour, edgecolor=colour,
                        hatch='/////' if hatched else None, linewidth=.8)
                ax.annotate(fmt.format(value), (value, yi), xytext=(2, 0), textcoords='offset points', ha='left',
                            va='center', fontsize=style.BODY_PT)
            ax.set_xlim(0, rows[col].max() * 1.22)
            ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
            ax.ticklabel_format(axis='x', style='plain', useOffset=False)
            ax.set_xlabel(xlabel)
            ax.grid(axis='x', color=style.GRID, linewidth=.5)
            ax.set_axisbelow(True)
            ax.tick_params(axis='y', length=0)
            style.panel(ax, title)
    axs[0].set_yticks(y, [c[1] for c in CASES_11])
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor='#9a9a9a', edgecolor='#9a9a9a', label='Regulators acting'),
               plt.Rectangle((0, 0), 1, 1, facecolor='white', edgecolor='#555555', hatch='/////',
                             label='Taps frozen at the hourly S0 positions (capacitors automatic)')]
    with plt.rc_context({'hatch.linewidth': .6}):
        fig.legend(handles=handles, loc='outside lower center', ncol=2, frameon=False, fontsize=style.BODY_PT)
        save_figure(fig, 'fig11_thermal_voltage_control_tradeoff_claude', produced)

    return [
        '**Fig. 11.** Absolute daily violation severity in the complete IEEE 8500-node feeder in OpenDSS for the '
        f'historical single-BESS S4 and four operating views of the later distributed S4 ({words(res["dist_sites"])} BESS, '
        f'{res["v2g_vehicles"]} V2G vehicles): (a) thermal excess above 100 % of the study ampacity, (b) voltage deficit below 0.95 pu and '
        '(c) voltage excess above 1.05 pu. Each severity sums the positive excess of every physical line-phase '
        '(a) or bus-phase (b, c) over the 24 one-hour intervals, multiplied by dt_h = 1 h; the units are '
        'percentage-point-hours and per-unit-hours summed over phases and locations, so they measure aggregate '
        'exposure, not the depth at a single point. The saved regulator policy lowers the set point of four '
        'phase-C regulators by 0.5 V on the potential-transformer secondary base (not 0.5 pu). Hatched bars hold '
        'the regulator taps at the hourly positions of the matching S0 (native-control S0 or saved-policy S0); '
        'capacitor controls remain automatic in those views. All quantities are absolute, not differences from S0 '
        'and not counts of newly overloaded lines. For reference, native-control S0 alone has '
        f'{s0["thermal_excess_pph"]:.1f} pp·h, {s0["undervoltage_deficit_puh"]:.2f} pu·h and '
        f'{s0["overvoltage_excess_puh"]:.2f} pu·h. The distributed configuration is not one of the 63 historical '
        'pandapower configurations.'
    ]


# -------------------------------------------------------------------------------------------------- main
OUTPUT_NAMES = [f'{stem}.{ext}' for stem in ('fig9_model_voltage_discrepancy_claude',
                                             'fig10_native_operational_replay_claude',
                                             'fig11_thermal_voltage_control_tradeoff_claude')
                for ext in ('pdf', 'png', 'tif')] + ['CAPTIONS_en.md', 'manifest_sha256.json',
                                                      'verification.json']


def main(replace: bool) -> int:
    existing = [n for n in OUTPUT_NAMES if (OUT / n).exists()]
    if existing and not replace:
        raise FileExistsError(f'Outputs already exist in {OUT}; use --replace to redraw them: {existing}')
    if OUT == PKG / 'figures':
        raise RuntimeError('Refusing to write into the current figures folder')
    DATA.mkdir(exist_ok=True)
    style = load_style()
    produced: list[Path] = []
    report: dict = {'check_values': CHECK, 'check_tolerance': CHECK_TOL, 'dt_h': DT_H}
    captions, failures = [], {}
    mathtext_arial = {'mathtext.fontset': 'custom', 'mathtext.rm': style.FONT, 'mathtext.it': f'{style.FONT}:italic',
                      'mathtext.bf': f'{style.FONT}:bold'}  # subscripts in Arial, no second embedded font
    with plt.rc_context({**style.rc(), **mathtext_arial}):
        for name, builder in (('fig9', figure_9), ('fig10', figure_10), ('fig11', figure_11)):
            try:
                captions += builder(style, produced, report)
                report[f'{name}_status'] = 'PASS'
            except CheckFailed as error:
                failures[name] = json.loads(str(error))
                report[f'{name}_status'] = 'NOT DRAWN: ' + failures[name]['check']
                plt.close('all')
    report['failures'] = failures
    (OUT / 'verification.json').write_text(json.dumps(_plain(report), ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'CAPTIONS_en.md').write_text(
        '# Candidate captions for Figs. 9-11 (English)\n\nGenerated by `build_figures_claude.py`; every number is '
        'computed from the source files listed in `manifest_sha256.json`.\n\n' + '\n\n'.join(captions) + '\n',
        encoding='utf-8')
    produced += [OUT / 'verification.json', OUT / 'CAPTIONS_en.md', *sorted(DATA.glob('*.csv'))]
    script = Path(__file__).resolve()
    manifest = dict(
        status='DERIVED_FROM_SAVED_RESULTS_NO_SIMULATION',
        inputs=INPUTS,
        script={'path': str(script), 'sha256': sha256(script)},
        outputs={str(p.relative_to(OUT)).replace('\\', '/'): sha256(p) for p in produced},
        environment=dict(python=platform.python_version(), matplotlib=matplotlib.__version__,
                         pandas=pd.__version__, numpy=np.__version__, pillow=Image.__version__,
                         platform=platform.platform()),
        rendering=dict(png_dpi=PNG_DPI, tiff_dpi=TIFF_DPI, tiff_compression='LZW', colour='RGB',
                       pdf_fonttype=42, fig9_whiskers='minimum-maximum'))
    (OUT / 'manifest_sha256.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, failure in failures.items():
        print(f'{name}: NOT DRAWN\n{json.dumps(failure, ensure_ascii=False, indent=2)}', file=sys.stderr)
    print(f'{len(produced)} files written, {len(INPUTS)} inputs fingerprinted, failures: {sorted(failures) or "none"}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(replace='--replace' in sys.argv[1:]))
