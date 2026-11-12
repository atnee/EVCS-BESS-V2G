#!/usr/bin/env python3
"""V2.9.2: cortes AC bidirecionais de tensao, ablação de inviabilidade e relinearizacao.

Mantem como incumbente a BT0 V2.8.1 corrigida. Usa o MILP individual
V2.9 com envelope absoluto V2.9.1; elegibilidade somente por replay AC 24h.
As ablações removem apenas cortes ADICIONAIS aprendidos, jamais os gates AC.
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

from distributed_stochastic import redespachar_milp_v291 as v291
from distributed_stochastic import redespachar_milp_v29 as v29
from distributed_stochastic import validar_passo_v27 as v27
from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic._milp_independente_v292 import make_milp_v292

KEY_L=v291.KEY_LINE
KEY_V=v291.KEY_VOLT


def indexed(frame, keys, value):
    if not set(keys).issubset(frame.columns) or value not in frame:
        raise ValueError(f'Tabela AC sem colunas: {keys} / {value}')
    if frame.duplicated(list(keys)).any():
        raise ValueError(f'Chaves AC repetidas em {value}')
    result=frame.set_index(list(keys))[value]
    if not np.isfinite(result.to_numpy(float)).all():
        raise ValueError(f'Valores nao finitos em {value}')
    return result


def classify_voltage(base, key, drop_pu):
    """Protege o estado inicial sem exigir eliminar violacoes preexistentes."""
    old=float(base[key])
    if old<.95:
        return 'floor', old-drop_pu
    if old>1.05:
        return 'ceiling', old+drop_pu
    # evitar que amostra anteriormente boa atravesse os limites
    return ('floor',.95-drop_pu) if old<=1.0 else ('ceiling',1.05+drop_pu)


def voltage_guards(incumbent_ac, slopes, args, priority_keys=()):
    base=indexed(incumbent_ac['voltages'],KEY_V,'v_pu')
    pool=[]
    for key in slopes['volts']:
        if key not in base:raise ValueError(f'Sensibilidade sem barra AC: {key}')
        value=float(base[key]);distance=min(abs(value-.95),abs(value-1.05))
        if distance<=args.near_voltage_margin_pu or value<.95 or value>1.05:
            pool.append((distance,key))
    forced=set(priority_keys)
    missing=forced-set(slopes['volts'])
    if missing:raise ValueError(f'Violacoes sem sensibilidade: {sorted(missing)[:5]}')
    # Determinismo: inclui sempre as chaves obrigatorias, mesmo com limite N.
    chosen=list(sorted(forced))
    for _,key in sorted(pool,key=lambda x:(x[0],x[1])):
        if key not in forced and len(chosen)<args.max_dynamic_voltage_keys:
            chosen.append(key)
    floors={};ceilings={}
    for key in chosen:
        kind,goal=classify_voltage(base,key,args.dynamic_voltage_drop_pu)
        if kind=='floor':floors[key]=goal
        else:ceilings[key]=goal
    return floors,ceilings,{'selected':len(chosen),'floors':len(floors),
                             'ceilings':len(ceilings),'forced':len(forced)}


def changed_taps(base_ac,candidate_ac):
    left=indexed(base_ac['regulators'],('hour','regulator'),'tap')
    right=indexed(candidate_ac['regulators'],('hour','regulator'),'tap')
    if set(left.index)!=set(right.index):raise ValueError('Reguladores AC divergentes')
    x=(left.sort_index()!=right.sort_index())
    return int(x.sum())


def full_ac_cuts(ctx,incumbent,proposal,ac,slopes,args):
    """Aprende com subtensao, SOBRETENSAO e sobrecarga em qualquer hora/fase.

    A severidade da previsao de primeira ordem e comparada ao AC real, nao ao
    valor reportado pelo solver. Inclui transicoes novas e pioras preexistentes.
    """
    changes=v291.diff_injections(ctx,incumbent,proposal)
    bline=indexed(incumbent['ac']['branches'],KEY_L,'loading_pct')
    aline=indexed(ac['branches'],KEY_L,'loading_pct')
    bvolt=indexed(incumbent['ac']['voltages'],KEY_V,'v_pu')
    avolt=indexed(ac['voltages'],KEY_V,'v_pu')
    if set(bline.index)!=set(aline.index) or set(bvolt.index)!=set(avolt.index):
        raise ValueError('Mudanca nas chaves fisicas do replay AC')
    line=[];under=[];over=[];missing=[]
    for key in bline.index:
        old=float(bline[key]);new=float(aline[key])
        if (old<=100.001 and new>100.001) or (old>100.001 and new>old+args.max_line_growth_pp):
            if key not in slopes['lines']:
                missing.append(('line',key));continue
            target=(99.999 if old<=100.001 else old+args.max_line_growth_pp)
            pred=v291.predict_value(key,slopes['lines'][key],old,changes)
            bias=min(args.max_ac_line_bias_pp,max(0.,new-pred)+args.cut_safety_pp)
            line.append((max(new-target,0.)+(10. if old<=100.001 else 0.),key,target,bias))
    for key in bvolt.index:
        old=float(bvolt[key]);new=float(avolt[key])
        if old>=.95 and new<.95-1e-8 or old<.95 and new<old-args.dynamic_voltage_drop_pu:
            if key not in slopes['volts']:
                missing.append(('under',key));continue
            target=max(.95-args.dynamic_voltage_drop_pu,old-args.dynamic_voltage_drop_pu) if old>=.95 else old-args.dynamic_voltage_drop_pu
            pred=v291.predict_value(key,slopes['volts'][key],old,changes)
            bias=min(args.max_ac_voltage_bias_pu,max(0.,pred-new)+args.cut_voltage_safety_pu)
            under.append((target-new+(10. if old>=.95 else 0.),key,target,bias))
        if old<=1.05 and new>1.05+1e-8 or old>1.05 and new>old+args.dynamic_voltage_drop_pu:
            if key not in slopes['volts']:
                missing.append(('over',key));continue
            target=min(1.05+args.dynamic_voltage_drop_pu,old+args.dynamic_voltage_drop_pu) if old<=1.05 else old+args.dynamic_voltage_drop_pu
            pred=v291.predict_value(key,slopes['volts'][key],old,changes)
            bias=min(args.max_ac_voltage_bias_pu,max(0.,new-pred)+args.cut_voltage_safety_pu)
            over.append((new-target+(10. if old<=1.05 else 0.),key,target,bias))
    if missing:
        raise ValueError(f'Chaves AC novas/agravadas sem Jacobiano: {missing[:8]}')
    # A pontuacao adiciona 10 SOMENTE para ordenar eventos novos primeiro.
    # Limites fisicos e vieses nao mudam; pontuacao nao entra no MILP.
    # limite independente por tipo para nao deixar cortes termicos excluir tensao
    line.sort(key=lambda x:(-x[0],x[1]));under.sort(key=lambda x:(-x[0],x[1]))
    over.sort(key=lambda x:(-x[0],x[1]))
    return {'lines':line[:args.max_ac_cuts],
            'under':under[:args.max_ac_voltage_cuts],
            'over':over[:args.max_ac_voltage_cuts],
            'summary':{'line_candidates':len(line),'under_candidates':len(under),
                       'over_candidates':len(over),'missing_sensitivities':len(missing)}}


def cut_settings(cuts,cap_base,floor_base,ceil_base,args,limit=None):
    """Deriva controles de cortes sem jamais alterar as guardas originais."""
    caps=dict(cap_base);floors=dict(floor_base);ceilings=dict(ceil_base)
    lb={};vb={}
    for _,key,goal,bias in cuts['lines'][:limit]:
        caps[key]=min(caps.get(key,float('inf')),goal)
        lb[key]=max(lb.get(key,0.),bias)
    for _,key,goal,bias in cuts['under'][:limit]:
        if key not in ceilings:
            floors[key]=max(floors.get(key,-float('inf')),goal)
            vb[key]=max(vb.get(key,0.),bias)
    for _,key,goal,bias in cuts['over'][:limit]:
        if key not in floors:
            ceilings[key]=min(ceilings.get(key,float('inf')),goal)
            vb[key]=max(vb.get(key,0.),bias)
    return caps,floors,ceilings,lb,vb


def seed_from_v291(ctx,args,slopes):
    """Reutiliza BT0 AC como experimento rejeitado, nunca como incumbente."""
    folder=Path(args.v291_dir).resolve()
    final=v27.read_json(folder/'final_report_v291.json')
    if final.get('state')!='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V291':
        raise ValueError('V2.9.1 nao preservou a V2.8.1 corrigida')
    for filename in ('branches.csv','voltages.csv','source.csv','regulators.csv','capacitors.csv'):
        old=folder/'best'/'AC'/filename
        base=ctx['v281cdir']/'best'/'AC'/filename
        if not old.is_file() or not base.is_file() or v291.raw_hash(old)!=v291.raw_hash(base):
            raise RuntimeError(f'Incumbente V2.9.1 diverge: {filename}')
    for filename in ('bess_dispatch.csv','vehicle_dispatch.csv',
                     'injections_reoptimized.csv','control_policy_v25.json'):
        old=folder/'best'/filename
        base=ctx['v281cdir']/'best'/filename
        if not old.is_file() or not base.is_file() or v291.raw_hash(old)!=v291.raw_hash(base):
            raise RuntimeError(f'Despacho ou politica V2.9.1 diverge: {filename}')
    f=folder/'iter_01'/'trial_bt0';rep=v27.read_json(f/'report_v291.json')
    if rep['status']!='AC_REJECTED':raise ValueError('BT0 da V2.9.1 nao e rejeitada')
    ac=ctx['v22'].read_ac_folder(f/'AC')
    q=v28.qmetrics(ctx,ac)
    v29.assert_scores(q,rep['ac'],'V2.9.1 BT0 recalculada')
    b=ctx['v24'].read_bess(f,ctx['state'])
    cars=ctx['v24'].read_cars(f,ctx['state'])
    ctx['v24'].validate_resources(ctx['state']['team'],b,cars,ctx['state']['places'],ctx['state']['cap'])
    prof=v29.verify_injections(ctx,f,b,cars)
    prop={'bess':b,'cars':cars,'profiles':prof,'ac':ac,'metrics':q,'path':f}
    cuts=full_ac_cuts(ctx,ctx['incumbent'],prop,ac,slopes,args)
    return cuts,prop


def experiment_plans(args):
    # Ablações distinguem NOVOS cortes e diminuicao de trust; nunca mudam AC gate.
    return [
        {'label':'cortes_sem_reduzir_trust','trust':1.,'cuts':'full'},
        {'label':'trust_reduzido_sem_cortes','trust':.5,'cuts':'none'},
        {'label':'cortes_progressivos_trust_inteiro','trust':1.,'cuts':'partial'},
        {'label':'cortes_combinados_trust_reduzido','trust':.5,'cuts':'full'},
        {'label':'cortes_sem_reduzir_trust_retentativa','trust':1.,'cuts':'full'},
    ][:args.max_attempts]


def prepare(args):
    ctx=v291.prepare(args)
    v28.ensure_output(ctx['output'],[Path(args.v291_dir).resolve()])
    if (ctx['output']/'best').exists() or (ctx['output']/'iter_01').exists() or (ctx['output']/'final_report_v292.json').exists():
        raise FileExistsError('Saida ja contem execucao V2.9.2; use outra pasta')
    return ctx


def get_slopes(ctx,args,current,it,folder):
    slopes=v291.reuse_v29_slopes(ctx,args,current,it)
    if slopes is not None:return slopes,'V29_CACHE_VERIFIED'
    with ctx['v24'].temporary_control_policy(ctx['inv'],ctx['policy']):
        slopes=ctx['v24'].calibrate_isolated_v24(ctx['repo'],current['profiles'],current['ac'],
            ctx['s0_policy'],ctx['state']['sites'],args.delta_kw,folder/'calibration')
    return slopes,'AC_RECALIBRATED'


def precheck(args,ctx):
    v291.precheck(args,ctx)
    # Apenas diagnostico de existencia do experimento anterior; auditoria completa
    # da semente exige importar as sensibilidades reais na task run.
    f=Path(args.v291_dir).resolve()/'iter_01'/'trial_bt0'/'AC'/'voltages.csv'
    if not f.is_file():raise FileNotFoundError(f)
    seed_audit={'status':'PENDING_CALIBRATION_AT_RUN'}
    if args.reuse_v29_calibration:
        slopes=v291.reuse_v29_slopes(ctx,args,ctx['incumbent'],1)
        seed,_=seed_from_v291(ctx,args,slopes)
        seed_audit={'status':'REPRODUCED','summary':seed['summary'],
                    'n_selected_line_cuts':len(seed['lines']),
                    'n_selected_voltage_cuts':len(seed['under'])+len(seed['over'])}
    info={'status':'V292_PRECHECK_OK','incumbent':'V281C_BT0',
          'v291_seed_trial':str(f.parent),'max_attempts':args.max_attempts,
          'near_voltage_margin_pu':args.near_voltage_margin_pu,
          'seed_audit':seed_audit,
          'acceptance':'unchanged_v291_and_v281_dual_voltage_gates'}
    v27.write_json(ctx['output']/'precheck_v292.json',info)
    print('[V2.9.2] Precheck OK; V2.8.1 corrigida preservada; semente V2.9.1 presente.',flush=True)


def run(args,ctx):
    out=ctx['output'];inc=ctx['incumbent']
    v27.write_json(out/'run_manifest_v292.json',vars(args))
    shutil.copytree(ctx['v281cdir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v292.json',{
        'source':'V281C_BT0_UNCHANGED','absolute_certification':False})
    idir=out/'iter_01';idir.mkdir()
    records=[];best=inc;accepted=False
    slopes,source=get_slopes(ctx,args,inc,1,idir)
    seed,prior=seed_from_v291(ctx,args,slopes)
    v27.write_json(idir/'seed_cuts_v292.json',seed)
    base_caps,base_floors,focus=v29.additional_milp_guards(ctx,inc['ac'],args)
    focus23=v29.exact_key(inc['ac']['branches'],list(KEY_L),v291.TARGET23,'loading_pct')
    base_caps[v291.TARGET23]=min(base_caps.get(v291.TARGET23,float('inf')),
                                  focus23-args.target_hour23_gain_pp)
    priority=[x[1] for part in ('under','over') for x in seed[part]]
    vf,vc,diagnostic=voltage_guards(inc['ac'],slopes,args,priority)
    for key,val in base_floors.items():
        if key in vc:del vc[key]
        vf[key]=max(vf.get(key,-float('inf')),val)
    v27.write_json(idir/'guard_manifest_v292.json',{
        'slopes_source':source,'dynamic_voltage':diagnostic,
        'seed_ac_reused':True,'seed_summary':seed['summary'],
        'line_guard_keys':len(base_caps),'voltage_floor_keys':len(vf),
        'voltage_ceiling_keys':len(vc),'AC_gate_unmodified':True})
    print(f'[V2.9.2] Sensibilidades={source}; cortes da BT0 anterior: '
          f'{seed["summary"]["line_candidates"]} linhas, '
          f'{seed["summary"]["under_candidates"]} subtensoes, '
          f'{seed["summary"]["over_candidates"]} sobretensoes.',flush=True)
    print(f'[V2.9.2] Guardas 24h: {len(vf)} pisos e {len(vc)} tetos.',flush=True)
    linear_current=inc
    recalibrations=0
    all_cuts=seed
    for i,plan in enumerate(experiment_plans(args)):
        trial=idir/f'trial_{i:02d}_{plan["label"]}'
        ratio=plan['trust'];mode=plan['cuts']
        if mode=='none':limit=0
        elif mode=='partial':limit=args.progressive_cut_count
        else:limit=None
        caps,floors,ceilings,lb,vb=cut_settings(all_cuts,base_caps,vf,vc,args,limit)
        rec={'attempt':i,'label':plan['label'],'trust_factor':ratio,
             'extra_cut_mode':mode,'n_line_biases':len(lb),'n_voltage_biases':len(vb),
             'reference_for_AC_acceptance':'V281C_BT0',
             'linearization_center':'incumbent' if linear_current is inc else 'rejected_trial',
             'slopes_source':source}
        v27.write_json(idir/f'inputs_trial_{i:02d}_v292.json', {
            'strategy':plan,'line_caps':[{ 'key':list(k),'cap_pct':v} for k,v in sorted(caps.items())],
            'voltage_floors':[{ 'key':list(k),'floor_pu':v} for k,v in sorted(floors.items())],
            'voltage_ceilings':[{ 'key':list(k),'ceiling_pu':v} for k,v in sorted(ceilings.items())],
            'line_bias':[{ 'key':list(k),'bias_pp':v} for k,v in sorted(lb.items())],
            'voltage_bias':[{ 'key':list(k),'bias_pu':v} for k,v in sorted(vb.items())],
            'absolute_thermal_reference_pph':v291.absolute_thermal(inc['ac']),
            'strict_voltage_reference_puh':v291.strict_voltage(inc['ac'])})
        print(f'[V2.9.2] Tentativa {i+1}: {plan["label"]}; '
              f'BESS={args.bess_trust_kw*ratio:g}, V2G={args.v2g_trust_kw*ratio:g}',flush=True)
        try:
            sol=make_milp_v292(ctx['state']['team'],ctx['state']['places'],ctx['state']['cap'],
                linear_current['bess'],linear_current['cars'],linear_current['ac'],ctx['s0_policy'],slopes,
                criterion='relative',trust_bess=args.bess_trust_kw*ratio,
                trust_v2g=args.v2g_trust_kw*ratio,time_limit=args.time_limit_s,mip_gap=args.mip_gap,
                hard_line_caps=caps,protected_voltage=floors,voltage_ceilings=ceilings,
                focus_target=focus,peak_growth_kw=args.local_peak_growth_kw,
                absolute_thermal_target=v291.absolute_thermal(inc['ac'])-args.predicted_absolute_gain_pph,
                strict_voltage_budget_puh=v291.strict_voltage(inc['ac'])+args.strict_voltage_budget_puh,
                max_line_growth_pp=args.max_line_growth_pp,
                nonoverloaded_margin_pp=args.nonoverloaded_margin_pp,
                hard_line_bias=lb,hard_voltage_bias=vb)
            prof=v29.validate_model_solution(ctx,sol,trial)
            ac=ctx['v24'].evaluate(ctx['repo'],prof,ctx['state']['sites'],ctx['inv'],ctx['policy'],trial/'AC')
            metrics=v28.qmetrics(ctx,ac)
            ok,reasons,audit=v291.gate_v291(ctx,inc,ac,metrics,args)
            guards=v27.guard_values(ctx['guard_table'],ac['branches'])
            guards.to_csv(trial/'guardas_v292_ac.csv',index=False)
            prop={'bess':sol['bess'],'cars':sol['cars'],'profiles':prof,'ac':ac,
                  'metrics':metrics,'path':trial}
            rec.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                       reasons=reasons,ac=metrics,gate_audit=audit,
                       model_stats=sol['model_stats'],solver_status=sol['solver_status'],
                       solver_gap=sol['mip_gap'],changed_taps=changed_taps(inc['ac'],ac))
            if ok:
                accepted=True;best=prop
                shutil.rmtree(out/'best');shutil.copytree(trial,out/'best')
                v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                v27.write_json(out/'best'/'ac_score_vs_original_s0.json',metrics['orig'])
                v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',metrics['policy'])
                v27.write_json(out/'best'/'provenance_v292.json',{
                   'source':str(trial),'AC_eligible':True,'absolute_certification':False})
            else:
                cuts=full_ac_cuts(ctx,linear_current,prop,ac,slopes,args)
                rec['new_cuts']=cuts['summary']
                # diagnosticos reprodutiveis; a proxima tentativa escolhe qual
                # grupo de cortes usa, sem falsificar a viabilidade do MILP.
                if cuts['lines'] or cuts['under'] or cuts['over']:
                    all_cuts=cuts
                if (args.adaptive_recalibration and i>=2 and not accepted and
                    recalibrations<args.max_recalibrations and
                    rec['changed_taps']>=args.tap_change_threshold):
                    # Recalibra em um estado REJEITADO apenas como centro local.
                    # O gate continua comparando com a incumbente V281C.
                    try:
                        slopes,source=get_slopes(ctx,args,prop,2,trial/'recalibrated')
                        recalibrations+=1;linear_current=prop
                        all_cuts={'lines':[],'under':[],'over':[],
                                  'summary':{'reason':'relinearized; previous biases invalid'}}
                        vf,vc,diagnostic=voltage_guards(inc['ac'],slopes,args,priority)
                        for key,val in base_floors.items():
                            if key in vc:del vc[key]
                            vf[key]=max(vf.get(key,-float('inf')),val)
                        rec['recalibration']={'state':'OK','changed_taps':rec['changed_taps']}
                    except Exception as e:
                        rec['recalibration']={'state':'FAILED','error':str(e)}
                        linear_current=inc
            print(f'[V2.9.2] AC T={metrics["policy"]["thermal_aggravation_pp_h"]:.3f}, '
                  f'ABS={metrics["orig"]["strict_thermal_pp_h"]:.3f}, '
                  f'V={metrics["policy"]["voltage_aggravation_pu_h"]:.4f}, '
                  f'aceito={ok}',flush=True)
        except Exception as exc:
            rec.update(status='MILP_INFEASIBLE_OR_ERROR',error=str(exc),
                       error_type=type(exc).__name__)
            trial.mkdir(parents=True,exist_ok=True)
            v27.write_json(trial/'error_v292.json',{
                'error':str(exc),'traceback':traceback.format_exc()})
            print(f'[V2.9.2] {rec["status"]}: {exc}',flush=True)
        v27.write_json(trial/'report_v292.json',rec)
        if accepted and best.get('path')==trial:
            v27.write_json(out/'best'/'report_v292.json',rec)
        records.append(rec)
        if accepted:break
    pd.DataFrame([{'attempt':x['attempt'],'label':x['label'],
        'status':x['status'],'reason':';'.join(x.get('reasons',[])) or x.get('error',''),
        'T_pph':x.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'absolute_pph':x.get('ac',{}).get('orig',{}).get('strict_thermal_pp_h'),
        'voltage_puh':x.get('ac',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
        'taps_different':x.get('changed_taps')}
        for x in records]).to_csv(out/'todos_candidatos_ac_v292.csv',index=False)
    report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else
                    'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V292',
            'champion':'V292' if accepted else 'V281_CORRIGIDA_BT0',
            'n_accepted':int(accepted),'attempts':len(records),
            'strategy_diagnostics':[{'name':x['label'],'status':x['status'],
               'error':x.get('error'),'reasons':x.get('reasons',[])} for x in records],
            'incumbent_metrics':inc['metrics'],'best_metrics':best['metrics'],
            'AC_certification':False,'recalibrations':recalibrations,
            'limitations':['AC linearization with automatic discrete taps is local',
                'No N-1, transformer, protection, or stochastic certification']}
    v27.write_json(out/'final_report_v292.json',report)
    print(f'[V2.9.2] Resultado: {report["state"]} ({int(accepted)} aceito).',flush=True)


def parser():
    p=v291.parser()
    p.description=__doc__
    p.add_argument('--v291-dir',required=True)
    p.set_defaults(output='./results/v292_milp_cortes_dinamicos',
                   backtracks=0,max_iterations=1)
    p.add_argument('--max-attempts',type=int,default=4)
    p.add_argument('--progressive-cut-count',type=int,default=4)
    p.add_argument('--max-dynamic-voltage-keys',type=int,default=160)
    p.add_argument('--near-voltage-margin-pu',type=float,default=.002)
    p.add_argument('--dynamic-voltage-drop-pu',type=float,default=.00015)
    p.add_argument('--max-ac-voltage-cuts',type=int,default=16)
    p.add_argument('--adaptive-recalibration',action='store_true')
    p.add_argument('--max-recalibrations',type=int,default=1)
    p.add_argument('--tap-change-threshold',type=int,default=12)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    if (not 1<=args.max_attempts<=5 or not 1<=args.max_ac_voltage_cuts<=64 or
        not 8<=args.max_dynamic_voltage_keys<=3000 or
        not 0<args.near_voltage_margin_pu<=.02 or
        not 0<args.dynamic_voltage_drop_pu<=.001 or
        not 1<=args.progressive_cut_count<=args.max_ac_cuts or
        not 0<=args.max_recalibrations<=2 or args.tap_change_threshold<1 or
        not 0<args.delta_kw or not 0<args.bess_trust_kw or not 0<args.v2g_trust_kw or
        not 0<args.mip_gap<1 or args.time_limit_s<=0 or
        args.backtracks!=0 or args.max_iterations!=1):
        p.error('Parametros invalidos para V2.9.2')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.9.2] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
