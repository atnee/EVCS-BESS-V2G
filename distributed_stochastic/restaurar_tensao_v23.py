#!/usr/bin/env python3
"""V2.3 — restauração de viabilidade AC priorizando SUB/SOBRETENSÃO.

Retoma o best/ da V2.2, mantém as localizações e capacidades BESS–V2G,
reotimiza 3 BESS e 150 veículos com sensibilidades calculadas no OpenDSS,
e só aceita propostas após replay AC cronológico completo de 24 horas.

Não otimiza tap de regulador nem capacidade de inversor reativo (sem rating validado).
Não garante viabilidade elétrica absoluta se o próprio S0 viola limites.
Convenção: +kW injeta na rede; -kW consome da rede.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import shutil
import sys
import traceback
import numpy as np
import pandas as pd
from distributed_stochastic import restaurar_viabilidade_v22 as v22
from distributed_stochastic._milp_tensao_v23 import make_milp


PROFILES = {
    'under': {'under':85000.,'over':40000.,'thermal':160.,'peak':.003,'worst_under':70000.,'worst_over':12000.},
    'balanced': {'under':65000.,'over':65000.,'thermal':170.,'peak':.003,'worst_under':45000.,'worst_over':45000.},
    'over': {'under':40000.,'over':90000.,'thermal':160.,'peak':.003,'worst_under':12000.,'worst_over':80000.},
}


def audit_components(s0,ac):
    v=s0['voltages'][v22.KEY_V+['v_pu']].merge(
      ac['voltages'][v22.KEY_V+['v_pu']],on=v22.KEY_V,
      suffixes=('_s0','_ac'),how='outer',indicator=True,validate='one_to_one')
    if (v._merge!='both').any() or v[v22.KEY_V].isna().any().any():
        raise RuntimeError('Conjuntos AC/S0 de barras, fases ou horas divergentes')
    if not np.isfinite(v[['v_pu_s0','v_pu_ac']].to_numpy()).all():
        raise RuntimeError('Tensão não finita')
    s0v=v.v_pu_s0.to_numpy(float);av=v.v_pu_ac.to_numpy(float)
    v['under_rel_pu']=np.maximum(0.,np.minimum(.95,s0v)-av)
    v['over_rel_pu']=np.maximum(0.,av-np.maximum(1.05,s0v))
    v['under_abs_pu']=np.maximum(0.,.95-av)
    v['over_abs_pu']=np.maximum(0.,av-1.05)
    u=float(v.under_rel_pu.sum());o=float(v.over_rel_pu.sum())
    return {'under_rel_puh':u,'over_rel_puh':o,'voltage_rel_puh':u+o,
      'under_abs_puh':float(v.under_abs_pu.sum()),'over_abs_puh':float(v.over_abs_pu.sum()),
      'under_rel_samples':int((v.under_rel_pu>1e-6).sum()),
      'over_rel_samples':int((v.over_rel_pu>1e-6).sum()),
      'under_abs_samples':int((v.under_abs_pu>1e-6).sum()),
      'over_abs_samples':int((v.over_abs_pu>1e-6).sum())},v


def rank_voltage(s0,ac,path):
    out,v=audit_components(s0,ac)
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    v['relative_severity_pu']=v.under_rel_pu+v.over_rel_pu
    v['absolute_severity_pu']=v.under_abs_pu+v.over_abs_pu
    v.sort_values(['relative_severity_pu','absolute_severity_pu'],ascending=False).head(300).drop(columns=['_merge']).to_csv(path/'tensoes_criticas_v23.csv',index=False)
    hourly=v.groupby('hour',as_index=False).agg(
       under_relative_puh=('under_rel_pu','sum'),over_relative_puh=('over_rel_pu','sum'),
       under_absolute_puh=('under_abs_pu','sum'),over_absolute_puh=('over_abs_pu','sum'),
       minimum_voltage=('v_pu_ac','min'),maximum_voltage=('v_pu_ac','max'))
    hourly.to_csv(path/'tensoes_por_hora_v23.csv',index=False)
    v[(v.relative_severity_pu>1e-6)].drop(columns=['_merge']).to_csv(path/'agravamentos_tensao_v23.csv.gz',index=False,compression='gzip')
    v22.as_json(path/'componentes_tensao_v23.json',out)
    return out


def tap_differences(ref,ac,path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    r=ref['regulators'][['hour','regulator','tap']].merge(
      ac['regulators'][['hour','regulator','tap']],on=['hour','regulator'],
      how='outer',indicator=True,validate='one_to_one',suffixes=('_v22','_v23'))
    if (r._merge!='both').any():
        raise RuntimeError('Reguladores diferentes na comparação AC')
    r['delta_tap']=r.tap_v23-r.tap_v22
    r.drop(columns=['_merge']).to_csv(path/'taps_comparados_v23.csv',index=False)
    c=ref['capacitors'][['hour','capacitor','states']].merge(
      ac['capacitors'][['hour','capacitor','states']],on=['hour','capacitor'],
      how='outer',indicator=True,validate='one_to_one',suffixes=('_v22','_v23'))
    if (c._merge!='both').any():
        raise RuntimeError('Capacitores diferentes na comparação AC')
    changes=int((c.states_v22!=c.states_v23).sum())
    return {'tap_changes':int((r.delta_tap!=0).sum()),
            'max_absolute_tap_change':int(r.delta_tap.abs().max()),
            'capacitor_state_changes':changes}


def acceptance(previous,attempt,pc,cc,*,max_thermal_growth=.08,max_component_growth=.15,min_voltage_improve=.003):
    """Aceita desempenho AC real sem reabrir linhas novas ou perder ganho térmico.

    Uma tentativa estritamente ou relativamente viável tem prioridade sobre o
    desempenho agregado. Caso contrário, exigimos melhora REAL de tensão.
    """
    if attempt['strict_pass'] and not previous['strict_pass']:
        return True,'absolute_AC_feasibility'
    if attempt['relative_s0_pass'] and not previous['relative_s0_pass']:
        return True,'relative_S0_AC_feasibility'
    if attempt['new_overloaded_lines_vs_s0']>previous['new_overloaded_lines_vs_s0']:
        return False,'reopened_distinct_overloaded_lines'
    if attempt['thermal_aggravation_pp_h']>previous['thermal_aggravation_pp_h']*(1+max_thermal_growth)+.01:
        return False,'thermal_severity_growth_exceeds_guard'
    # Proteção de tensão: não transformar melhora de subtensão em sobretensão extrema.
    for name in ('under_rel_puh','over_rel_puh'):
        a=pc[name];b=cc[name]
        if b>a*(1+max_component_growth)+.01:
            return False,f'{name}_worsened_beyond_guard'
    if cc['voltage_rel_puh']<pc['voltage_rel_puh']*(1-min_voltage_improve)-1e-5:
        return True,'AC_voltage_severity_improved_with_thermal_guard'
    return False,'AC_voltage_improvement_insufficient'


def read_incumbent(repo,baseline,v22_dir):
    sys.path.insert(0,str(repo/'opendss_rebuild'))
    sys.path.insert(0,str(repo/'src'))
    sys.path.insert(0,str(repo))
    from reotimizar_bess_v2g import read_existing, read_team
    from distributed_stochastic.otimizar_siting_v21 import build_injections
    refs,s1=read_existing(baseline)
    team=read_team(repo,baseline)
    v22_best=v22_dir/'best'
    assign=pd.read_csv(v22_dir/'vehicle_site_assignment.csv')
    if assign.vehicle_id.duplicated().any():raise ValueError('Veiculo duplicado na alocacao V2.2')
    id2site=dict(zip(assign.vehicle_id.astype(str),assign.site.astype(str)))
    ids=[str(v.id) for v in team['vehicles']]
    if len(ids)!=150 or set(ids)!=set(id2site):raise ValueError('A V2.3 requer todos os 150 veiculos da V2.2')
    places=[id2site[x] for x in ids]
    vdf=pd.read_csv(v22_best/'vehicle_dispatch.csv')
    if vdf.duplicated(['vehicle_id','hour']).any() or vdf.shape[0]!=150*24:
        raise ValueError('Despacho V2.2 de veiculos duplicado/incompleto')
    if (vdf.set_index('vehicle_id')['site'].groupby(level=0).nunique()!=1).any():
        raise ValueError('Veiculo associado a mais de uma estacao')
    site_by_vehicle=vdf.groupby('vehicle_id').site.first().to_dict()
    if any(str(site_by_vehicle.get(x))!=place for x,place in zip(ids,places)):
        raise ValueError('Veiculo e estacao nao coincidem no despacho e na alocacao')
    matrix=vdf.pivot(index='vehicle_id',columns='hour',values='p_kw').reindex(index=ids,columns=range(24))
    if matrix.isna().any().any():raise ValueError('Lacuna no despacho individual V2.2')
    cars=matrix.to_numpy(float)
    sizing=pd.read_csv(v22_best/'bess_siting_sizing.csv')
    if sizing.site.duplicated().any() or len(sizing)!=3:
        raise ValueError('Siting de BESS V2.2 incompleto')
    cap={str(r.site):{'power_kw':float(r.power_kw),'energy_kwh':float(r.energy_kwh)} for r in sizing.itertuples()}
    if abs(sum(x['power_kw'] for x in cap.values())-2200.)>1e-3 or abs(sum(x['energy_kwh'] for x in cap.values())-7200.)>1e-3:
        raise ValueError('Capacidade total BESS V2.2 divergente')
    bdf=pd.read_csv(v22_best/'bess_dispatch.csv')
    if bdf.duplicated(['site','hour']).any() or bdf.shape[0]!=len(cap)*24:
        raise ValueError('Despacho BESS V2.2 incompleto')
    bess={s:bdf[bdf.site==s].set_index('hour').reindex(range(24)).p_kw.to_numpy(float) for s in cap}
    if any(not np.isfinite(arr).all() for arr in list(bess.values())+[cars]):raise ValueError('Despacho nao finito')
    # Verify the V2.2 dispatch and OpenDSS injection profile use the same signed convention.
    profiles=build_injections(s1,team['old_fleet_profile'],bess,cars,places)
    saved=pd.read_csv(v22_best/'injections_reoptimized.csv')
    keys=['hour','bus','phase'];cols=['p_kw','q_kvar']
    j=profiles[keys+cols].merge(saved[keys+cols],on=keys,how='outer',indicator=True,
      validate='one_to_one',suffixes=('_reconstructed','_saved'))
    if (j._merge!='both').any() or any(float((j[f'{col}_reconstructed']-j[f'{col}_saved']).abs().max())>1e-3 for col in cols):
        raise RuntimeError('Falha no replay das injecoes V2.2: risco de duplicar V2G')
    ac=v22.read_ac_folder(v22_best/'AC')
    previous=v22.score_ac(refs['S0'],ac)
    previous_file=json.loads((v22_best/'ac_score.json').read_text(encoding='utf-8'))
    for key in ('new_overloaded_lines_vs_s0','thermal_aggravation_samples','voltage_aggravation_samples'):
        if previous[key]!=previous_file[key]:
            raise RuntimeError(f'Diferenca nos indicadores do V2.2 anterior ({key})')
    site_caps={s:float(x['power_kw']) for s,x in cap.items()}
    for s in set(places):site_caps[s]=max(site_caps.get(s,0.),11*places.count(s))
    return {'refs':refs,'s1':s1,'team':team,'assign':assign,'cars':cars,'bess':bess,
      'places':places,'cap':cap,'ac':ac,'score':previous,'profiles':profiles,'sites':site_caps,
      'build_injections':build_injections}


def write_best(out,state):
    best=out/'best';best.mkdir(parents=True,exist_ok=True)
    v22.write_dispatch(best,state['bess'],state['cars'],state['team']['vehicles'],state['places'],state['cap'])
    state['profiles'].to_csv(best/'injections_reoptimized.csv',index=False)
    acdir=best/'AC';acdir.mkdir(parents=True,exist_ok=True)
    for k,table in state['ac'].items():table.to_csv(acdir/(k+'.csv'),index=False)
    v22.as_json(best/'ac_score.json',state['score'])
    rank_voltage(state['refs']['S0'],state['ac'],best/'diagnostics')


def pareto_table(out,attempts,original):
    rows=[original]
    for row in attempts:
        if row.get('state') in ('AC_ACCEPTED','AC_REJECTED'):
            rows.append(row)
    dimensions=('thermal_aggravation_pp_h','voltage_rel_puh','source_peak_kw')
    good=[]
    for i,a in enumerate(rows):
        dominated=False
        for j,b in enumerate(rows):
            if i==j:continue
            if all(b[k]<=a[k]+1e-9 for k in dimensions) and any(b[k]<a[k]-1e-7 for k in dimensions):
                dominated=True;break
        good.append({**a,'non_dominated':not dominated})
    pd.DataFrame(good).to_csv(out/'comparacao_pareto_v23.csv',index=False)
    return int(sum(x['non_dominated'] for x in good))


def run(args):
    repo=Path(args.repo).resolve();baseline=Path(args.baseline).resolve()
    v22dir=Path(args.v22_dir).resolve();out=Path(args.output).resolve()
    if out==v22dir or str(out).startswith(str(v22dir)+str(Path('/'))):
        raise ValueError('A pasta de saida V2.3 nao pode estar dentro da V2.2')
    state=read_incumbent(repo,baseline,v22dir)
    initial=state['score'];cs,vdf=audit_components(state['refs']['S0'],state['ac'])
    out.mkdir(parents=True,exist_ok=True)
    v22.as_json(out/'precheck_v23.json',{'state':'INPUTS_VALIDATED','bess_sites':sorted(state['cap']),
        'v2g_sites':sorted(set(state['places'])),'vehicles':len(state['cars']),
        'injections_exact_reconstruction':True,'v22_ac_score':initial,'voltage_components_v22':cs,
        'AC_absolute_feasible':initial['strict_pass'],'AC_relative_S0_feasible':initial['relative_s0_pass']})
    print('[V2.3] Entradas OK; 3 BESS, 150 veiculos, 24 h; V2.2 validada como referencia.',flush=True)
    if args.validate_only:return
    write_best(out,state)
    v22.as_json(out/'baseline_v22_score.json',initial)
    accepted=0;history=[];inc_components=cs;incumbent=initial
    original={'name':'v22_input','state':'BASELINE_AC','thermal_aggravation_pp_h':initial['thermal_aggravation_pp_h'],
              'voltage_rel_puh':cs['voltage_rel_puh'],'source_peak_kw':initial['source_peak_kw']}
    for iteration in range(1,args.max_iterations+1):
        folder=out/f'iter_{iteration:02d}'
        print(f'\n[V2.3] Iteracao {iteration}: sensibilidades OpenDSS e diagnostico de tensao',flush=True)
        slopes=v22.calibrate(repo,state['profiles'],state['ac'],state['refs']['S0'],state['sites'],args.delta_kw,folder/'calibration')
        any_accepted=False
        for profile in args.profiles.split(','):
            for bt in range(args.backtracks+1):
                trustB=args.bess_trust_kw*(.5**bt);trustV=args.v2g_trust_kw*(.5**bt)
                trial=folder/f'{profile}_bt{bt:02d}'
                trial.mkdir(parents=True,exist_ok=True)
                try:
                    sol=make_milp(state['team'],state['places'],state['cap'],state['bess'],state['cars'],
                      state['ac'],state['refs']['S0'],slopes,criterion=args.criterion,
                      trust_bess=trustB,trust_v2g=trustV,time_limit=args.time_limit_s,
                      mip_gap=args.mip_gap,voltage_weights=PROFILES[profile],
                      thermal_budget=(float(incumbent['thermal_aggravation_pp_h'])*(1+args.linear_thermal_budget_pct)+args.linear_thermal_extra))
                    v22.write_dispatch(trial,sol['bess'],sol['cars'],state['team']['vehicles'],state['places'],state['cap'])
                    candidate_frame=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],sol['bess'],sol['cars'],state['places'])
                    candidate_frame.to_csv(trial/'injections_reoptimized.csv',index=False)
                    ac=v22.run_ac(repo,candidate_frame,state['sites'],trial/'AC')
                    quality=v22.score_ac(state['refs']['S0'],ac)
                    components=rank_voltage(state['refs']['S0'],ac,trial/'diagnostics')
                    taps=tap_differences(state['ac'],ac,trial/'diagnostics')
                    ok,why=acceptance(incumbent,quality,inc_components,components,
                      max_thermal_growth=args.max_thermal_growth_pct,
                      max_component_growth=args.max_component_growth_pct,
                      min_voltage_improve=args.min_voltage_improve_pct)
                    report={'state':'AC_ACCEPTED' if ok else 'AC_REJECTED','reason':why,
                        'ac_score':quality,'voltage_components':components,'controls':taps,
                        'profile':profile,'backtrack':bt,'iteration':iteration,
                        'milp':{k:sol[k] for k in ('solver_status','solver_message','mip_gap','objective','model_stats',
                           'slack_sum','worst_under_pred_pu','worst_over_pred_pu','thermal_budget')},
                        'trust_bess_kw':trustB,'trust_v2g_kw':trustV}
                    v22.as_json(trial/'report_v23.json',report)
                    history.append({'name':f'i{iteration}_{profile}_bt{bt}','state':report['state'],
                       'reason':why,'thermal_aggravation_pp_h':quality['thermal_aggravation_pp_h'],
                       'voltage_rel_puh':components['voltage_rel_puh'],
                       'under_rel_puh':components['under_rel_puh'],
                       'over_rel_puh':components['over_rel_puh'],
                       'source_peak_kw':quality['source_peak_kw'],
                       'new_overloaded_lines_vs_s0':quality['new_overloaded_lines_vs_s0'],
                       'strict_pass':quality['strict_pass'],'relative_s0_pass':quality['relative_s0_pass'],
                       'tap_changes':taps['tap_changes'],'milp_gap':sol['mip_gap']})
                    print(f' [V2.3] {profile}/bt{bt} -> AC tensao {components["voltage_rel_puh"]:.5f} p.u.-h, '
                          f'termica {quality["thermal_aggravation_pp_h"]:.2f} p.p.-h, '
                          f'novas linhas={quality["new_overloaded_lines_vs_s0"]}, aceite={ok}: {why}',flush=True)
                    if ok:
                        state.update({'bess':sol['bess'],'cars':sol['cars'],'profiles':candidate_frame,
                                      'ac':ac,'score':quality})
                        write_best(out,state)
                        incumbent=quality;inc_components=components;accepted+=1;any_accepted=True
                        break
                except Exception as e:
                    history.append({'name':f'i{iteration}_{profile}_bt{bt}','state':'ERROR','error':str(e)})
                    v22.as_json(trial/'error_v23.json',{'error':str(e),'traceback':traceback.format_exc()})
                    print(f' [V2.3] {profile}/bt{bt} FALHOU: {e}',flush=True)
            if any_accepted:break
        if not any_accepted or incumbent['strict_pass'] or (args.stop_relative and incumbent['relative_s0_pass']):
            break
    pd.DataFrame(history).to_csv(out/'historico_v23.csv',index=False)
    pf=pareto_table(out,history,original)
    final={'status':'ABSOLUTE_AC_PASS' if incumbent['strict_pass'] else (
            'RELATIVE_S0_AC_PASS_NOT_ABSOLUTE' if incumbent['relative_s0_pass'] else 'AC_VIOLATIONS_REMAIN'),
      'initial_v22':initial,'best_v23':incumbent,
      'initial_voltage_components':cs,'best_voltage_components':inc_components,
      'accepted_trials':accepted,'attempts':history,'non_dominated_total':pf,
      'warnings':['MILP soft slacks are NOT electrical feasibility',
        'OpenDSS replay covers MV monitored bars/lines, not transformer/conductor protection/N-1',
        'Regulators and capacitors automatic in replay, NOT optimised as discrete controls',
        'Locations and port capacities fixed from V2.1; grid reinforcement may be necessary',
        'No out-of-sample stochastic test yet']}
    v22.as_json(out/'final_report_v23.json',final)
    print('\n[V2.3] STATUS:',final['status'],'| tentativas aceitas:',accepted,flush=True)
    print('Relatorio:',out/'final_report_v23.json',flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v22-dir',required=True)
    p.add_argument('--output',default='results/v23_tensao')
    p.add_argument('--validate-only',action='store_true')
    p.add_argument('--criterion',choices=('relative','strict'),default='relative')
    p.add_argument('--profiles',default='under,balanced',help='Perfis separados por virgula: under,balanced,over')
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--max-iterations',type=int,default=1)
    p.add_argument('--backtracks',type=int,default=0)
    p.add_argument('--bess-trust-kw',type=float,default=180.)
    p.add_argument('--v2g-trust-kw',type=float,default=120.)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    p.add_argument('--max-thermal-growth-pct',type=float,default=.08)
    p.add_argument('--max-component-growth-pct',type=float,default=.15)
    p.add_argument('--min-voltage-improve-pct',type=float,default=.003)
    p.add_argument('--linear-thermal-budget-pct',type=float,default=.20)
    p.add_argument('--linear-thermal-extra',type=float,default=10.)
    p.add_argument('--stop-relative',action='store_true')
    args=p.parse_args(argv)
    try:
        profiles=[x.strip() for x in args.profiles.split(',')]
        if not profiles or any(x not in PROFILES for x in profiles) or len(profiles)!=len(set(profiles)):
            p.error('Perfis devem ser distintos e pertencer a: '+', '.join(PROFILES))
        args.profiles=','.join(profiles)
        if args.delta_kw<=0 or args.max_iterations<1 or args.backtracks<0 or args.bess_trust_kw<=0 or args.v2g_trust_kw<=0 or args.time_limit_s<=0 or not 0<args.mip_gap<1:
            p.error('Parametros numericos invalidos')
        if any(getattr(args,name)<0 for name in ('max_thermal_growth_pct','max_component_growth_pct','min_voltage_improve_pct','linear_thermal_budget_pct','linear_thermal_extra')):
            p.error('Guardas numericas negativas')
    except ValueError as e:p.error(str(e))
    run(args)

if __name__=='__main__':main()
