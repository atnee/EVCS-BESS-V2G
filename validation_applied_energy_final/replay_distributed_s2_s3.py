"""Isolate distributed BESS and V2G using saved schedules on the full feeder."""
from pathlib import Path
import json
import math
import os
import sys
import traceback

import numpy as np
import pandas as pd

from audit import A, B, OUT, digest
from check_alternatives import run
from improve import read_ac, save
from replay_s2_s3 import metrics

sys.path[:0] = [str(B),str(B/'src'),str(B/'opendss_rebuild')]
from reotimizar_bess_v2g import read_existing, read_team
from distributed_stochastic.otimizar_siting_v21 import build_injections
from v2g.strategies import fleet_profile
from rebuild.contracts import load_context


def assert_same_injections(expected: pd.DataFrame, saved: pd.DataFrame) -> float:
    keys=['hour','bus','phase']
    j=expected.merge(saved,on=keys,how='outer',validate='one_to_one',indicator=True,suffixes=('_computed','_saved'))
    if not j._merge.eq('both').all() or len(j)!=len(expected) or len(j)!=len(saved):
        raise ValueError('S4 reconstruction keys do not match the saved physical replay')
    error=max(float((j.p_kw_computed-j.p_kw_saved).abs().max()),float((j.q_kvar_computed-j.q_kvar_saved).abs().max()))
    if error>1e-6:raise ValueError(f'S4 reconstruction differs by {error} kW/kvar')
    return error


def main() -> None:
    output=OUT/'distributed_s2_s3'
    output.mkdir(exist_ok=False)
    cwd=Path.cwd()
    ctx=load_context(B,B/'opendss_rebuild')
    historical=A/'results/replay_s0_s4_opendss'
    team=read_team(B,historical)
    _,s1=read_existing(historical)
    best=B/'results/v301_continuacao_ac_segura/best'
    sizes=pd.read_csv(best/'bess_siting_sizing.csv')
    dispatch=pd.read_csv(best/'bess_dispatch.csv')
    cars=pd.read_csv(best/'vehicle_dispatch.csv')
    bess={site:dispatch[dispatch.site==site].set_index('hour').p_kw.reindex(range(24)).to_numpy(float) for site in sizes.site}
    ids=[vehicle.id for vehicle in team['vehicles']]
    vehicle=cars.pivot(index='vehicle_id',columns='hour',values='p_kw').loc[ids,range(24)].to_numpy(float)
    places=cars.groupby('vehicle_id').site.first().reindex(ids).tolist()
    if set(places)!={'m1069504','m1209790','l3160107'}:
        raise ValueError('Unexpected distributed V2G locations')
    if not json.loads((OUT/'alternative_controls/resource_validation.json').read_text(encoding='utf-8'))['resource_validators_passed']:
        raise ValueError('Saved distributed resource validator did not pass')
    # S2 retains the individually validated uncontrolled charging of all 150 cars.
    fleet_profile(team['station'],team['grid'],team['vehicles'],team['old_fleet'],team['ind'],
                  ('independent-demand-group',),None,False)
    old_places=[team['v2g_bus']]*len(ids)
    zeros={site:np.zeros(24) for site in bess}
    s2=build_injections(s1,team['old_fleet_profile'],bess,team['old_fleet'],old_places)
    s3=build_injections(s1,team['old_fleet_profile'],zeros,vehicle,places)
    s4=build_injections(s1,team['old_fleet_profile'],bess,vehicle,places)
    saved_s4=best/'injections_reoptimized.csv'
    reconstruction_error=assert_same_injections(s4,pd.read_csv(saved_s4))
    for label,profile in [('S1',s1),('S2',s2),('S3',s3),('S4',s4)]:
        profile.to_csv(output/f'{label}_injections.csv',index=False)
    policy_path=best/'control_policy_v25.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8'))
    sources=[historical/'S1/injections_replayed.csv',best/'bess_siting_sizing.csv',best/'bess_dispatch.csv',
             best/'vehicle_dispatch.csv',saved_s4,policy_path]
    manifest=dict(status='RUNNING', model='original full OpenDSS with study ampacities',
                  baseline='Same native-policy S0/S1 and changed-policy S0/S1 as the S4 comparator',
                  profiles='S2 = saved S1 + 3 BESS; S3 = saved S1 with only 150 V2G cars relocated and rescheduled',
                  s4_reconstruction_max_kw_kvar_error=reconstruction_error,
                  resource_validator='PASS', source_sha256={str(p.resolve()):digest(p) for p in sources},
                  completed=[],errors=[])
    save(output/'manifest.json',manifest)
    table=[]
    try:
        for label,policy_cfg in [('native',None),('changed',policy)]:
            s0=read_ac(OUT/('exact_profile_pilot/S0_auto' if label=='native' else 'alternative_controls/S0_saved_policy'))
            s0_taps=s0['regulators']
            cases={'S0_auto':s0}
            if label=='native':
                cases['S1_auto']=read_ac(OUT/'exact_profile_pilot/S1_auto')
                cases['S4_auto']=read_ac(OUT/'alternative_controls/distributed_native')
                cases['S1_frozen']=read_ac(OUT/'exact_profile_pilot/S1_frozen')
                cases['S4_frozen']=read_ac(OUT/'alternative_controls/distributed_native_frozen')
            else:
                cases['S4_auto']=read_ac(OUT/'alternative_controls/distributed_saved_policy')
                cases['S4_frozen']=read_ac(OUT/'alternative_controls/distributed_policy_frozen')
            for sc,view in [('S1','auto'),('S1','frozen'),('S2','auto'),('S2','frozen'),('S3','auto'),('S3','frozen')]:
                key=f'{sc}_{view}'
                if key in cases:continue
                print('START',label,key,flush=True)
                try:
                    ac=run(ctx,{'S1':s1,'S2':s2,'S3':s3}[sc],output/f'{label}_{key}',
                           policy=policy_cfg,frozen=s0_taps if view=='frozen' else None)
                    if view=='frozen':
                        taps=ac['regulators'].merge(s0_taps,on=['hour','regulator'],validate='one_to_one',suffixes=('_actual','_s0'))
                        if len(taps)!=len(s0_taps) or not taps.tap_actual.eq(taps.tap_s0).all():
                            raise RuntimeError('Frozen taps differ from this policy\'s hourly S0')
                    if len(ac['source'])!=24:raise RuntimeError('Incomplete distributed replay')
                    cases[key]=ac
                    manifest['completed'].append(f'{label}_{key}')
                    save(output/'manifest.json',manifest)
                except Exception:
                    manifest['errors'].append(dict(case=f'{label}_{key}',traceback=traceback.format_exc()))
                    print(traceback.format_exc(),flush=True)
                finally:os.chdir(cwd)
            for key,ac in cases.items():
                try:
                    entry=metrics(f'{label}_{key}',ac,s0)
                    entry.update(policy=label,scenario=key.split('_')[0],view=key.split('_')[1],
                                 profile_origin='historical_S1' if key.startswith(('S0','S1')) else 'distributed_saved_dispatch_isolated')
                    table.append({k:v for k,v in entry.items() if k!='new_line_ids_vs_S0'})
                except Exception:
                    manifest['errors'].append(dict(case=f'{label}_{key}:metrics',traceback=traceback.format_exc()))
            pd.DataFrame(table).to_csv(output/'comparison.csv',index=False)
    finally:
        os.chdir(cwd)
        manifest['inputs_unchanged']=all(digest(Path(p))==hash_value for p,hash_value in manifest['source_sha256'].items())
        # Four native and six changed-policy replays are computed here.
        manifest['status']='COMPLETE' if len(manifest['completed'])==10 and not manifest['errors'] and manifest['inputs_unchanged'] else 'INCOMPLETE'
        save(output/'manifest.json',manifest)
    if manifest['status']!='COMPLETE':
        raise RuntimeError('Distributed S2/S3 incomplete: inspect '+str(output/'manifest.json'))


if __name__=='__main__':
    main()
