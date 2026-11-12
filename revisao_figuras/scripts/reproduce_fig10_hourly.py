"""Re-execute archived OpenDSS replays whose hourly outputs are missing from this copy.

No optimization, sampling or parameter change: every case replays saved injections
(and, for the phase-C case, the saved regulator policy) with the audit code
(rebuild.engine.ODSSEngine, mode='original', ampacity='study', native controls),
using the same per-hour loop as validation_applied_energy_final/check_alternatives.run.

Control cases (stored outputs exist): S0 and S1 historical replays, distributed S4 with
the saved phase-C policy. They must match the stored files before any new case is used.
New cases (stored outputs missing here): historical S4, distributed S4 native,
distributed S2 native, distributed S3 native.

Inputs are read-only and hash-checked against the audit manifests. Outputs are written
only to --out. Run with PYTHONDONTWRITEBYTECODE=1 so no bytecode lands in the audit tree.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REV = HERE.parent
REPO = REV.parent  # repository root
B = REPO
AUDIT = B / 'validation_applied_energy_final'
TABLES = ['voltages', 'branches', 'source', 'regulators', 'capacitors']
KEYS = {'source': (['hour'], ['p_kw', 'q_kvar', 'loss_kw']),
        'voltages': (['hour', 'bus', 'phase'], ['v_pu']),
        'branches': (['hour', 'line', 'phase'], ['current_a', 'loading_pct']),
        'regulators': (['hour', 'regulator'], ['tap'])}
# Acceptance rule (agreed with the author after the first control run).
# A strict 1e-9 absolute tolerance on every column was tried first and failed:
# macOS-arm64 vs the audit's Windows-x86 differ by ~1e-11 relative (taps and
# overloaded-line sets identical; repeated runs on this machine bit-identical).
# Per-column absolute tolerances, far below the printed precision of Tables 13/14:
TOL = {'v_pu': 1e-9, 'loading_pct': 1e-5, 'current_a': 1e-5,
       'p_kw': 1e-4, 'q_kvar': 1e-4, 'loss_kw': 1e-4, 'tap': 0.0}
REL_SUMMARY = 1e-8  # new cases vs audited 24-h summaries (controls reached ~1e-11)
MARGIN_FACTOR = 100  # threshold distance must exceed 100x the column tolerance
SUMMARY_FIELDS = ['peak_import_kw', 'loss_kwh', 'v_min_pu', 'loading_max_pct']
INT_FIELDS = ['overloaded_unique_lines']


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expect(path: Path, value: str, label: str) -> dict:
    got = sha256(path)
    if got != value:
        raise ValueError(f'{label}: SHA-256 {got} differs from audit record {value}')
    return dict(path=str(path), sha256=got, matches_audit=True)


def run(ctx, profile, folder: Path, policy=None):
    """Same steps as check_alternatives.run (no frozen view)."""
    from rebuild.engine import ODSSEngine, error_checked
    from distributed_stochastic.coordenar_controles_v24 import inventory, apply_policy
    folder.mkdir(parents=True, exist_ok=False)
    engine = ODSSEngine(ctx, mode='original', ampacity='study')
    engine.compile()
    error_checked(engine.dss, f'Set DataPath="{folder.as_posix()}"')
    if policy is not None:
        applied = apply_policy(engine, inventory(engine), policy)
        (folder / 'applied_policy.json').write_text(json.dumps(applied, indent=2, default=str), encoding='utf-8')
    engine.define_assets(profile)
    frames = {k: [] for k in TABLES}
    for h in range(24):
        result = engine.solve_hour(h, float(ctx['multipliers'][h]), profile)
        for k, values in result.items():
            frames[k].extend(values)
            pd.DataFrame(values).to_csv(folder / f'{k}.csv', index=False, mode='w' if h == 0 else 'a', header=h == 0)
    return {k: pd.DataFrame(v) for k, v in frames.items()}


def overloaded(folder: Path) -> set:
    b = pd.read_csv(folder / 'branches.csv')
    return set(b.loc[b.loading_pct > 100, 'line'])


def margins(folder: Path) -> dict:
    b, v = pd.read_csv(folder / 'branches.csv'), pd.read_csv(folder / 'voltages.csv')
    m = dict(loading_to_100pct=float((b.loading_pct - 100).abs().min()), v_to_095pu=float((v.v_pu - .95).abs().min()))
    m['passed'] = m['loading_to_100pct'] > MARGIN_FACTOR * TOL['loading_pct'] and m['v_to_095pu'] > MARGIN_FACTOR * TOL['v_pu']
    return m


def compare(new: Path, stored: Path) -> dict:
    out = {}
    for t, (keys, cols) in KEYS.items():
        a, b = pd.read_csv(new / f'{t}.csv'), pd.read_csv(stored / f'{t}.csv')
        j = a.merge(b, on=keys, how='outer', validate='one_to_one', indicator=True, suffixes=('_new', '_stored'))
        if not j._merge.eq('both').all():
            raise ValueError(f'{t}: key sets differ ({(~j._merge.eq("both")).sum()} rows)')
        err = {c: float((j[c + '_new'] - j[c + '_stored']).abs().max()) for c in cols}
        out[t] = dict(pairs=len(j), max_abs_error=err, passed=all(err[c] <= TOL[c] for c in cols),
                      byte_identical=sha256(new / f'{t}.csv') == sha256(stored / f'{t}.csv'))
    out['same_overloaded_lines'] = overloaded(new) == overloaded(stored)
    out['passed'] = out['same_overloaded_lines'] and all(out[t]['passed'] for t in KEYS)
    return out


def check_summary(ctx, folder: Path, base_bad: set, ref: dict, ref_new: int) -> dict:
    from rebuild.contracts import energy_metrics
    f = {t: pd.read_csv(folder / f'{t}.csv') for t in ['source', 'voltages', 'branches']}
    m = energy_metrics(f['source'], f['voltages'], f['branches'], 1.)
    new_lines = len(overloaded(folder) - base_bad)
    rel = {k: abs(float(m[k]) - float(ref[k])) / abs(float(ref[k])) for k in SUMMARY_FIELDS}
    ints = {k: (int(m[k]), int(ref[k])) for k in INT_FIELDS}
    ok = all(v <= REL_SUMMARY for v in rel.values()) and all(a == b for a, b in ints.values()) and new_lines == ref_new
    return dict(rerun={k: float(m[k]) for k in SUMMARY_FIELDS}, audited={k: float(ref[k]) for k in SUMMARY_FIELDS},
                rel_error=rel, integers=ints, new_lines_vs_S0=(new_lines, ref_new), passed=ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mirror', type=Path, required=True, help='hash-checked copy of feeder sources/configs')
    ap.add_argument('--hist-injections', type=Path, required=True, help='folder with S0/S1/S4_injections.csv')
    ap.add_argument('--v301-best', type=Path, required=True, help='extracted v301_continuacao_ac_segura/best')
    ap.add_argument('--stored-replay', type=Path, required=True, help='extracted replay_s0_s4_opendss')
    ap.add_argument('--out', type=Path, default=REV / 'reproduced_opendss')
    a = ap.parse_args()
    sys.path[:0] = [str(B / 'opendss_rebuild'), str(B)]
    import opendssdirect
    from rebuild.contracts import load_context, canonical_profile

    a.out.mkdir(parents=True, exist_ok=False)
    pilot = json.loads((AUDIT / 'exact_profile_pilot/manifest.json').read_text(encoding='utf-8'))
    dist = json.loads((AUDIT / 'distributed_s2_s3/manifest.json').read_text(encoding='utf-8'))
    dist_hash = {Path(k.replace('\\', '/')).name: v for k, v in dist['source_sha256'].items()}
    inputs = {}
    for s in ['S0', 'S1', 'S4']:
        inputs[f'hist_{s}'] = expect(a.hist_injections / f'{s}_injections.csv', pilot['inputs'][s]['sha256'], f'historical {s}')
    inputs['dist_S4'] = expect(a.v301_best / 'injections_reoptimized.csv', dist_hash['injections_reoptimized.csv'], 'distributed S4')
    inputs['policy'] = expect(a.v301_best / 'control_policy_v25.json', dist_hash['control_policy_v25.json'], 'phase-C policy')
    ctx = load_context(a.mirror, a.mirror / 'opendss_rebuild')
    hist = {s: canonical_profile(pd.read_csv(a.hist_injections / f'{s}_injections.csv'), list(range(24)), ctx['buses'])
            for s in ['S0', 'S1', 'S4']}
    dprof = {s: pd.read_csv(AUDIT / f'distributed_s2_s3/{s}_injections.csv') for s in ['S1', 'S2', 'S3', 'S4']}
    saved_s4 = pd.read_csv(a.v301_best / 'injections_reoptimized.csv')
    # The audit-written S2/S3 profiles carry no hash: verify them structurally instead.
    k = ['hour', 'bus', 'phase']
    # BESS buses appear only in S2/S4: outer join, absent rows are zero injection.
    sig = {s: (-1 if s in ('S1', 'S4') else 1) for s in ['S1', 'S2', 'S3', 'S4']}
    parts = [d.set_index(k)[['p_kw', 'q_kvar']] * sig[s] for s, d in dprof.items() if s != 'S4']
    parts.append(-saved_s4.set_index(k)[['p_kw', 'q_kvar']])
    resid = pd.concat(parts).groupby(level=k).sum()
    additivity = float(resid.abs().to_numpy().max())
    s4_csv_error = float((dprof['S4'].set_index(k)[['p_kw', 'q_kvar']] - saved_s4.set_index(k)[['p_kw', 'q_kvar']]).abs().to_numpy().max())
    if s4_csv_error > 1e-9:
        raise ValueError(f'distributed_s2_s3/S4_injections.csv differs from saved S4 by {s4_csv_error}')
    if additivity > 1e-6:
        raise ValueError(f'S2 + S3 - S1 differs from saved S4 by {additivity}')
    policy = json.loads((a.v301_best / 'control_policy_v25.json').read_text(encoding='utf-8'))
    comp = pd.read_csv(AUDIT / 'distributed_s2_s3/comparison.csv').set_index('case')
    pil = pd.read_csv(AUDIT / 'exact_profile_pilot/summary.csv')
    hist4 = pil[(pil.scenario == 'S4') & (pil.controls == 'auto')].iloc[0]
    refs = {'S4_hist_native': (hist4, int(hist4.new_lines_vs_S0), 'exact_profile_pilot/summary.csv S4 auto')}
    for c in ['S2', 'S3', 'S4']:
        r = comp.loc[f'native_{c}_auto']
        refs[f'{c}_dist_native'] = (r, int(r.new_overloaded_lines_vs_S0), f'distributed_s2_s3/comparison.csv native_{c}_auto')
    cases = [  # (name, profile, policy, stored folder or None)
        ('control_S0_native', hist['S0'], None, a.stored_replay / 'S0'),
        ('control_S1_native', hist['S1'], None, a.stored_replay / 'S1'),
        ('control_S4_dist_phaseC', saved_s4, policy, a.v301_best / 'AC'),
        ('S4_hist_native', hist['S4'], None, None),
        ('S4_dist_native', saved_s4, None, None),
        ('S2_dist_native', dprof['S2'], None, None),
        ('S3_dist_native', dprof['S3'], None, None),
    ]
    manifest = dict(
        status='RUNNING', purpose='Regenerate missing Fig. 10 hourly series by re-executing archived replays; no optimization',
        environment=dict(python=sys.version.split()[0], platform=platform.platform(), opendss=opendssdirect.Basic.Version(),
                         numpy=np.__version__, pandas=pd.__version__),
        audit_environment='Windows, Python 3.13.5, DSS C-API 0.14.5, DSS-Python 0.15.7, OpenDSSDirect.py 0.9.4',
        inputs=inputs, s2_s3_s1_s4_additivity_max=additivity, s4_csv_vs_saved_max=s4_csv_error,
        acceptance=dict(first_attempt='1e-9 absolute on every column: FAILED on control_S0_native (platform rounding ~1e-11 relative)',
                        column_tolerance_abs=TOL, summary_rel_tolerance=REL_SUMMARY, threshold_margin_factor=MARGIN_FACTOR,
                        rule='controls: identical taps and overloaded-line sets, column errors within tolerance; '
                             'new cases: audited 24-h summary within relative tolerance, identical line counts, '
                             'threshold margins > factor x tolerance; any failure stops the run'),
        cases={}, errors=[])
    cwd = Path.cwd()
    try:
        for name, profile, pol, stored in cases:
            t0 = time.time()
            print('START', name, flush=True)
            run(ctx, profile, a.out / name, pol)
            os.chdir(cwd)
            entry = dict(seconds=round(time.time() - t0, 1), sha256={t: sha256(a.out / name / f'{t}.csv') for t in TABLES})
            if stored is not None:
                entry['comparison_with_stored'] = compare(a.out / name, stored)
                ok = entry['comparison_with_stored']['passed']
                if name == 'control_S0_native':
                    base_bad = overloaded(a.out / name)
            else:
                ref, ref_new, origin = refs[name]
                entry['audited_summary_source'] = origin
                entry['summary_check'] = check_summary(ctx, a.out / name, base_bad, ref, ref_new)
                entry['threshold_margins'] = margins(a.out / name)
                ok = entry['summary_check']['passed'] and entry['threshold_margins']['passed']
            entry['accepted'] = ok
            print(name, 'ACCEPTED' if ok else 'REJECTED', flush=True)
            if not ok:
                manifest['cases'][name] = entry
                raise RuntimeError(f'{name}: acceptance rule failed; later cases not run')
            manifest['cases'][name] = entry
            (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            print('DONE', name, entry['seconds'], 's', flush=True)
        manifest['status'] = 'COMPLETE_ALL_ACCEPTED'
    except Exception:
        manifest['status'] = 'INCOMPLETE'
        manifest['errors'].append(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        os.chdir(cwd)
        manifest['inputs_unchanged'] = all(sha256(Path(v['path'])) == v['sha256'] for v in inputs.values())
        (a.out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return 0 if manifest['status'].startswith('COMPLETE') else 1


if __name__ == '__main__':
    raise SystemExit(main())
