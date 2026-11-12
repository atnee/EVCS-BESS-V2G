#!/usr/bin/env python3
"""V2.9 — novo MILP de redespacho individual BESS e V2G + guardas AC.

Incumbente V2.8.1 corrigida (BT0); 150 VEs, 3 BESS, 24h e locais fixos.
Aproximacoes de rede com sensibilidades locais; validacao independente de
24h em OpenDSS com reguladores/capacitores automaticos. Elegibilidade nao
equivale a certificacao absoluta de operacao ou robustez estocastica.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import traceback
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from distributed_stochastic import validar_passo_v27 as v27
from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic import otimizar_conjunto_v281 as v281
from distributed_stochastic._milp_independente_v29 import make_milp_v29

TARGET20=('ln6350556-2','ln6350556-3','ln5835160-1')
KEY_LINE=['hour','line','phase']
KEY_VOLT=['hour','bus','phase']


def exact_key(df, keys, values, column):
    if df.duplicated(keys).any():
        raise ValueError(f'Duplicatas na tabela eletrica {keys}')
    lookup=df.set_index(keys)
    key=tuple(values)
    if key not in lookup.index:
        raise ValueError(f'Chave de rede ausente: {key}')
    value=float(lookup.loc[key,column])
    if not np.isfinite(value):
        raise ValueError(f'Valor nao finito para {key}')
    return value


def additional_milp_guards(ctx, ac, args):
    """Guardas lineares individuais para o MILP; AC completo decide elegibilidade.

    - V2.5: guardas herdadas, independente de eventuais sobrecargas de S0.
    - 20h: reduzir ligeiramente os tres eventos fase A da V2.7.
    - 18h: nao piorar as N menores tensoes fase A do incumbente.
    - 15h: reduzir a linha critica C sem ignorar os demais horarios.
    """
    g=ctx['guard_table']
    if g.empty or g.duplicated(KEY_LINE).any():
        raise ValueError('Guardas V2.5 ausentes/duplicadas')
    lines=ac['branches']
    caps={}
    for row in g.itertuples(index=False):
        key=(int(row.hour),str(row.line),str(row.phase))
        caps[key]=min(99.9,float(row.target_pct))+.02
    for line in TARGET20:
        key=(20,line,'A')
        old=exact_key(lines,KEY_LINE,key,'loading_pct')
        # Retem reducao pequena sem pedir 100% absoluto quando a rede viola S0.
        goal=old-args.target_hour20_gain_pp
        caps[key]=min(caps.get(key,float('inf')),goal)
    focus_key=(15,ctx['focus_line'],ctx['focus_phase'])
    focus_current=exact_key(lines,KEY_LINE,focus_key,'loading_pct')
    focus_target=(focus_key,focus_current-args.target_hour15_gain_pp)
    v=ac['voltages']
    sel=v[(v.hour==18)&(v.phase=='A')].nsmallest(args.protect_n_buses_18h,'v_pu')
    if len(sel)!=args.protect_n_buses_18h:
        raise ValueError('Numero insuficiente de barras na fase A em 18h')
    floors={(18,str(r.bus),'A'):float(r.v_pu)-args.protect_vmin_drop_pu
            for r in sel.itertuples(index=False)}
    return caps,floors,focus_target


def assert_scores(actual, saved, label):
    for side in ('orig','policy'):
        v27.score_close(actual[side],saved[side],label+'/'+side,atol=1e-4)
    for key in ('focus',):
        if abs(float(actual[key])-float(saved[key]))>1e-4:
            raise RuntimeError(f'Metrica do incumbente {label}/{key} inconsistente')
    if actual['ops']!=saved['ops']:
        raise RuntimeError('Operacoes de controles divergentes no incumbente')


def verify_injections(ctx,folder,bess,cars):
    state=ctx['state']
    expected=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                       bess,cars,state['places'])
    recorded=pd.read_csv(folder/'injections_reoptimized.csv')
    cols=['hour','bus','phase','p_kw','q_kvar']
    for f in (expected,recorded):
        if not set(cols).issubset(f.columns) or f.duplicated(cols[:3]).any():
            raise ValueError('Injecoes incompletas ou com chaves duplicadas')
    assert_frame_equal(expected[cols].sort_values(cols[:3]).reset_index(drop=True),
                       recorded[cols].sort_values(cols[:3]).reset_index(drop=True),
                       check_dtype=False,atol=1e-5,rtol=0)
    return expected


def prepare(args):
    p281c=Path(args.v281c_dir).resolve()
    for file in (p281c/'final_report_v281_corrigida.json',
                 p281c/'best'/'AC'/'branches.csv',
                 p281c/'best'/'report_v281_corrigida.json'):
        if not file.is_file():
            raise FileNotFoundError(file)
    ctx=v28.prepare(args)  # valida V2.5, V2.6 e V2.7 e 2 referencias S0.
    out=ctx['output']
    v28.ensure_output(out,[p281c,Path(args.v28_dir).resolve(),
                           Path(args.v281_dir).resolve()])
    if (out/'best').exists() or (out/'iter_01').exists() or (out/'final_report_v29.json').exists():
        raise FileExistsError('V2.9 ja executada nesta pasta; use outra --output')
    report=v27.read_json(p281c/'final_report_v281_corrigida.json')
    if report.get('state')!='AC_ELIGIBLE_NOT_CERTIFIED' or report.get('champion')!= 'V2.8.1_CORRIGIDA_trial_bt0':
        raise RuntimeError('Incumbente V2.8.1 corrigida nao corresponde ao BT0 aceito')
    best=p281c/'best'
    v27.assert_dispatch_identity(best,ctx['state'])
    b=ctx['v24'].read_bess(best,ctx['state'])
    cars=ctx['v24'].read_cars(best,ctx['state'])
    ctx['v24'].validate_resources(ctx['state']['team'],b,cars,ctx['state']['places'],ctx['state']['cap'])
    profiles=verify_injections(ctx,best,b,cars)
    ac=ctx['v22'].read_ac_folder(best/'AC')
    metrics=v28.qmetrics(ctx,ac)
    assert_scores(metrics,report['best_metrics'],'V2.8.1 corrigida')
    if v27.read_json(best/'control_policy_v25.json')!=ctx['policy']:
        raise RuntimeError('Politica V2.8.1 corrigida difere de V2.5')
    if metrics['orig']['new_overloaded_lines_vs_s0']!=0 or metrics['policy']['new_overloaded_lines_vs_s0']!=0:
        raise RuntimeError('Incumbente possui novas linhas contra S0')
    ctx['v281cdir']=p281c
    ctx['incumbent']={'bess':b,'cars':cars,'profiles':profiles,'ac':ac,
                      'metrics':metrics,'path':best}
    ctx['v27_metrics']=ctx['start_metrics']
    return ctx


def precheck(args,ctx):
    if not callable(getattr(ctx['v24'],'calibrate_isolated_v24',None)):
        raise RuntimeError('Falta a rotina V2.4.1 calibrate_isolated_v24 na sua instalacao')
    out=ctx['output'];out.mkdir(parents=True,exist_ok=True)
    caps,floors,focus=additional_milp_guards(ctx,ctx['incumbent']['ac'],args)
    q=ctx['incumbent']['metrics']
    info={'state':'V29_INPUT_VALIDATED','incumbent':'V2.8.1_CORRIGIDA_BT0',
       'source_dir':str(ctx['v281cdir']), 'policy':ctx['policy'],
       'bess_units':len(ctx['state']['cap']),'vehicles':len(ctx['incumbent']['cars']),
       'hourly_BESS_variables':len(ctx['state']['cap'])*24,
       'hourly_EV_variables':len(ctx['incumbent']['cars'])*24,
       'milp_line_guards':len(caps),'milp_voltage_guards':len(floors),
       'incumbent_metrics':q, 'focus_15h_loading_pct':focus[1]+args.target_hour15_gain_pp,
       'reference_v27_metrics':ctx['v27_metrics'],
       'method':'Full individual BESS/EV MILP; linearized local AC constraints; 24-hour AC validation',
       'note':'A restricao MILP de rede e aproximada. Somente AC completo determina elegibilidade.'}
    v27.write_json(out/'precheck_v29.json',info)
    print('[V2.9] Precheck OK: V2.8.1 corrigida reproduzida, 3 BESS e 150 VEs.',flush=True)
    print(f'[V2.9] Guardas previstas: {len(caps)} linhas-fase-hora, {len(floors)} barras fase A 18h.',flush=True)


def validate_model_solution(ctx,sol,folder):
    state=ctx['state'];v24=ctx['v24'];v22=ctx['v22']
    v24.validate_resources(state['team'],sol['bess'],sol['cars'],state['places'],state['cap'])
    prof=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                  sol['bess'],sol['cars'],state['places'])
    folder.mkdir(parents=True,exist_ok=False)
    v22.write_dispatch(folder,sol['bess'],sol['cars'],state['team']['vehicles'],
                       state['places'],state['cap'])
    prof.to_csv(folder/'injections_reoptimized.csv',index=False)
    return prof


def run(args,ctx):
    out=ctx['output']
    incumbent=ctx['incumbent'];current=incumbent
    v27.write_json(out/'run_manifest_v29.json',vars(args))
    shutil.copytree(ctx['v281cdir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v29.json',{
        'source':'V2.8.1_CORRIGIDA_BT0_UNCHANGED','absolute_certification':False})
    accepted=0;rows=[];summaries=[]
    for it in range(1,args.max_iterations+1):
        idir=out/f'iter_{it:02d}'
        idir.mkdir(parents=True,exist_ok=False)
        print(f'[V2.9] Iteracao {it}: calibrando sensitividades em 24h sob politica C...',flush=True)
        try:
            with ctx['v24'].temporary_control_policy(ctx['inv'],ctx['policy']):
                slopes=ctx['v24'].calibrate_isolated_v24(
                    ctx['repo'],current['profiles'],current['ac'],ctx['s0_policy'],
                    ctx['state']['sites'],args.delta_kw,idir/'calibration')
            caps,floors,focus=additional_milp_guards(ctx,current['ac'],args)
            # Falhar cedo se qualquer guarda nao estiver na malha de derivadas.
            absent=[key for key in caps if key not in slopes['lines']]
            absent+= [key for key in floors if key not in slopes['volts']]
            if focus[0] not in slopes['lines']:absent.append(focus[0])
            if absent:raise RuntimeError(f'Guardas sem sensibilidades: {absent[:8]}')
            v27.write_json(idir/'calibration_summary_v29.json',{
                'n_line_keys':len(slopes['lines']),'n_volt_keys':len(slopes['volts']),
                'n_line_guards':len(caps),'n_voltage_guards':len(floors),
                'focus':list(focus[0]), 'delta_kw':args.delta_kw})
        except Exception as exc:
            summaries.append({'iteration':it,'state':'CALIBRATION_FAILED','error':str(exc)})
            v27.write_json(idir/'error_calibration_v29.json',{'error':str(exc),'traceback':traceback.format_exc()})
            print(f'[V2.9] Calibracao falhou: {exc}',flush=True)
            break
        found=False
        for bt in range(args.backtracks+1):
            folder=idir/f'trial_bt{bt}'
            rb=args.bess_trust_kw*(0.5**bt)
            rv=args.v2g_trust_kw*(0.5**bt)
            record={'iteration':it,'backtrack':bt,'trust_bess_kw':rb,'trust_v2g_kw':rv}
            print(f'[V2.9] MILP it={it} bt={bt}: BESS={rb:g} kW, V2G={rv:g} kW...',flush=True)
            try:
                sol=make_milp_v29(ctx['state']['team'],ctx['state']['places'],ctx['state']['cap'],
                    current['bess'],current['cars'],current['ac'],ctx['s0_policy'],slopes,
                    criterion='relative',trust_bess=rb,trust_v2g=rv,
                    time_limit=args.time_limit_s,mip_gap=args.mip_gap,
                    hard_line_caps=caps,protected_voltage=floors,
                    focus_target=focus,peak_growth_kw=args.local_peak_growth_kw)
                prof=validate_model_solution(ctx,sol,folder)
                ac=ctx['v24'].evaluate(ctx['repo'],prof,ctx['state']['sites'],
                                      ctx['inv'],ctx['policy'],folder/'AC')
                metrics=v28.qmetrics(ctx,ac)
                yes,reasons,audit=v281.dual_voltage_budget_gate(
                    ctx,ac,metrics,current,args)
                guard=v27.guard_values(ctx['guard_table'],ac['branches'])
                guard.to_csv(folder/'guardas_v29_ac.csv',index=False)
                record.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if yes else 'AC_REJECTED',
                    reasons=reasons,ac=metrics,
                    solver={'status':sol['solver_status'],'message':sol['solver_message'],
                       'gap':sol['mip_gap'],'objective':sol['objective'],
                       'slack_sum':sol['slack_sum'],'model_stats':sol['model_stats']},
                    gate_audit=audit)
                v27.write_json(folder/'report_v29.json',record)
                print(f'[V2.9] AC: T={metrics["policy"]["thermal_aggravation_pp_h"]:.4f} pp.h; '
                      f'V={metrics["policy"]["voltage_aggravation_pu_h"]:.5f} pu.h; '
                      f'novas={metrics["orig"]["new_overloaded_lines_vs_s0"]}; '
                      f'aceito={yes}',flush=True)
                if yes:
                    found=True;accepted+=1
                    current={'bess':sol['bess'],'cars':sol['cars'],'profiles':prof,
                             'ac':ac,'metrics':metrics,'path':folder}
                    shutil.rmtree(out/'best')
                    shutil.copytree(folder,out/'best')
                    v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                    v27.write_json(out/'best'/'ac_score_vs_original_s0.json',metrics['orig'])
                    v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',metrics['policy'])
                    v27.write_json(out/'best'/'provenance_v29.json',{
                        'source':str(folder),'champion':'V2.9_AC_ELIGIBLE',
                        'absolute_certification':False})
            except Exception as exc:
                record.update(status='ERROR_OR_MODEL_INFEASIBLE',error=str(exc))
                folder.mkdir(parents=True,exist_ok=True)
                v27.write_json(folder/'error_v29.json',{
                    'error':str(exc),'traceback':traceback.format_exc()})
                print(f'[V2.9] Tentativa falhou: {exc}',flush=True)
            rows.append(record)
            if found:break
        summaries.append({'iteration':it,'state':'AC_ACCEPTED' if found else 'NO_AC_ACCEPTED',
                          'attempts':len([r for r in rows if r['iteration']==it])})
        if not found:break
    pd.DataFrame([{
         'iteration':r['iteration'],'backtrack':r['backtrack'],'status':r['status'],
         'reasons':';'.join(r.get('reasons',[])) or r.get('error',''),
         'thermal_policy_pph':r.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
         'voltage_policy_puh':r.get('ac',{}).get('policy',{}).get('voltage_aggravation_pu_h')
         } for r in rows],columns=['iteration','backtrack','status','reasons',
                          'thermal_policy_pph','voltage_policy_puh']).to_csv(
                              out/'todos_candidatos_ac_v29.csv',index=False)
    report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else 'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V29',
        'champion':'V2.9' if accepted else 'V2.8.1_CORRIGIDA_BT0',
        'accepted_iterations':accepted,'attempts':len(rows),'steps':summaries,
        'reference_v281c':incumbent['metrics'],'best_metrics':current['metrics'],
        'reference_v27':ctx['v27_metrics'],'policy':ctx['policy'],
        'scientific_scope':'Individual BESS/EV MILP, localized AC-linear constraints, 24h OpenDSS postsolve',
        'absolute_certification':False,
        'limitations':['Modelo local AC nao garante previsao exata de controles discretos',
                       'Nao certifica N-1, transformadores, protecao, harmônicos ou robustez estocastica']}
    v27.write_json(out/'final_report_v29.json',report)
    print(f'[V2.9] Final: {report["state"]}; aceitos={accepted}; '
          f'campeao={report["champion"]}',flush=True)
    print('[V2.9] Relatorio:',out/'final_report_v29.json',flush=True)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','v22-dir','v23-dir','v24-dir','v25-dir','v26-dir',
                 'v27-dir','v28-dir','v281-dir','v281c-dir'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--repo',default='.')
    p.add_argument('--output',default='./results/v29_milp_ac')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--candidates',default='soft,anchors3')
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--bess-trust-kw',type=float,default=100.)
    p.add_argument('--v2g-trust-kw',type=float,default=60.)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    p.add_argument('--max-iterations',type=int,default=1)
    p.add_argument('--backtracks',type=int,default=1)
    p.add_argument('--protect-n-buses-18h',type=int,default=8)
    p.add_argument('--protect-vmin-drop-pu',type=float,default=.00015)
    p.add_argument('--target-hour15-gain-pp',type=float,default=.01)
    p.add_argument('--target-hour20-gain-pp',type=float,default=.003)
    # Guardas de aceitacao da V2.8.1 corrigida, separadas do surrogate MILP.
    p.add_argument('--historical-voltage-budget-puh',type=float,default=.15)
    p.add_argument('--voltage-budget-puh',type=float,default=.03)
    p.add_argument('--min-thermal-gain',type=float,default=.01)
    p.add_argument('--min-focus-gain-pph',type=float,default=.5)
    p.add_argument('--min-actual-thermal-gain-pph',type=float,default=.05)
    p.add_argument('--min-actual-focus-gain-pph',type=float,default=.02)
    p.add_argument('--peak-allowance-kw',type=float,default=60.)
    p.add_argument('--local-peak-growth-kw',type=float,default=50.)
    p.add_argument('--vmin-drop-pu',type=float,default=.0007)
    p.add_argument('--max-loading-growth-pp',type=float,default=.12)
    p.add_argument('--hour15-max-worsening-pp',type=float,default=.10)
    p.add_argument('--hour20-max-worsening-pp',type=float,default=.02)
    p.add_argument('--hour18-vmin-drop-pu',type=float,default=.0003)
    p.add_argument('--hour18-under-budget-puh',type=float,default=.015)
    p.add_argument('--max-new-voltage-samples',type=int,default=20)
    p.add_argument('--max-new-thermal-samples',type=int,default=4)
    p.add_argument('--max-added-voltage-samples',type=int,default=5)
    p.add_argument('--max-added-overload-samples',type=int,default=0)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    positive=(args.delta_kw,args.bess_trust_kw,args.v2g_trust_kw,args.time_limit_s)
    if (any(not np.isfinite(z) or z<=0 for z in positive) or
        not 0<args.mip_gap<1 or not 1<=args.max_iterations<=3 or
        not 0<=args.backtracks<=2 or not 1<=args.protect_n_buses_18h<=40 or
        any(not np.isfinite(z) or z<0 for z in (
           args.target_hour15_gain_pp,args.target_hour20_gain_pp,args.protect_vmin_drop_pu,
           args.historical_voltage_budget_puh,args.voltage_budget_puh)) or
        args.voltage_budget_puh>args.historical_voltage_budget_puh):
        p.error('Parametros invalidos')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.9] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
