"""Independent completion and component-additivity check after native replay."""
from pathlib import Path
import json

import numpy as np
import pandas as pd

from audit import OUT, digest
from improve import save


def main() -> None:
    root=OUT/'distributed_s2_s3'
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    required={f'{policy}_{scenario}_{view}' for policy in ('native','changed')
              for scenario,view in [('S2','auto'),('S2','frozen'),('S3','auto'),('S3','frozen')]}
    required.update({'changed_S1_auto','changed_S1_frozen'})
    if set(manifest['completed'])!=required or manifest['errors']:
        raise ValueError('Missing or failed electrical replay; not certifiable as complete')
    if any(digest(Path(path))!=expected for path,expected in manifest['source_sha256'].items()):
        raise ValueError('An input changed during the replay')
    table=pd.read_csv(root/'comparison.csv')
    if len(table)!=18 or table.case.nunique()!=18 or not table.steps.eq(24).all():
        raise ValueError('Incomplete case comparison')
    for name in required:
        folder=root/name
        for key,expected_min in [('source',24),('branches',24*3700),('voltages',24*3800),('regulators',24*12)]:
            frame=pd.read_csv(folder/f'{key}.csv')
            if set(frame.hour)!=set(range(24)) or len(frame)<expected_min:
                raise ValueError(f'{name}/{key}: missing physical hours or observations')
        if name.endswith('_frozen'):
            policy=name.split('_')[0]
            ref=OUT/('exact_profile_pilot/S0_auto/regulators.csv' if policy=='native'
                     else 'alternative_controls/S0_saved_policy/regulators.csv')
            a=pd.read_csv(folder/'regulators.csv')
            b=pd.read_csv(ref)
            pair=a.merge(b,on=['hour','regulator'],validate='one_to_one',suffixes=('_case','_S0'))
            if len(pair)!=len(b) or not pair.tap_case.eq(pair.tap_S0).all():
                raise ValueError('Frozen tap mismatch: '+name)
    profiles={}
    keys=['hour','bus','phase']
    for scenario in ['S1','S2','S3','S4']:
        frame=pd.read_csv(root/f'{scenario}_injections.csv')
        if frame.duplicated(keys).any():raise ValueError('Duplicate phase injection: '+scenario)
        profiles[scenario]=frame.set_index(keys)[['p_kw','q_kvar']]
    index=profiles['S1'].index.union(profiles['S2'].index).union(profiles['S3'].index).union(profiles['S4'].index)
    x={name:frame.reindex(index,fill_value=0).to_numpy(float) for name,frame in profiles.items()}
    additivity=float(np.max(np.abs(x['S4']-x['S2']-x['S3']+x['S1'])))
    if additivity>1e-6:raise ValueError('S2+S3-S1 does not reconstruct S4')
    if float(manifest['s4_reconstruction_max_kw_kvar_error'])>1e-6:
        raise ValueError('S4 did not match the saved distributed physical injection')
    report=dict(status='PASS',new_replays=len(required),cases_in_comparison=len(table),
                S4_component_additivity_max_kw_kvar=additivity,
                S4_saved_reconstruction_max_kw_kvar_error=manifest['s4_reconstruction_max_kw_kvar_error'],
                frozen_taps_verified=True,source_hashes_preserved=True,
                correction='The first runner exit code was 1 because its final bookkeeping check expected 8 instead of the 10 completed new replays. No physical run failed. The corrected runner uses 10; this independent checker verified every completed file and changes only the manifest status.')
    save(root/'verification.json',report)
    manifest['status']='COMPLETE_VERIFIED_AFTER_BOOKKEEPING_CORRECTION'
    manifest['completion_verification']=report
    save(root/'manifest.json',manifest)
    print('Verified',len(required),'new replays;',len(table),'compared cases; additivity error',additivity)


if __name__=='__main__':main()
