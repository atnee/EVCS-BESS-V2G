"""Replay exact historical injections on native phases, without reoptimization."""
import json
import os
import sys
import traceback
from pathlib import Path
import pandas as pd
from audit import A,B,OUT,digest


def main():
    sys.path.insert(0,str(B/'opendss_rebuild'))
    from rebuild.contracts import load_context,canonical_profile,energy_metrics
    from rebuild.engine import ODSSEngine,error_checked
    ctx=load_context(B,B/'opendss_rebuild')
    output=OUT/'exact_profile_pilot'
    output.mkdir(exist_ok=False)
    cwd=Path.cwd()
    files={s:A/f'results/ieee8500/integration/dados/{s}_injections.csv' for s in ['S0','S1','S4']}
    profiles={s:canonical_profile(pd.read_csv(p),list(range(24)),ctx['buses']) for s,p in files.items()}
    manifest=dict(status='RUNNING',inputs={s:dict(path=str(p),sha256=digest(p)) for s,p in files.items()},conditions='Native full IEEE network, original source and capacitor controls, study ampacities; identical historical injections; frozen means hourly S0 RegControl tap numbers, capacitor controls remain active',dt_h=1,errors=[])
    source_taps={}
    summaries=[]
    basebad=set()
    try:
        for scenario,mode in [('S0','auto'),('S1','auto'),('S4','auto'),('S1','frozen'),('S4','frozen')]:
            engine=ODSSEngine(ctx,mode='original',ampacity='study')
            engine.compile()
            dss=engine.dss
            error_checked(dss,f'Set DataPath="{output.as_posix()}"')
            engine.define_assets(profiles[scenario])
            if mode=='frozen':
                error_checked(dss,'BatchEdit RegControl..* enabled=no')
            folder=output/f'{scenario}_{mode}';folder.mkdir()
            tables={k:[] for k in ['voltages','branches','source','regulators','capacitors']}
            balances=[]
            for hour in range(24):
                if mode=='frozen':
                    for name,tap in source_taps[hour].items():
                        dss.RegControls.Name(name)
                        dss.RegControls.TapNumber(tap)
                result=engine.solve_hour(hour,float(ctx['multipliers'][hour]),profiles[scenario])
                if scenario=='S0':
                    source_taps[hour]={r['regulator']:r['tap'] for r in result['regulators']}
                if mode=='frozen' and any(r['tap']!=source_taps[hour][r['regulator']] for r in result['regulators']):
                    raise RuntimeError('Frozen S0 tap mismatch')
                p_sink=q_sink=0.
                for name in dss.Circuit.AllElementNames():
                    if not name.lower().startswith(('load.','generator.','capacitor.')):
                        continue
                    dss.Circuit.SetActiveElement(name)
                    if dss.CktElement.Enabled():
                        pq=dss.CktElement.Powers();p_sink+=sum(pq[::2]);q_sink+=sum(pq[1::2])
                lp,lq=[x/1000 for x in dss.Circuit.Losses()]
                balances.append(dict(hour=hour,residual_kw=result['source'][0]['p_kw']-p_sink-lp,residual_kvar=result['source'][0]['q_kvar']-q_sink-lq))
                for key in tables:
                    tables[key].extend(result[key])
                    pd.DataFrame(result[key]).to_csv(folder/f'{key}.csv',mode='w' if hour==0 else 'a',header=hour==0,index=False)
                pd.DataFrame([balances[-1]]).to_csv(folder/'balances.csv',mode='w' if hour==0 else 'a',header=hour==0,index=False)
                if hour in [3,18,23]:print(scenario,mode,'hour',hour,'completed',flush=True)
            frames={k:pd.DataFrame(v) for k,v in tables.items()}
            m=energy_metrics(frames['source'],frames['voltages'],frames['branches'],1.)
            bad=set(frames['branches'].loc[frames['branches'].loading_pct>100,'line'])
            if scenario=='S0':basebad=bad
            bal=pd.DataFrame(balances)
            m.update(scenario=scenario,controls=mode,new_lines_vs_S0=len(bad-basebad),max_balance_residual_kw=float(bal.residual_kw.abs().max()),max_balance_residual_kvar=float(bal.residual_kvar.abs().max()))
            summaries.append(m)
            pd.DataFrame(summaries).to_csv(output/'summary.csv',index=False)
            manifest['completed_cases']=len(summaries)
            (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        manifest['status']='COMPUTED_NOT_EXTERNALLY_VALIDATED'
    except Exception:
        manifest['status']='INCOMPLETE'
        manifest['errors'].append(traceback.format_exc())
        print(traceback.format_exc(),flush=True)
    finally:
        os.chdir(cwd)
        manifest['inputs_unchanged']=all(digest(p)==manifest['inputs'][s]['sha256'] for s,p in files.items())
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return int(bool(manifest['errors']))


if __name__=='__main__':
    raise SystemExit(main())
