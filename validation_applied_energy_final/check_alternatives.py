"""AC controls ablations; reuse the saved distributed solution without optimization."""
import json
import os
import sys
import traceback
from pathlib import Path
import pandas as pd
from audit import A,B,OUT,digest
from improve import read_ac,metric_row,save
sys.path.insert(0,str(B))
from rebuild.engine import ODSSEngine,error_checked
from rebuild.contracts import load_context
from distributed_stochastic.coordenar_controles_v24 import inventory,apply_policy


def run(ctx,profile,folder,policy=None,frozen=None):
    folder.mkdir(exist_ok=False)
    engine=ODSSEngine(ctx,mode='original',ampacity='study');engine.compile()
    error_checked(engine.dss,f'Set DataPath="{folder.as_posix()}"')
    if policy is not None:save(folder/'applied_policy.json',apply_policy(engine,inventory(engine),policy))
    engine.define_assets(profile)
    if frozen is not None:error_checked(engine.dss,'BatchEdit RegControl..* enabled=no')
    frames={k:[] for k in ['source','branches','voltages','regulators','capacitors']}
    for h in range(24):
        if frozen is not None:
            for row in frozen[frozen.hour==h].itertuples():
                engine.dss.RegControls.Name(row.regulator);engine.dss.RegControls.TapNumber(int(row.tap))
        try:
            result=engine.solve_hour(h,float(ctx['multipliers'][h]),profile)
        except Exception:
            save(folder/'error.json',dict(hour=h,traceback=traceback.format_exc(),event_log=list(engine.dss.Solution.EventLog()),note='No limits or iteration budgets were changed; incomplete run is not an accepted candidate'))
            raise
        for k,values in result.items():
            frames[k].extend(values)
            pd.DataFrame(values).to_csv(folder/f'{k}.csv',index=False,mode='w' if h==0 else 'a',header=h==0)
        if h in [3,18,23]:print(folder.name,'hour',h,flush=True)
    return {k:pd.DataFrame(v) for k,v in frames.items()}


def main():
    ctx=load_context(B,B/'opendss_rebuild')
    out=OUT/'alternative_controls';out.mkdir(exist_ok=False)
    base=read_ac(OUT/'exact_profile_pilot/S0_auto');s1=read_ac(OUT/'exact_profile_pilot/S1_auto')
    best=B/'results/v301_continuacao_ac_segura/best'
    profile=pd.read_csv(best/'injections_reoptimized.csv')
    policy=json.loads((best/'control_policy_v25.json').read_text(encoding='utf-8'))
    cwd=Path.cwd();rows=[]
    try:
        native=run(ctx,profile,out/'distributed_native')
        rows.append(metric_row('distributed_native',native,base,s1))
        controlled=run(ctx,profile,out/'distributed_saved_policy',policy)
        rows.append(metric_row('distributed_saved_policy',controlled,base,s1))
        empty=pd.DataFrame(columns=['hour','bus','phase','p_kw','q_kvar'])
        policy_s0=run(ctx,empty,out/'S0_saved_policy',policy)
        frozen=run(ctx,profile,out/'distributed_native_frozen',frozen=base['regulators'])
        rows.append(metric_row('distributed_native_frozen',frozen,base,s1))
        frozenp=run(ctx,profile,out/'distributed_policy_frozen',policy=policy,frozen=policy_s0['regulators'])
        rows.append(metric_row('distributed_policy_frozen',frozenp,policy_s0,s1))
        # Compare native re-run with the actual saved V3.0.1 AC values.
        checks=[]
        for k,keys,col in [('branches',['hour','line','phase'],'loading_pct'),('voltages',['hour','bus','phase'],'v_pu'),('source',['hour'],'p_kw')]:
            previous=pd.read_csv(best/'AC'/f'{k}.csv')
            pair=controlled[k].merge(previous,on=keys,validate='one_to_one',suffixes=('_new','_saved'))
            checks.append(dict(table=k,pairs=len(pair),max_error=float((pair[col+'_new']-pair[col+'_saved']).abs().max())))
        save(out/'distributed_reproduction.json',dict(source=str(best),injection_sha256=digest(best/'injections_reoptimized.csv'),comparison=checks))
        pd.DataFrame(rows).to_csv(out/'comparison.csv',index=False)
    finally:os.chdir(cwd)


if __name__=='__main__':main()
