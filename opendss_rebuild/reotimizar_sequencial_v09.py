#!/usr/bin/env python3
"""V0.9: sequential OpenDSS-guided BESS/V2G optimization, keeping V0.8 unchanged.

Uses an AC-replayed S4 as the initial point; at each accepted iteration,
updates finite differences at the actual AC operating point, solves a 24-hour
per-vehicle MILP within a trust region, and accepts only AC-tested improvement.
No guarantee of feasibility or global optimality. OpenDSS is required on Windows.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
import traceback
import numpy as np
import pandas as pd

import reotimizar_bess_v2g as v08
from rebuild.contracts import load_context,sha256
from rebuild.engine import ODSSEngine,error_checked
from rebuild.joint_optimization import solve_joint,OptSettings

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
TABLES=("source","branches","voltages","regulators","capacitors")


def dump(path, payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload,indent=2,ensure_ascii=False,default=str),encoding='utf-8')


def load_tables(directory):
    return {n:pd.read_csv(directory/f'{n}.csv') for n in TABLES}


def read_seed(folder, team, ctx):
    if not (folder/'S4/injections_reoptimized.csv').is_file():
        raise FileNotFoundError('Requer resultados da V0.8: S4/injections_reoptimized.csv')
    manifest=json.loads((folder/'S4/manifest.json').read_text(encoding='utf-8'))
    if manifest.get('mode')!='S4':raise ValueError('Arquivo V0.8 não corresponde ao S4')
    for k,path in ctx['files'].items():
        if manifest['original_files_sha256'].get(k)!=sha256(path):
            raise RuntimeError('Configurações alteradas desde a V0.8: '+k)
    sc=manifest['simulation']
    for k in ('source_mode','controls','ampacity'):
        if sc[k]!=ctx['settings']['simulation'][k]:
            raise RuntimeError(f'V0.8 e V0.9 divergem na opção {k}')
    if sc['controls']!='auto':raise RuntimeError('Necessário OpenDSS com controles automáticos')
    d=pd.read_csv(folder/'S4/dispatch_24h.csv').sort_values('hour')
    if d.hour.tolist()!=list(range(24)):raise ValueError('Despacho V0.8 sem 24 horas')
    vp=pd.read_csv(folder/'S4/vehicle_dispatch.csv')
    vehicles=team['vehicles']; ids=[v.id for v in vehicles]
    if vp.duplicated(['hour','vehicle_id']).any():raise ValueError('Despacho V0.8 duplicado')
    if set(vp.vehicle_id)!=set(ids):raise ValueError('IDs de veículos V0.8 diferentes dos configurados')
    matrix=vp.pivot(index='vehicle_id',columns='hour',values='p_kw').loc[ids,list(range(24))].to_numpy(float)
    if not np.allclose(matrix.sum(axis=0),d.v2g_fleet_net_injection_kw.to_numpy(float),atol=1e-3):
        raise ValueError('Despacho agregado V2G não confere com os 150 veículos')
    x=pd.read_csv(folder/'S4/injections_reoptimized.csv')
    for key in ('bus','phase'):
        x[key]=x[key].astype(str).str.lower() if key=='bus' else x[key].astype(str).str.upper()
    if sorted(x.hour.unique().tolist())!=list(range(24)) or x.duplicated(['hour','bus','phase']).any():
        raise ValueError('Injeções S4 incompletas ou duplicadas')
    return dict(dfs=load_tables(folder/'S4'),profile=x,
                bess=d.bess_net_injection_kw.to_numpy(float),
                fleet=d.v2g_fleet_net_injection_kw.to_numpy(float),
                vehicles=matrix,manifest=manifest,seed_dir=folder/'S4')


def check_reference(reference, observed):
    errors=v08.evaluate_s1_replay(reference,observed)
    # V0.8 uses exactly these limits for independent S1 AC replay.
    return errors


def finite_difference(ctx,team,reference,output,delta_kw):
    """Replays 24 reference hours with automatic controls, freezes only perturbations."""
    engine=ODSSEngine(ctx,ctx['settings']['simulation']['source_mode'],ctx['settings']['simulation']['ampacity'])
    engine.compile()
    probe=reference['profile'].copy()
    extra=[]
    for bus,maxkw in ((team['bess_bus'],team['battery'].power_kw),
                      (team['v2g_bus'],team['station'].connection_kw)):
        for phase in 'ABC':
            extra.append(dict(hour=99,bus=bus,phase=phase,p_kw=maxkw/3.,q_kvar=0.))
    probe=pd.concat([probe,pd.DataFrame(extra)],ignore_index=True)
    engine.define_assets(probe)
    dss=engine.dss
    rows={name:[] for name in TABLES}
    dl=[];dv=[];ds=[]
    for h in range(24):
        baseline=engine.solve_hour(h,float(ctx['multipliers'][h]),reference['profile'])
        for name in TABLES: rows[name].extend(baseline[name])
        bline={(r['line'],r['phase']):r['loading_pct'] for r in baseline['branches']}
        bvolt={(r['bus'],r['phase']):r['v_pu'] for r in baseline['voltages']}
        src=float(baseline['source'][0]['p_kw'])
        regs={(r['regulator'],r['tap']) for r in baseline['regulators']}
        caps={(r['capacitor'],r['states']) for r in baseline['capacitors']}
        original=reference['profile'].loc[reference['profile'].hour==h]
        perturb={}
        error_checked(dss,'Set controlmode=off')
        try:
            for kind,bus in (('bess',team['bess_bus']),('v2g',team['v2g_bus'])):
                # Delta positive means a SMALL additional injection, even if reference is charging.
                engine.update_assets(v08.with_delta(original,bus,delta_kw))
                error_checked(dss,'Solve')
                if not dss.Solution.Converged():raise RuntimeError(f'Não convergiu h={h} {kind}')
                e=engine.read_results(h)
                if {(r['regulator'],r['tap']) for r in e['regulators']}!=regs:
                    raise RuntimeError(f'Tap alterado durante perturbação fixa h={h}, {kind}')
                if {(r['capacitor'],r['states']) for r in e['capacitors']}!=caps:
                    raise RuntimeError(f'Capacitor mudou durante perturbação h={h}, {kind}')
                perturb[kind]=e
                engine.update_assets(original);error_checked(dss,'Solve')
                if not dss.Solution.Converged():raise RuntimeError(f'Não restaurou base h={h}')
            pm={kind:{(r['line'],r['phase']):r['loading_pct'] for r in x['branches']} for kind,x in perturb.items()}
            pv={kind:{(r['bus'],r['phase']):r['v_pu'] for r in x['voltages']} for kind,x in perturb.items()}
            for kind in ('bess','v2g'):
                if set(pm[kind])!=set(bline) or set(pv[kind])!=set(bvolt):
                    raise RuntimeError(f'Conjunto de fases mudou em h={h} {kind}')
            for (line,ph),value in bline.items():
                dl.append(dict(hour=h,line=line,phase=ph,
                               dl_bess=(pm['bess'][line,ph]-value)/delta_kw,
                               dl_v2g=(pm['v2g'][line,ph]-value)/delta_kw))
            for (bus,ph),value in bvolt.items():
                dv.append(dict(hour=h,bus=bus,phase=ph,
                               dv_bess=(pv['bess'][bus,ph]-value)/delta_kw,
                               dv_v2g=(pv['v2g'][bus,ph]-value)/delta_kw))
            ds.append(dict(hour=h,dp_bess=(perturb['bess']['source'][0]['p_kw']-src)/delta_kw,
                           dp_v2g=(perturb['v2g']['source'][0]['p_kw']-src)/delta_kw))
        finally:
            engine.update_assets(original)
            error_checked(dss,'Set controlmode=static')
        print(f'[V0.9 sensitivities] {h:02d}/23',flush=True)
    observed={name:pd.DataFrame(records) for name,records in rows.items()}
    errors=check_reference(reference['dfs'],observed)
    folder=output/'sensitivity';folder.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(dl).to_csv(folder/'lines.csv',index=False)
    pd.DataFrame(dv).to_csv(folder/'voltages.csv',index=False)
    pd.DataFrame(ds).to_csv(folder/'source.csv',index=False)
    dump(folder/'check.json',dict(delta_kw=delta_kw,replay_max_error=errors,
                                  note='AC reference automatic; derivatives frozen controls'))
    return pd.DataFrame(dl),pd.DataFrame(dv),pd.DataFrame(ds),errors


def relative_score(s0,s4,weights):
    """Quantifies ALREADY existing IEEE S0 violations separately from added violations."""
    bk=['hour','line','phase'];vk=['hour','bus','phase']
    br=s4['branches'][bk+['loading_pct']].merge(s0['branches'][bk+['loading_pct']],on=bk,
                                                    suffixes=('_new','_s0'),validate='one_to_one')
    if len(br)!=len(s4['branches']):raise RuntimeError('Não comparáveis: linhas S0 e S4')
    bv=np.maximum(100.,br.loading_pct_s0.to_numpy(float))
    therm=float(np.maximum(br.loading_pct_new.to_numpy(float)-bv,0).sum())
    vv=s4['voltages'][vk+['v_pu']].merge(s0['voltages'][vk+['v_pu']],on=vk,
                                          suffixes=('_new','_s0'),validate='one_to_one')
    if len(vv)!=len(s4['voltages']):raise RuntimeError('Não comparáveis: barras S0 e S4')
    vn=vv.v_pu_new.to_numpy(float);v0=vv.v_pu_s0.to_numpy(float)
    under=float(np.maximum(np.minimum(.95,v0)-vn,0).sum())
    over=float(np.maximum(vn-np.maximum(1.05,v0),0).sum())
    peak=float(max(s4['source'].p_kw.max(),0))
    merit=float(weights['thermal']*therm+weights['voltage']*(under+over)+weights['peak']*peak)
    return dict(thermal_vs_s0_pph=therm,under_vs_s0_puh=under,over_vs_s0_puh=over,
                total_voltage_vs_s0_puh=under+over,peak_import_kw=peak,ac_merit=merit,
                overload_samples=int((s4['branches'].loading_pct>100).sum()),
                voltage_violation_samples=int(((s4['voltages'].v_pu<.95)|(s4['voltages'].v_pu>1.05)).sum()),
                overloaded_unique_lines=int(s4['branches'].loc[s4['branches'].loading_pct>100,'line'].nunique()))


def decision(before,after,max_thermal_growth,max_voltage_growth,minimum_merit_drop):
    if not np.isfinite(after['ac_merit']):return False,'candidate merit nonfinite'
    if after['thermal_vs_s0_pph'] > before['thermal_vs_s0_pph']*(1+max_thermal_growth)+1e-4:
        return False,'AC thermal severity growth too large'
    if after['total_voltage_vs_s0_puh'] > before['total_voltage_vs_s0_puh']*(1+max_voltage_growth)+1e-6:
        return False,'AC voltage severity growth too large'
    if before['ac_merit']-after['ac_merit']<minimum_merit_drop:
        return False,'AC merit did not improve sufficiently'
    return True,'AC merit and safety-guard tests passed'


def save_candidate(solution, folder,team):
    folder.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(dict(hour=np.arange(24),bess_charge_kw=solution['bess_charge_kw'],
                      bess_discharge_kw=solution['bess_discharge_kw'],
                      bess_net_injection_kw=solution['bess_net_kw'],
                      v2g_fleet_net_injection_kw=solution['v2g_net_kw'])).to_csv(folder/'dispatch_24h.csv',index=False)
    pd.DataFrame({'boundary':range(25),'energy_kwh':solution['bess_energy_kwh'],
                  'soc':solution['bess_energy_kwh']/team['battery'].capacity_kwh}).to_csv(folder/'bess_soc.csv',index=False)
    mat=pd.DataFrame(solution['vehicle_power_kw'].T,columns=[v.id for v in team['vehicles']]);mat.insert(0,'hour',range(24))
    mat.melt(id_vars='hour',var_name='vehicle_id',value_name='p_kw').to_csv(folder/'vehicle_dispatch.csv',index=False)
    dump(folder/'milp_diagnostic.json',solution['report'])


def make_cli():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--results',type=Path,required=True,help='Old OpenDSS replay S0/S1 dataset')
    p.add_argument('--v08-dir',type=Path,required=True,help='V0.8 directory containing S4 and sensitividades')
    p.add_argument('--output',type=Path,required=True,help='New, empty V0.9 output directory')
    p.add_argument('--repo',type=Path,default=ROOT)
    p.add_argument('--max-iterations',type=int,default=3)
    p.add_argument('--max-backtracks',type=int,default=2)
    p.add_argument('--bess-trust-kw',type=float,default=300.)
    p.add_argument('--v2g-trust-kw',type=float,default=250.)
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--thermal-weight',type=float,default=40.)
    p.add_argument('--voltage-weight',type=float,default=5000.)
    p.add_argument('--peak-weight',type=float,default=.5)
    p.add_argument('--max-thermal-growth',type=float,default=.05)
    p.add_argument('--max-voltage-growth',type=float,default=.10)
    p.add_argument('--minimum-merit-drop',type=float,default=1.)
    p.add_argument('--time-limit',type=float,default=300.)
    return p


def main():
    args=make_cli().parse_args()
    results=args.results.resolve();v08dir=args.v08_dir.resolve();out=args.output.resolve();repo=args.repo.resolve()
    if out.exists() and any(out.iterdir()):raise FileExistsError('Use uma nova pasta de saída vazia para não misturar resultados')
    if not 10<=args.delta_kw<=250 or args.max_iterations<1 or args.max_backtracks<1:
        raise ValueError('Delta 10..250kW e números de iterações positivos')
    if min(args.bess_trust_kw,args.v2g_trust_kw,args.time_limit)<=0:
        raise ValueError('Trust regions e time-limit devem ser positivos')
    if min(args.max_thermal_growth,args.max_voltage_growth)<0 or args.minimum_merit_drop<=0:
        raise ValueError('Critérios AC de aceitação inválidos')
    if not results.is_dir() or not v08dir.is_dir():raise FileNotFoundError('Falta o replay S0/S1 ou a V0.8')
    if results in out.parents or v08dir in out.parents or out in (results,v08dir,repo):
        raise ValueError('Não coloque os resultados dentro da V0.8 nem do replay')
    out.mkdir(parents=True,exist_ok=True)
    try:
        ctx=load_context(repo,HERE)
        # Configuration hashes prevent mixing new optimization with old physical data.
        s,s1=v08.read_existing(results)
        team=v08.read_team(repo,results)
        reference=read_seed(v08dir,team,ctx)
        # Reconstruct the V0.8 electrical injection strictly from the CURRENT S1
        # and the saved net schedules. Reject stale/altered replays immediately.
        reconstructed=v08.build_scenario_injections(s1,team['old_fleet_profile'],team,
            {'bess_net_kw':reference['bess'],'v2g_net_kw':reference['fleet']})
        original=reference['profile']
        keys=['hour','bus','phase']; cols=['p_kw','q_kvar']
        merged=original[keys+cols].merge(reconstructed[keys+cols],on=keys,
                                          how='outer',suffixes=('_v08','_from_s1'),indicator=True)
        if (merged._merge!='both').any() or not np.allclose(merged[['p_kw_v08','q_kvar_v08']].to_numpy(),
                                                merged[['p_kw_from_s1','q_kvar_from_s1']].to_numpy(),atol=1e-4):
            raise RuntimeError('S1 atual não reproduz as injeções S4 V0.8; reveja o replay usado')
        weights={'thermal':args.thermal_weight,'voltage':args.voltage_weight,'peak':args.peak_weight}
        score=relative_score(s['S0'],reference['dfs'],weights)
        init=score.copy();dump(out/'initial_ac_score.json',init)
        legacy=None
        if all((results/'S4'/f'{name}.csv').is_file() for name in TABLES):
            legacy=relative_score(s['S0'],load_tables(results/'S4'),weights)
            dump(out/'legacy_s4_ac_score.json',legacy)
        best=out/'best';shutil.copytree(reference['seed_dir'],best)
        history=[];accepted=0
        for iteration in range(1,args.max_iterations+1):
            workspace=out/f'iteration_{iteration:02d}';workspace.mkdir()
            dl,dv,ds,replay_err=finite_difference(ctx,team,reference,workspace,args.delta_kw)
            improvement=False
            for backtrack in range(args.max_backtracks):
                factor=2**backtrack
                bt=args.bess_trust_kw/factor;vt=args.v2g_trust_kw/factor
                attempt=workspace/f'trial_{backtrack+1:02d}';attempt.mkdir()
                settings=OptSettings(thermal_weight=args.thermal_weight,
                                     undervoltage_weight=args.voltage_weight,overvoltage_weight=args.voltage_weight,
                                     peak_weight=args.peak_weight,bess_trust_kw=bt,v2g_trust_kw=vt,
                                     bess_throughput_usd_kwh=float(team['battery'].degradation_per_kwh),
                                     vehicle_degradation_usd_kwh=float(team['configs']['v2g']['cesar_v1'].get('degradation_usd_kwh',0.)),
                                     solver_time_limit_s=args.time_limit)
                row=dict(iteration=iteration,trial=backtrack+1,bess_trust_kw=bt,v2g_trust_kw=vt,
                         reference_ac_merit=score['ac_merit'],baseline_errors=replay_err)
                try:
                    solution=solve_joint(vehicles=team['vehicles'],grid=team['grid'],station=team['station'],
                                         battery=team['battery'],bess_bus=team['bess_bus'],v2g_bus=team['v2g_bus'],
                                         source_s1=reference['dfs']['source'],lines_s1=reference['dfs']['branches'],
                                         volt_s1=reference['dfs']['voltages'],lines_s0=s['S0']['branches'],
                                         volt_s0=s['S0']['voltages'],ds_lines=dl,ds_volt=dv,ds_source=ds,
                                         fleet_s1_kw=team['old_fleet_net'],fleet_s1_per_vehicle_kw=team['old_fleet'],
                                         independent_evcs_kw=team['ind'],discharge_window=team['discharge_window'],
                                         no_charge_window=team['no_charge_window'],tariffs=team['tariff'],
                                         remuneration=team['remuneration'],settings=settings,mode='S4',
                                         network_reference_bess_kw=reference['bess'],
                                         network_reference_fleet_kw=reference['fleet'])
                    save_candidate(solution,attempt,team)
                    delta_b=np.max(np.abs(solution['bess_net_kw']-reference['bess']))
                    delta_v=np.max(np.abs(solution['v2g_net_kw']-reference['fleet']))
                    row.update(max_bess_delta_kw=float(delta_b),max_v2g_delta_kw=float(delta_v))
                    if delta_b<1e-3 and delta_v<1e-3:
                        row.update(accepted=False,reason='Optimizer kept the same aggregate injections')
                        dump(attempt/'acceptance.json',row);history.append(row);break
                    injections=v08.build_scenario_injections(s1,team['old_fleet_profile'],team,solution)
                    observed=v08.simulate_solution(ctx,injections,team,s['S1'],attempt)
                    newscore=relative_score(s['S0'],observed,weights)
                    good,why=decision(score,newscore,args.max_thermal_growth,
                                      args.max_voltage_growth,args.minimum_merit_drop)
                    check=v08.score(s['S0'],s['S1'],observed)[1]
                    row.update(candidate_ac_score=newscore,ac_verification=check,accepted=good,reason=why)
                    dump(attempt/'acceptance.json',row)
                    history.append(row)
                    if good:
                        improvement=True;accepted+=1
                        reference=dict(dfs=observed,profile=injections,
                                       bess=solution['bess_net_kw'],fleet=solution['v2g_net_kw'],
                                       vehicles=solution['vehicle_power_kw'])
                        score=newscore
                        shutil.rmtree(best);shutil.copytree(attempt,best)
                        print(f'[V0.9 ACCEPTED] iteration={iteration}, trial={backtrack+1}, AC merit={score["ac_merit"]:.2f}',flush=True)
                        break
                    print(f'[V0.9 REJECTED] iteration={iteration}, trial={backtrack+1}: {why}',flush=True)
                except Exception as exc:
                    row.update(accepted=False,reason='MILP/AC exception',error=str(exc))
                    dump(attempt/'error.json',dict(error=str(exc),traceback=traceback.format_exc()))
                    history.append(row)
                    print(f'[V0.9 trial failure] {str(exc)}',flush=True)
            if not improvement:
                print('[V0.9] No AC improvement passed acceptance. Stopping without modifying best.',flush=True)
                break
        dump(out/'history.json',history)
        dump(out/'final_report.json',dict(status='AC_GUIDED_REOPTIMIZATION_COMPLETE_NOT_AUTOMATICALLY_FEASIBLE',
                                          legacy_s4=legacy,
                                          better_merit_than_legacy=(None if legacy is None else bool(score['ac_merit']<legacy['ac_merit'])),
                                          accepted_iterations=accepted,initial=init,final=score,
                                          better_than_v08=bool(accepted),
                                          note='No AC feasibility or journal suitability is asserted automatically.',
                                          benchmark='Legacy S4 must be compared independently before paper claims',
                                          created_utc=datetime.now(timezone.utc).isoformat()))
        print(f'[V0.9 COMPLETE] accepted={accepted}; best AC merit={score["ac_merit"]:.2f}; output={out}')
    except Exception as exc:
        dump(out/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()))
        raise

if __name__=='__main__':main()
