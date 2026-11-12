"""Build traceable, ready-to-review Applied Energy figures and source tables."""
from __future__ import annotations

from pathlib import Path
import csv
import hashlib
import json
import math
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
B = ROOT.parent
A = B.parent / 'EVCS-BESS-V2G'
OUT = ROOT / 'paper_insertions'
FIG = OUT / 'figures'
TAB = OUT / 'tables'
DATA = OUT / 'figure_data'

SOURCE: dict[str, dict[str, str]] = {}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_csv(key: str, path: Path) -> pd.DataFrame:
    path = path.resolve(strict=True)
    SOURCE[key] = {'path': str(path), 'sha256': sha256(path)}
    return pd.read_csv(path)


def write_csv(df: pd.DataFrame, name: str) -> None:
    if df.empty:
        raise ValueError(f'Empty output table: {name}')
    df.to_csv(TAB / name, index=False, float_format='%.10g')


def save_figure(fig: plt.Figure, stem: str) -> None:
    for suffix, kwargs in (
        ('pdf', {}), ('png', {'dpi': 400}), ('tif', {'dpi': 400, 'pil_kwargs': {'compression': 'tiff_lzw'}}),
    ):
        fig.savefig(FIG / f'{stem}.{suffix}', bbox_inches='tight', facecolor='white', **kwargs)
    plt.close(fig)


def assert_times(df: pd.DataFrame, label: str) -> pd.DataFrame:
    if set(df.hour) != set(range(24)) or df.hour.duplicated().any():
        raise ValueError(f'{label}: expected one row for every hourly interval')
    return df.sort_values('hour').reset_index(drop=True)


def hourly_case(case: str, folder: Path, base_lines: set[str]) -> tuple[pd.DataFrame, dict]:
    s = assert_times(read_csv(f'{case}:source', folder / 'source.csv'), case + ' source')
    v = read_csv(f'{case}:voltages', folder / 'voltages.csv')
    b = read_csv(f'{case}:branches', folder / 'branches.csv')
    if v.duplicated(['hour', 'bus', 'phase']).any() or b.duplicated(['hour', 'line', 'phase']).any():
        raise ValueError(f'{case}: duplicated phase-hour observation')
    if set(v.hour) != set(range(24)) or set(b.hour) != set(range(24)):
        raise ValueError(f'{case}: missing electrical hour')
    vmin = v.groupby('hour').v_pu.min().reindex(range(24))
    vmax = v.groupby('hour').v_pu.max().reindex(range(24))
    thermal = b.assign(excess_pp=(b.loading_pct - 100).clip(lower=0)).groupby('hour').excess_pp.sum().reindex(range(24))
    under = v.assign(deficit_pu=(.95 - v.v_pu).clip(lower=0)).groupby('hour').deficit_pu.sum().reindex(range(24))
    over = v.assign(excess_pu=(v.v_pu - 1.05).clip(lower=0)).groupby('hour').excess_pu.sum().reindex(range(24))
    new = b[(b.loading_pct > 100) & ~b.line.isin(base_lines)].groupby('hour').line.nunique().reindex(range(24), fill_value=0)
    hours = pd.DataFrame(dict(hour=range(24), source_kw=s.p_kw.to_numpy(), min_voltage_pu=vmin.to_numpy(),
                              max_voltage_pu=vmax.to_numpy(), new_overloaded_lines=new.to_numpy(),
                              thermal_excess_pp=thermal.to_numpy(), undervoltage_deficit_pu=under.to_numpy(),
                              overvoltage_excess_pu=over.to_numpy()))
    summary = dict(case=case, peak_kw=float(s.p_kw.max()), min_voltage_pu=float(v.v_pu.min()),
                   max_voltage_pu=float(v.v_pu.max()), max_loading_pct=float(b.loading_pct.max()),
                   new_overloaded_lines_vs_s0=int(len(set(b.loc[b.loading_pct > 100, 'line']) - base_lines)),
                   thermal_excess_pph=float(thermal.sum()), undervoltage_deficit_puh=float(under.sum()),
                   overvoltage_excess_puh=float(over.sum()), loss_kwh=float(s.loss_kw.sum()),
                   hours=24, dt_h=1.0)
    return hours, summary


def build(refresh: bool = False) -> None:
    if OUT.exists():
        manifest_path = OUT / 'manifest.json'
        if not refresh or not manifest_path.is_file():
            raise FileExistsError(f'Preserve the prior package and use a fresh destination: {OUT}')
        old = json.loads(manifest_path.read_text(encoding='utf-8'))
        for kind, path in [('tables', TAB), ('figures', FIG)]:
            if {p.name: sha256(p) for p in path.iterdir()} != old[kind]:
                raise ValueError(f'Existing {kind} differ from the recorded package; refusing refresh')
    for folder in (FIG, TAB, DATA):
        folder.mkdir(parents=True, exist_ok=refresh)
    manuscript = (B.parent / 'EVCS_BESS_V2G_Applied_Energy_Elsevier_Template_v6.docx').resolve(strict=True)
    SOURCE['manuscript_v6'] = {'path': str(manuscript), 'sha256': sha256(manuscript)}
    with ZipFile(manuscript) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
    word_ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    paragraphs = [''.join(t.text or '' for t in node.findall('.//w:t',word_ns))
                  for node in document.findall('.//w:p',word_ns)]
    if not any(text.startswith('Fig. 8.') for text in paragraphs) or not any(text.startswith('Table 12.') for text in paragraphs):
        raise ValueError('Manuscript figure/table numbering must be reviewed before creating Fig. 9 and Table 13')
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.spines.top': False,
                         'axes.spines.right': False, 'pdf.fonttype': 42, 'ps.fonttype': 42,
                         'axes.labelcolor': '#23344b', 'text.color': '#23344b'})
    blue, orange, green, purple = '#2766a6', '#d17832', '#238276', '#79599c'

    # Historical pair: fixed taps, project MV reduction vs native-phase DSS model.
    paired = A / 'results' / 'validacao_fases_s0_ae'
    rows = []
    for hour in (3, 18):
        tag = f's0_h{hour:02d}_mt_equivalente'
        df = read_csv(f'paired_h{hour:02d}', paired / f'{tag}_comparacao_tensoes.csv')
        if len(df) != 3820 or set(df.phase) != {'A', 'B', 'C'}:
            raise ValueError('Historical physical-phase pairing differs from audit')
        df['hour'] = hour
        rows.append(df[['hour', 'bus', 'phase', 'pandapower_pu', 'opendss_pu', 'delta_pu', 'abs_delta_pu']])
    voltage = pd.concat(rows, ignore_index=True)
    voltage.to_csv(DATA / 'fig9_voltage_pairs.csv', index=False, float_format='%.10g')
    vstats = []
    for (hour, phase), block in voltage.groupby(['hour', 'phase']):
        error = block.delta_pu.to_numpy()
        vstats.append(dict(hour=hour, phase=phase, pairs=len(block), mae_pu=float(np.mean(abs(error))),
                           rmse_pu=float(np.sqrt(np.mean(error**2))), max_abs_error_pu=float(np.max(abs(error)))))
    for hour, block in voltage.groupby('hour'):
        error = block.delta_pu.to_numpy()
        vstats.append(dict(hour=hour, phase='ALL', pairs=len(block), mae_pu=float(np.mean(abs(error))),
                           rmse_pu=float(np.sqrt(np.mean(error**2))), max_abs_error_pu=float(np.max(abs(error)))))
    vstats = pd.DataFrame(vstats).sort_values(['hour','phase'])
    write_csv(vstats, 'table_S1_paired_voltage_error.csv')
    historic = read_csv('historical_pair_metrics', ROOT / 'metricas_pareadas_complementares.csv')
    validation = vstats[vstats.phase == 'ALL'].merge(
        historic[historic['mode'] == 'mt_equivalente'][['hour','pp_p_subestacao_kw','dss_p_subestacao_kw',
            'pp_q_subestacao_kvar','dss_q_subestacao_kvar','pp_perdas_kw','dss_perdas_kw']], on='hour', validate='one_to_one')
    write_csv(validation, 'table_S2_paired_source_losses.csv')
    raw_audit = read_csv('original_audit_voltage_stats', ROOT / '04_validacao_s0.csv')
    original = raw_audit[(raw_audit['mode'] == 'mt_equivalente') & (raw_audit.phase == 'ALL')].set_index('hour')
    for r in validation.itertuples():
        if not math.isclose(r.rmse_pu, float(original.loc[r.hour, 'rmse_pu']), abs_tol=1e-12):
            raise AssertionError('Regenerated error disagrees with audited raw pairs')

    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.6), sharey=True, layout='constrained')
    for ax, hour in zip(axes, (3, 18)):
        parts = [voltage[(voltage.hour == hour) & (voltage.phase == p)].abs_delta_pu.to_numpy() for p in 'ABC']
        boxes = ax.boxplot(parts, tick_labels=list('ABC'), showfliers=False, patch_artist=True,
                           widths=.55, medianprops={'color':'#202d3d', 'linewidth':1.2})
        for patch, color in zip(boxes['boxes'], (blue, orange, green)):
            patch.set_facecolor(color); patch.set_alpha(.75)
        ax.set_title(f'S0 at {hour:02d}:00')
        ax.set_xlabel('Physical phase')
        ax.grid(axis='y', alpha=.25)
        ax.text(.98, .97, f"RMSE {float(original.loc[hour,'rmse_pu']):.4f} pu", transform=ax.transAxes,
                ha='right', va='top', fontsize=8)
    axes[0].set_ylabel('Absolute voltage difference (pu)')
    save_figure(fig, 'fig9_model_voltage_discrepancy')

    # Physical operational replay. Only common topology, same source policy and 1-h grid.
    pilot = ROOT / 'exact_profile_pilot'
    controls = ROOT / 'alternative_controls'
    baseb = read_csv('S0:base_branches', pilot / 'S0_auto' / 'branches.csv')
    base_lines = set(baseb.loc[baseb.loading_pct > 100, 'line'])
    locations = [
        ('S0', pilot / 'S0_auto'),
        ('S1', pilot / 'S1_auto'),
        ('S4 single BESS', pilot / 'S4_auto'),
        ('S4 distributed', controls / 'distributed_native'),
        ('S4 distributed + control', controls / 'distributed_saved_policy'),
    ]
    case_hours: dict[str, pd.DataFrame] = {}
    metrics = []
    for label, folder in locations:
        hrs, row = hourly_case(label, folder, base_lines)
        case_hours[label] = hrs
        metrics.append(row)
    metrics = pd.DataFrame(metrics)
    write_csv(metrics, 'table_13_opendss_operational_comparison.csv')
    with (DATA / 'fig10_hourly_profiles.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['case', *case_hours['S0'].columns])
        writer.writeheader()
        for label, frame in case_hours.items():
            for row in frame.to_dict('records'):
                writer.writerow({'case':label, **row})
    previous = read_csv('original_native_summary', pilot / 'summary.csv')
    for label, pcase in [('S0','S0'), ('S1','S1'), ('S4 single BESS','S4')]:
        old = previous[(previous.scenario == pcase) & (previous.controls == 'auto')].iloc[0]
        got = metrics[metrics.case == label].iloc[0]
        if not math.isclose(float(old.peak_import_kw),float(got.peak_kw),abs_tol=1e-6):
            raise AssertionError('Native replay changed unexpectedly')
    fig, axs = plt.subplots(3, 1, figsize=(7.15, 7.6), sharex=True, layout='constrained')
    styles = [
        ('S0', '#79889b', '-'), ('S1', orange, '-'), ('S4 single BESS', blue, '-'),
        ('S4 distributed', green, '-'), ('S4 distributed + control', purple, '--'),
    ]
    for label, color, dash in styles:
        f = case_hours[label]
        for ax, column in zip(axs, ('source_kw', 'new_overloaded_lines', 'min_voltage_pu')):
            ax.plot(f.hour, f[column], label=label, color=color, linestyle=dash, linewidth=1.65)
    axs[0].set_ylabel('Substation import (kW)')
    axs[1].set_ylabel('New overloaded lines')
    axs[2].set_ylabel('Minimum MV voltage (pu)')
    axs[2].axhline(.95, color='#b34a51', linestyle=':', linewidth=1.1)
    axs[2].set_xlabel('Hour, interval start (h)')
    axs[2].set_xticks(range(0, 24, 3))
    for ax in axs: ax.grid(alpha=.2)
    axs[0].legend(ncol=2, loc='upper left', fontsize=7.3, frameon=False)
    save_figure(fig, 'fig10_native_operational_replay')

    # Controls: separate native and changed-regulator operating policies.
    details = [
        ('Historical single BESS', pilot / 'S4_auto'),
        ('Distributed, native controls', controls / 'distributed_native'),
        ('Distributed, changed controls', controls / 'distributed_saved_policy'),
        ('Distributed, native S0 taps', controls / 'distributed_native_frozen'),
        ('Distributed, changed S0 taps', controls / 'distributed_policy_frozen'),
    ]
    control_rows = []
    for label, folder in details:
        _, summary = hourly_case(label, folder, base_lines)
        control_rows.append(summary)
    control_rows = pd.DataFrame(control_rows)
    write_csv(control_rows, 'table_14_controls_and_frozen_taps.csv')
    saved = read_csv('V301_verified_summary', controls / 'comparison.csv')
    case = control_rows[control_rows.case == 'Distributed, changed controls'].iloc[0]
    old = saved[saved.case == 'distributed_saved_policy'].iloc[0]
    if not math.isclose(float(case.thermal_excess_pph), float(old.thermal_pph), abs_tol=1e-7):
        raise AssertionError('Distributed replay changed unexpectedly')
    labels = ['Single BESS', 'Distributed\nnative', 'Distributed\nchanged control',
              'Distributed\nnative frozen', 'Distributed\nchanged frozen']
    fig, axs = plt.subplots(1, 3, figsize=(7.15, 3.25), layout='constrained')
    for ax, col, title, unit in zip(axs,
                                    ('thermal_excess_pph','undervoltage_deficit_puh','overvoltage_excess_puh'),
                                    ('Thermal excess','Undervoltage','Overvoltage'),
                                    ('pp·h','pu·h','pu·h')):
        bars = ax.bar(range(5), control_rows[col], color=[blue,green,purple,'#82aa9d','#aa92bf'])
        ax.set_title(title)
        ax.set_ylabel(unit)
        ax.set_xticks(range(5),labels,rotation=50,ha='right',fontsize=7)
        ax.grid(axis='y', alpha=.2)
        ax.bar_label(bars,fmt='%.0f' if col == 'thermal_excess_pph' else '%.2f',fontsize=7,padding=2)
    save_figure(fig, 'fig11_thermal_voltage_control_tradeoff')

    # Same resource budget is not the same network operating cost.
    resources = read_csv('distributed_bess_budget', B / 'results/v301_continuacao_ac_segura/best/bess_siting_sizing.csv')
    fleet = read_csv('distributed_vehicle_siting', B / 'results/v301_continuacao_ac_segura/best/vehicle_dispatch.csv')
    if not math.isclose(float(resources.power_kw.sum()), 2200, abs_tol=.001) or not math.isclose(float(resources.energy_kwh.sum()), 7200, abs_tol=.001):
        raise AssertionError('Distributed BESS budget changed')
    allocation = fleet.groupby('vehicle_id').site.nunique()
    if len(allocation) != 150 or allocation.max() != 1:
        raise AssertionError('V2G assignment is not one site per vehicle')
    vehicles = fleet.groupby('vehicle_id').site.first().value_counts().rename_axis('site').reset_index(name='vehicles')
    write_csv(resources, 'table_S3_distributed_bess_allocation.csv')
    write_csv(vehicles, 'table_S4_distributed_v2g_allocation.csv')
    # Complete S0-S4 attribution after the later native-phase S2/S3 replays.
    distributed = read_csv('distributed_S0_S4_replay', ROOT/'distributed_s2_s3/comparison.csv')
    wanted = ['scenario','policy','peak_import_kw','v_min_pu','v_max_pu','loading_max_pct',
              'new_overloaded_lines_vs_S0','thermal_excess_pph','undervoltage_deficit_puh',
              'overvoltage_excess_puh','loss_kwh']
    for policy, number in [('native',15),('changed',16)]:
        frame = distributed[(distributed.policy==policy)&(distributed.view=='auto')][wanted].copy()
        if len(frame)!=5 or set(frame.scenario)!=set(('S0','S1','S2','S3','S4')):
            raise ValueError(f'Incomplete distributed scenarios under {policy} controls')
        frame['scenario']=pd.Categorical(frame.scenario,categories=['S0','S1','S2','S3','S4'],ordered=True)
        frame=frame.sort_values('scenario')
        write_csv(frame,f'table_{number}_distributed_s0_s4_{policy}.csv')
    historic_full=read_csv('historical_S0_S4_native_replay',ROOT/'native_s2_s3/summary.csv')
    write_csv(historic_full,'table_S6_historical_s2_s3_native_replay.csv')
    site_costs=read_csv('connection_site_requirements',ROOT/'connection_costs/site_requirements.csv')
    write_csv(site_costs,'table_S5_connection_scope_and_missing_costs.csv')
    cost_file=(ROOT/'connection_costs/cost_reconciliation.json').resolve(strict=True)
    SOURCE['connection_cost_reconciliation']={'path':str(cost_file),'sha256':sha256(cost_file)}
    for p in FIG.glob('*'):
        if p.stat().st_size == 0: raise ValueError('Empty figure: '+str(p))
    manifest = dict(status='SOURCE_CHECKED_NOT_EXTERNAL_VALIDATION', source_files=SOURCE,
                    tables={p.name: sha256(p) for p in TAB.glob('*.csv')},
                    figures={p.name: sha256(p) for p in FIG.glob('*')},
                    assumptions=['Voltage comparisons use physical node-phase pairs and fixed-tap historical S0.',
                                 'Hourly native replay uses the full secondary network, original source and study ampacity.',
                                 'The changed-control case differs in regulator setpoints; other comparisons share native controls.',
                                 'New overloads compare each line identity against the original native S0 set.',
                                 'Absolute thermal, undervoltage and overvoltage excesses are reported separately.',
                                 'The three-BESS case is an existing later campaign, not part of the 63 historical paper cases.'])
    (OUT / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2), encoding='utf-8')
    print('Prepared',len(manifest['tables']),'tables,',len(manifest['figures']),'figure files,',len(SOURCE),'source fingerprints')


if __name__ == '__main__':
    build(refresh='--refresh' in sys.argv[1:])
