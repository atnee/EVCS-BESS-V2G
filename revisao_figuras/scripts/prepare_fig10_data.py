"""Assemble the stored OpenDSS results plotted in the revised Fig. 10.

No power flow, optimisation or stochastic analysis is executed here. The script only
reads saved (or verified re-executed, see source 3) OpenDSS outputs, recomputes the same derived hourly quantities used
by ``validation_applied_energy_final/build_paper_assets.py`` (``hourly_case``),
checks them against the audited 24-h summaries and writes:

    data/fig10_hourly_traces.csv     hourly values actually plotted in (a)-(c)
    data/fig10_summary_metrics.csv   24-h case metrics plotted in (d)-(f)
    data/fig10_provenance.json       source path, SHA-256 and status per case

Source priority for every hourly case:
  1. the audit folder named in paper_insertions/manifest.json, accepted only if
     its SHA-256 equals the manifest value ("audit_exact");
  2. a project archive (.rar) whose member is byte-identical to the manifest
     file ("archive_byte_identical");
  3. a verified re-execution of the archived replay by scripts/reproduce_fig10_hourly.py
     ("reproduced_verified"): same saved injections, same audit engine code, no
     optimisation; accepted only if that run's manifest is COMPLETE_ALL_ACCEPTED, the
     case passed its acceptance rule and the file hashes equal the manifest record;
  4. for the historical S4 replay only, the predecessor module-based replay of
     the same historical schedule ("archive_predecessor_not_identical"); it is
     plotted only with ``--allow-predecessor`` and is always flagged.
A case with none of these sources is reported as unavailable and is not drawn.

Usage (from any directory):
    python3 prepare_fig10_data.py [--allow-predecessor]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
DATA = OUT / 'data'
REPO = OUT.parent  # repository root
ORIG = REPO
DIST = REPO
AUDIT = DIST / 'validation_applied_energy_final'
MANIFEST = AUDIT / 'paper_insertions' / 'manifest.json'
REPRO = OUT / 'reproduced_opendss'
REPRO_REL_TOL = 1e-8  # same summary tolerance as the acceptance rule of reproduce_fig10_hourly.py
PLOTTABLE = ('audit_exact', 'archive_byte_identical', 'reproduced_verified', 'archive_predecessor_not_identical')

FILES = ('source.csv', 'voltages.csv', 'branches.csv')

# case id -> (label, audit folder, manifest key prefix, archive, archive folder, is_historical_s4)
CASES = {
    'S0_native': ('S0, native controls', AUDIT / 'exact_profile_pilot' / 'S0_auto', 'S0',
                  ORIG / 'results' / 'replay_s0_s4_opendss.rar', 'replay_s0_s4_opendss/S0', False),
    'S1_native': ('S1, native controls', AUDIT / 'exact_profile_pilot' / 'S1_auto', 'S1',
                  ORIG / 'results' / 'replay_s0_s4_opendss.rar', 'replay_s0_s4_opendss/S1', False),
    'S4_hist_native': ('Historical S4 replay, native controls', AUDIT / 'exact_profile_pilot' / 'S4_auto',
                       'S4 single BESS', ORIG / 'results' / 'replay_s0_s4_opendss.rar',
                       'replay_s0_s4_opendss/S4', True),
    'S2_dist_native': ('Distributed S2, native controls', AUDIT / 'distributed_s2_s3' / 'native_S2_auto',
                       None, None, None, False),
    'S3_dist_native': ('Distributed S3, native controls', AUDIT / 'distributed_s2_s3' / 'native_S3_auto',
                       None, None, None, False),
    'S4_dist_native': ('Distributed S4, native controls', AUDIT / 'alternative_controls' / 'distributed_native',
                       'S4 distributed', None, None, False),
    'S4_dist_phaseC': ('Distributed S4, saved phase-C policy',
                       AUDIT / 'alternative_controls' / 'distributed_saved_policy', 'S4 distributed + control',
                       DIST / 'results' / 'v301_continuacao_ac_segura.rar',
                       'v301_continuacao_ac_segura/best/AC', False),
}

# Audited 24-h summaries used to check every hourly source that is plotted.
# (file, row filter) -> the reference row for each case.
SUMMARY_REF = {
    'S0_native': ('distributed_s2_s3/comparison.csv', {'case': 'native_S0_auto'}),
    'S1_native': ('distributed_s2_s3/comparison.csv', {'case': 'native_S1_auto'}),
    'S2_dist_native': ('distributed_s2_s3/comparison.csv', {'case': 'native_S2_auto'}),
    'S3_dist_native': ('distributed_s2_s3/comparison.csv', {'case': 'native_S3_auto'}),
    'S4_dist_native': ('distributed_s2_s3/comparison.csv', {'case': 'native_S4_auto'}),
    'S4_dist_phaseC': ('distributed_s2_s3/comparison.csv', {'case': 'changed_S4_auto'}),
    'S4_hist_native': ('exact_profile_pilot/summary.csv', {'scenario': 'S4', 'controls': 'auto'}),
}

# Rev09 Table 13 (native controls) and Table 14, as printed in the manuscript.
REV09_TABLE13 = {
    'S0': (0, 111.03, 0.93707), 'S1': (41, 132.13, 0.91915), 'S2': (0, 114.57, 0.92438),
    'S3': (32, 129.70, 0.92558), 'S4': (0, 108.51, 0.93648),
}
REV09_TABLE14 = {
    'S4_hist_native': (23, 120.80, 0.92357),
    'S4_dist_native': (0, 108.51, 0.93648),
    'S4_dist_phaseC': (0, 109.36, 0.93752),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def manifest_hashes() -> dict[str, str]:
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    return {k: v['sha256'] for k, v in data['source_files'].items()}


def extract(archive: Path, member_dir: str, dest: Path) -> Path:
    members = [f'{member_dir}/{name}' for name in FILES]
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(['bsdtar', '-xf', str(archive), '-C', str(dest), *members], check=True)
    return dest / member_dir


def resolve(case: str, hashes: dict[str, str], tmp: Path, allow_predecessor: bool) -> dict:
    label, folder, key, archive, member, is_hist = CASES[case]
    expected = {f: hashes.get(f'{key}:{f[:-4]}') for f in FILES} if key else {}
    record = dict(case=case, label=label, audit_folder=str(folder.relative_to(REPO)))
    if all((folder / f).is_file() for f in FILES):
        got = {f: sha256(folder / f) for f in FILES}
        ok = (not key) or all(got[f] == expected[f] for f in FILES)
        record.update(status='audit_exact' if ok else 'audit_folder_hash_mismatch', folder=folder,
                      sha256=got, expected_sha256=expected or None)
        if ok:
            return record
    predecessor = None
    if archive is not None and archive.is_file():
        path = extract(archive, member, tmp / case)
        got = {f: sha256(path / f) for f in FILES}
        identical = bool(expected) and all(got[f] == expected[f] for f in FILES)
        status = 'archive_byte_identical' if identical else 'archive_predecessor_not_identical'
        record.update(status=status, folder=path, archive=str(archive.relative_to(REPO)), member=member,
                      sha256=got, expected_sha256=expected or None)
        if identical:
            return record
        predecessor = dict(record)
    reproduced = reproduced_source(case)
    if reproduced is not None:
        record.update(reproduced)
        return record
    if predecessor is not None:
        if is_hist and allow_predecessor:
            return predecessor
        record['status'] = 'unavailable_predecessor_rejected'
        return record
    record.setdefault('status', 'unavailable')
    return record


def reproduced_source(case: str) -> dict | None:
    man_path = REPRO / 'manifest.json'
    if not man_path.is_file():
        return None
    man = json.loads(man_path.read_text(encoding='utf-8'))
    entry = man.get('cases', {}).get(case)
    if man.get('status') != 'COMPLETE_ALL_ACCEPTED' or not man.get('inputs_unchanged') or not entry or not entry.get('accepted'):
        return None
    folder = REPRO / case
    got = {f: sha256(folder / f) for f in FILES}
    if any(got[f] != entry['sha256'][f[:-4]] for f in FILES):
        return None
    return dict(status='reproduced_verified', folder=folder, reproduced_folder=str(folder.relative_to(REPO)),
                sha256=got, reproduction_manifest=str(man_path.relative_to(REPO)),
                reproduction_environment=man['environment'], acceptance=man['acceptance'],
                control_cases={k: v['comparison_with_stored']['passed'] for k, v in man['cases'].items()
                               if k.startswith('control_')})


def hourly(folder: Path, base_lines: set[str]) -> tuple[pd.DataFrame, dict]:
    """Same definitions as build_paper_assets.hourly_case (audit package)."""
    s = pd.read_csv(folder / 'source.csv').sort_values('hour').reset_index(drop=True)
    v = pd.read_csv(folder / 'voltages.csv')
    b = pd.read_csv(folder / 'branches.csv')
    if list(s.hour) != list(range(24)) or set(v.hour) != set(range(24)) or set(b.hour) != set(range(24)):
        raise ValueError(f'{folder}: expected 24 hourly intervals')
    if v.duplicated(['hour', 'bus', 'phase']).any() or b.duplicated(['hour', 'line', 'phase']).any():
        raise ValueError(f'{folder}: duplicated phase-hour observation')
    new = (b[(b.loading_pct > 100) & ~b.line.isin(base_lines)]
           .groupby('hour').line.nunique().reindex(range(24), fill_value=0))
    frame = pd.DataFrame(dict(
        hour=range(24),
        substation_input_kw=s.p_kw.to_numpy(),
        new_overloaded_lines=new.to_numpy(),
        min_mv_voltage_pu=v.groupby('hour').v_pu.min().reindex(range(24)).to_numpy(),
        max_branch_loading_pct=b.groupby('hour').loading_pct.max().reindex(range(24)).to_numpy(),
    ))
    summary = dict(peak_kw=float(s.p_kw.max()), v_min_pu=float(v.v_pu.min()),
                   loading_max_pct=float(b.loading_pct.max()),
                   new_lines=int(len(set(b.loc[b.loading_pct > 100, 'line']) - base_lines)),
                   loss_kwh=float(s.loss_kw.sum()), bus_phases_per_hour=int(len(v) / 24),
                   lines=int(b.line.nunique()))
    return frame, summary


def reference_row(case: str) -> pd.Series:
    file, flt = SUMMARY_REF[case]
    df = pd.read_csv(AUDIT / file)
    for k, val in flt.items():
        df = df[df[k] == val]
    if len(df) != 1:
        raise ValueError(f'{case}: reference row not unique in {file}')
    return df.iloc[0]


def ref_metrics(row: pd.Series) -> dict:
    new = row['new_overloaded_lines_vs_S0'] if 'new_overloaded_lines_vs_S0' in row else row['new_lines_vs_S0']
    return dict(peak_kw=float(row.peak_import_kw), v_min_pu=float(row.v_min_pu),
                loading_max_pct=float(row.loading_max_pct), new_lines=int(new),
                loss_kwh=float(row.loss_kwh))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--allow-predecessor', action='store_true',
                    help='plot the flagged predecessor replay of the historical S4 when the exact one is absent')
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    hashes = manifest_hashes()
    tmp = Path(tempfile.mkdtemp(prefix='fig10_'))
    try:
        records = {c: resolve(c, hashes, tmp, args.allow_predecessor) for c in CASES}
        if records['S0_native']['status'] not in ('audit_exact', 'archive_byte_identical'):
            raise RuntimeError('Native S0 is required to define pre-existing overloaded lines')
        b0 = pd.read_csv(records['S0_native']['folder'] / 'branches.csv')
        base_lines = set(b0.loc[b0.loading_pct > 100, 'line'])

        traces, provenance = [], []
        for case, rec in records.items():
            entry = {k: (str(v) if isinstance(v, Path) else v) for k, v in rec.items() if k != 'folder'}
            if 'folder' in rec and rec['status'] in PLOTTABLE:
                frame, got = hourly(rec['folder'], base_lines)
                ref = ref_metrics(reference_row(case))
                diff = {k: got[k] - ref[k] for k in ref}
                if rec['status'] == 'reproduced_verified':
                    exact = got['new_lines'] == ref['new_lines'] and all(
                        math.isclose(got[k], ref[k], rel_tol=REPRO_REL_TOL) for k in ref if k != 'new_lines')
                else:
                    exact = all(math.isclose(got[k], ref[k], rel_tol=0, abs_tol=1e-6) for k in ref)
                entry.update(recomputed_24h=got, audited_24h=ref, difference=diff,
                             matches_audited_summary=exact, plotted=True)
                if rec['status'] != 'archive_predecessor_not_identical' and not exact:
                    raise AssertionError(f'{case}: {rec["status"]} source does not reproduce audit summary')
                frame.insert(0, 'case', case)
                frame.insert(1, 'source_status', rec['status'])
                traces.append(frame)
            else:
                entry.update(plotted=False)
            provenance.append(entry)

        pd.concat(traces).to_csv(DATA / 'fig10_hourly_traces.csv', index=False, float_format='%.10g')

        # 24-h metrics for panels (d)-(f): audited summaries, no hourly reconstruction.
        comp = pd.read_csv(AUDIT / 'distributed_s2_s3' / 'comparison.csv')
        rows = []
        for sc in ('S0', 'S1', 'S2', 'S3', 'S4'):
            r = comp[comp.case == f'native_{sc}_auto'].iloc[0]
            rows.append(dict(group='distributed_native', scenario=sc, policy='native', view='auto',
                             new_overloaded_lines=int(r.new_overloaded_lines_vs_S0),
                             max_branch_loading_pct=float(r.loading_max_pct), min_voltage_pu=float(r.v_min_pu),
                             source='validation_applied_energy_final/distributed_s2_s3/comparison.csv'))
        r = comp[comp.case == 'changed_S4_auto'].iloc[0]
        rows.append(dict(group='distributed_phaseC', scenario='S4', policy='saved phase-C', view='auto',
                         new_overloaded_lines=int(r.new_overloaded_lines_vs_S0),
                         max_branch_loading_pct=float(r.loading_max_pct), min_voltage_pu=float(r.v_min_pu),
                         source='validation_applied_energy_final/distributed_s2_s3/comparison.csv'))
        r = reference_row('S4_hist_native')
        rows.append(dict(group='historical_replay', scenario='S4', policy='native', view='auto',
                         new_overloaded_lines=int(r.new_lines_vs_S0),
                         max_branch_loading_pct=float(r.loading_max_pct), min_voltage_pu=float(r.v_min_pu),
                         source='validation_applied_energy_final/exact_profile_pilot/summary.csv'))
        summary = pd.DataFrame(rows)

        # Consistency with the printed Rev09 Tables 13 and 14 (manuscript rounding).
        checks = []
        for sc, (n, lmax, vmin) in REV09_TABLE13.items():
            r = summary[(summary.group == 'distributed_native') & (summary.scenario == sc)].iloc[0]
            checks.append(dict(table='13', row=sc, ok=bool(r.new_overloaded_lines == n and
                               round(r.max_branch_loading_pct, 2) == lmax and round(r.min_voltage_pu, 5) == vmin)))
        for case, group in (('S4_hist_native', 'historical_replay'), ('S4_dist_native', 'distributed_native'),
                            ('S4_dist_phaseC', 'distributed_phaseC')):
            n, lmax, vmin = REV09_TABLE14[case]
            r = summary[(summary.group == group) & (summary.scenario == 'S4')].iloc[0]
            checks.append(dict(table='14', row=case, ok=bool(r.new_overloaded_lines == n and
                               round(r.max_branch_loading_pct, 2) == lmax and round(r.min_voltage_pu, 5) == vmin)))
        if not all(c['ok'] for c in checks):
            raise AssertionError(f'Stored results disagree with Rev09 tables: {checks}')
        s2 = summary[(summary.group == 'distributed_native') & (summary.scenario == 'S2')].iloc[0]
        s4 = summary[(summary.group == 'distributed_native') & (summary.scenario == 'S4')].iloc[0]
        # Rev09 reports differences of the tabulated (rounded) values: 114.57 - 108.51 = 6.06 pp and
        # 0.93648 - 0.92438 = 0.01210 pu. The unrounded loading difference is 6.0656 pp (6.07 when
        # rounded); both are recorded so the editorial choice stays visible.
        deltas = dict(
            loading_reduction_pp_unrounded=float(s2.max_branch_loading_pct - s4.max_branch_loading_pct),
            voltage_gain_pu_unrounded=float(s4.min_voltage_pu - s2.min_voltage_pu),
            loading_reduction_pp_tabulated=round(round(s2.max_branch_loading_pct, 2)
                                                 - round(s4.max_branch_loading_pct, 2), 2),
            voltage_gain_pu_tabulated=round(round(s4.min_voltage_pu, 5) - round(s2.min_voltage_pu, 5), 5))
        if deltas['loading_reduction_pp_tabulated'] != 6.06 or deltas['voltage_gain_pu_tabulated'] != 0.01210:
            raise AssertionError(f'S2-S4 contrast differs from Rev09: {deltas}')
        summary.to_csv(DATA / 'fig10_summary_metrics.csv', index=False, float_format='%.10g')

        (DATA / 'fig10_provenance.json').write_text(json.dumps(dict(
            note=('Derived from stored OpenDSS outputs; cases with status reproduced_verified come from a '
                  'verified re-execution of the archived replays (scripts/reproduce_fig10_hourly.py, no '
                  'optimisation). No power flow is executed by this script.'),
            pre_existing_overloaded_lines_native_S0=sorted(base_lines),
            cases=provenance, rev09_table_checks=checks, s2_vs_s4=deltas,
            allow_predecessor=args.allow_predecessor), indent=2, default=str), encoding='utf-8')
        for p in provenance:
            print(f"{p['case']:16s} {p['status']:36s} plotted={p['plotted']}"
                  + (f"  matches_audit={p.get('matches_audited_summary')}" if p['plotted'] else ''))
        print('Rev09 Tables 13/14 checks:', all(c['ok'] for c in checks), '| S2->S4:', deltas)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
