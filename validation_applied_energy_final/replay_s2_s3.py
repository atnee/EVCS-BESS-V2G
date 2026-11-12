"""Replay historical S2/S3 on the complete IEEE feeder; preserve S0 taps."""
from pathlib import Path
import json
import math
import os
import sys
import traceback

import numpy as np
import pandas as pd
import yaml

from audit import A, B, OUT, digest
from check_alternatives import run
from improve import read_ac, save

sys.path.insert(0, str(B / 'src'))
from core.assets import Station
from v2g.runner import vehicles_from_config, discharge_mask
from v2g.strategies import fleet_profile
from rebuild.contracts import canonical_profile, energy_metrics, load_context


def check_s3_fleet() -> dict:
    root = A / 'results/ieee8500/integration'
    vcfg = yaml.safe_load((A / 'configs/v2g.yaml').read_text(encoding='utf-8'))
    evcs = json.loads((root / 'modules/evcs/S3/manifest.json').read_text(encoding='utf-8'))
    saved = json.loads((root / 'dados/S3_fleet.json').read_text(encoding='utf-8'))
    from core.schemas import TimeGrid
    grid = TimeGrid(**vcfg['time'])
    cars = vehicles_from_config(vcfg)
    validated = fleet_profile(Station(**evcs['metadata']['station']), grid, cars,
                              saved['vehicle_power_kw'], evcs['metadata']['station_demand_kw'],
                              evcs['vehicle_ids'], discharge_mask(vcfg, grid), True)
    if not np.allclose(validated.metadata['energy_kwh'], saved['energy_kwh'], atol=1e-7, rtol=0):
        raise ValueError('Saved S3 vehicle states differ from the reconstructed resource states')
    return dict(vehicles=len(cars), delivered_kwh=float(validated.metadata['v2g_delivered_kwh']),
                reserve_departure_ports_and_soc='PASS', saved_states_match=True)


def metrics(name: str, ac: dict[str, pd.DataFrame], base: dict[str, pd.DataFrame]) -> dict:
    m = energy_metrics(ac['source'], ac['voltages'], ac['branches'], 1.)
    before = set(base['branches'].loc[base['branches'].loading_pct > 100, 'line'])
    after = set(ac['branches'].loc[ac['branches'].loading_pct > 100, 'line'])
    m.update(case=name, new_overloaded_lines_vs_S0=len(after-before),
             new_line_ids_vs_S0=sorted(after-before),
             thermal_excess_pph=float((ac['branches'].loading_pct - 100).clip(lower=0).sum()),
             undervoltage_deficit_puh=float((.95-ac['voltages'].v_pu).clip(lower=0).sum()),
             overvoltage_excess_puh=float((ac['voltages'].v_pu-1.05).clip(lower=0).sum()),
             source_q_kvar_at_p_peak=float(ac['source'].loc[ac['source'].p_kw.idxmax(), 'q_kvar']))
    return m


def main() -> None:
    out = OUT / 'native_s2_s3'
    out.mkdir(exist_ok=False)
    cwd = Path.cwd()
    ctx = load_context(B, B / 'opendss_rebuild')
    baseline = {s:read_ac(OUT / f'exact_profile_pilot/{s}_auto') for s in ('S0','S1','S4')}
    taps = baseline['S0']['regulators']
    manifest = dict(status='RUNNING', model='full original OpenDSS, original source, native automatic controls',
                    dt_h=1.0, inputs={}, completed=[], errors=[], resources={'S3':check_s3_fleet()})
    save(out/'manifest.json', manifest)
    rows = [metrics(s, baseline[s], baseline['S0']) for s in ('S0','S1','S4')]
    try:
        for scenario in ('S2','S3'):
            path = A / f'results/ieee8500/integration/dados/{scenario}_injections.csv'
            manifest['inputs'][scenario] = dict(path=str(path.resolve()), sha256=digest(path))
            profiles = canonical_profile(pd.read_csv(path), list(range(24)), ctx['buses'])
            if scenario=='S2':
                assert (OUT/'energia_historica_verificada.json').is_file(), 'Saved BESS dispatch was not checked'
            for view in ('auto','frozen'):
                key = f'{scenario}_{view}'
                print('START', key, flush=True)
                try:
                    ac = run(ctx, profiles, out/key, frozen=taps if view=='frozen' else None)
                    if view=='frozen':
                        observed = ac['regulators'].merge(taps,on=['hour','regulator'],validate='one_to_one',suffixes=('_actual','_S0'))
                        if len(observed)!=len(taps) or not observed.tap_actual.eq(observed.tap_S0).all():
                            raise RuntimeError('S2/S3 frozen taps do not match hourly S0 taps')
                    result = metrics(key, ac, baseline['S0'])
                    if len(ac['source'])!=24 or not all(math.isfinite(result[f]) for f in
                        ['peak_import_kw','loss_kwh','v_min_pu','v_max_pu','loading_max_pct']):
                        raise RuntimeError('Incomplete or nonfinite S2/S3 electrical result')
                    rows.append(result)
                    manifest['completed'].append(key)
                    pd.DataFrame([{k:v for k,v in row.items() if k!='new_line_ids_vs_S0'} for row in rows]).to_csv(out/'summary.csv',index=False)
                    save(out/f'{key}_metrics.json',result)
                    print('DONE', key, 'new_lines', result['new_overloaded_lines_vs_S0'], 'vmin',result['v_min_pu'],flush=True)
                except Exception:
                    manifest['errors'].append(dict(case=key,traceback=traceback.format_exc()))
                    print(traceback.format_exc(),flush=True)
                finally:
                    os.chdir(cwd)
                    save(out/'manifest.json',manifest)
    finally:
        os.chdir(cwd)
        manifest['inputs_unchanged'] = all(digest(Path(x['path']))==x['sha256'] for x in manifest['inputs'].values())
        manifest['status'] = 'COMPLETE' if len(manifest['completed'])==4 and not manifest['errors'] and manifest['inputs_unchanged'] else 'INCOMPLETE'
        save(out/'manifest.json',manifest)
    if manifest['status']!='COMPLETE':
        raise RuntimeError('S2/S3 replay incomplete: inspect '+str(out/'manifest.json'))


if __name__=='__main__':
    main()
