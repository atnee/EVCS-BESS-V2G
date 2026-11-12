#!/usr/bin/env python3
"""V2.9.3: diagnostico elastic + recalibracao imediata apos mudanca de taps.

Reutiliza V2.9.2 sem modificar modulos anteriores. Diagnosticos matematicos
NUNCA geram candidatos elegiveis: somente um MILP sem folgas diagnosticas,
seguido do replay AC 24h e gates V2.9.1/V2.8.1, pode ser aceito.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import sys
import traceback

import pandas as pd
import numpy as np

from distributed_stochastic import redespachar_milp_v292 as v292
from distributed_stochastic import redespachar_milp_v291 as v291
from distributed_stochastic import redespachar_milp_v29 as v29
from distributed_stochastic import validar_passo_v27 as v27
from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic._milp_independente_v293 import make_milp_v293


def is_reported_infeasible(exc):
    """Nao confundir time-limit/erro Python com MILP provadamente infeasible."""
    msg=str(exc).lower()
    return ('infeasible' in msg or 'inviavel' in msg or 'inviável' in msg) and (
           'status=2' in msg or 'model_status is infeasible' in msg)


def classify_diagnostic(outcomes):
    """Classificacao por ablacoes de grupos; nao e um IIS formal."""
    status={x['name']:x['status'] for x in outcomes}
    b=status.get('BASE_SEM_APRENDIDOS')
    l=status.get('APENAS_CORTES_TERMICOS')
    v=status.get('APENAS_CORTES_TENSAO')
    if b=='MILP_INFEASIBLE':return 'BASE_OR_ENVELOPES_INFEASIBLE'
    if b!='MILP_PRIMAL_FOUND':return 'INCONCLUSIVE_BASE_UNKNOWN'
    if l=='MILP_INFEASIBLE' and v=='MILP_PRIMAL_FOUND':
        return 'THERMAL_LEARNED_GROUP_CONFLICT'
    if v=='MILP_INFEASIBLE' and l=='MILP_PRIMAL_FOUND':
        return 'VOLTAGE_LEARNED_GROUP_CONFLICT'
    if l=='MILP_PRIMAL_FOUND' and v=='MILP_PRIMAL_FOUND':
        return 'POSSIBLE_JOINT_GROUP_CONFLICT'
    return 'INCONCLUSIVE_INTERACTION_OR_TIMEOUT'


def plans_v293(count):
    """Primeiro reproduz a ablaçao V2.9.2; depois usa centro recalibrado."""
    seq=[
      {'name':'seed_all_full_trust','ratio':1.,'cut_mode':'full'},
      {'name':'no_seed_half_trust','ratio':.5,'cut_mode':'none'},
      {'name':'recalibrated_no_cuts_half_trust','ratio':.5,'cut_mode':'none'},
      {'name':'recalibrated_progressive_full_trust','ratio':1.,'cut_mode':'partial'},
      {'name':'recalibrated_no_cuts_full_trust','ratio':1.,'cut_mode':'none'},
      {'name':'recalibrated_full_cuts_half_trust','ratio':.5,'cut_mode':'full'},
    ]
    return seq[:count]


def selected_cuts_for_mode(cuts,args,mode):
    if mode=='none':limit=0
    elif mode=='partial':limit=args.progressive_cut_count
    elif mode=='full':limit=None
    else:raise ValueError(f'Modo de cortes desconhecido: {mode}')
    return {k:cuts[k][:limit] for k in ('lines','under','over')}


def make_cuts_for_mode(cuts,base_caps,base_floors,base_ceilings,args,mode):
    n={'none':0,'partial':args.progressive_cut_count,'full':None}[mode]
    return v292.cut_settings(cuts,base_caps,base_floors,base_ceilings,args,limit=n)


def group_cuts(cuts,group):
    if group=='base':names=()
    elif group=='thermal':names=('lines',)
    elif group=='voltage':names=('under','over')
    else:raise ValueError(group)
    return {k:(cuts[k] if k in names else []) for k in ('lines','under','over')}


def solve_model(ctx,args,center,slopes,focus,settings,ratio,*,diagnostic=False,
                diag_line_keys=(),diag_under_keys=(),diag_over_keys=(),diag_seconds=None):
    caps,floors,ceilings,line_bias,voltage_bias=settings
    result=make_milp_v293(
        ctx['state']['team'],ctx['state']['places'],ctx['state']['cap'],
        center['bess'],center['cars'],center['ac'],ctx['s0_policy'],slopes,
        criterion='relative',trust_bess=args.bess_trust_kw*ratio,
        trust_v2g=args.v2g_trust_kw*ratio,
        time_limit=diag_seconds if diag_seconds is not None else args.time_limit_s,
        mip_gap=args.mip_gap,
        hard_line_caps=caps,protected_voltage=floors,voltage_ceilings=ceilings,
        focus_target=focus,peak_growth_kw=args.local_peak_growth_kw,
        absolute_thermal_target=v291.absolute_thermal(ctx['incumbent']['ac'])-args.predicted_absolute_gain_pph,
        strict_voltage_budget_puh=v291.strict_voltage(ctx['incumbent']['ac'])+args.strict_voltage_budget_puh,
        max_line_growth_pp=args.max_line_growth_pp,
        nonoverloaded_margin_pp=args.nonoverloaded_margin_pp,
        hard_line_bias=line_bias,hard_voltage_bias=voltage_bias,
        diagnostic_elastic=diagnostic,
        elastic_line_keys=diag_line_keys,elastic_under_keys=diag_under_keys,
        elastic_over_keys=diag_over_keys,
        elastic_absolute_envelope=diagnostic,
        elastic_voltage_envelope=diagnostic)
    if bool(result.get('diagnostic_mode'))!=bool(diagnostic):
        raise RuntimeError('Modelo retornou indicador de diagnostico inconsistente')
    return result


def diagnose_infeasibility(ctx,args,center,slopes,focus,cuts,bases,ratio,root):
    """Diagnostico SOMENTE em MILP. Nao valida nem executa AC em nenhum resultado.

    1. Ensaio elastico com folgas identificadas para cortes aprendidos e envelopes.
    2. Ablaçoes de grupos termicos e de tensao (sem relaxar requisitos fisicos
       na operacao real; os resultados nao podem ser candidatos elegiveis).
    """
    root.mkdir(parents=True,exist_ok=True)
    base_caps,base_floors,base_ceilings=bases
    outcomes=[]
    trials=[('ELASTIC_DIAGNOSTIC','full'),
            ('BASE_SEM_APRENDIDOS','base'),
            ('APENAS_CORTES_TERMICOS','thermal'),
            ('APENAS_CORTES_TENSAO','voltage')]
    for name,group in trials[:args.max_diagnostic_solves]:
        chosen=cuts if group=='full' else group_cuts(cuts,group)
        settings=make_cuts_for_mode(chosen,base_caps,base_floors,base_ceilings,args,'full')
        rec={'name':name,'status':'UNKNOWN','diagnostic_only':True,
             'trust_ratio':ratio,'time_limit_s':args.diagnostic_time_limit_s,
             'line_learned':len(chosen['lines']),
             'under_learned':len(chosen['under']),
             'over_learned':len(chosen['over'])}
        try:
            result=solve_model(ctx,args,center,slopes,focus,settings,ratio,
                diagnostic=(group=='full'),
                diag_line_keys=[e[1] for e in chosen['lines']],
                diag_under_keys=[e[1] for e in chosen['under']],
                diag_over_keys=[e[1] for e in chosen['over']],
                diag_seconds=args.diagnostic_time_limit_s)
            rec['status']='MILP_PRIMAL_FOUND'
            rec['solver_status']=result['solver_status']
            rec['mip_gap']=result['mip_gap']
            if group=='full':
                rec['elastic_slacks']=result['diagnostic_slacks']
                rec['elastic_nonzero']={key:sum(z['slack']>1e-7 for z in val)
                   for key,val in result['diagnostic_slacks'].items()}
        except Exception as exc:
            rec['status']='MILP_INFEASIBLE' if is_reported_infeasible(exc) else 'UNKNOWN_OR_ERROR'
            rec['error']=str(exc)
        outcomes.append(rec)
        v27.write_json(root/f'diagnose_{len(outcomes):02d}.json',rec)
    summary={'diagnostic_only':True,'never_run_AC':True,'never_accept_candidate':True,
            'group_classification_not_IIS':classify_diagnostic(outcomes),
            'outcomes':outcomes}
    v27.write_json(root/'diagnostic_summary_v293.json',summary)
    return summary


def should_recalibrate(args,changed_taps,recalibrations,has_more_attempts):
    """Gatilho imediato: sem dependencia de indice i da tentativa."""
    return bool(args.adaptive_recalibration and has_more_attempts and
                changed_taps>=args.tap_change_threshold and
                recalibrations<args.max_recalibrations)


def updated_voltage_guards(inc,slopes,args,priority,base_floors):
    """Alvos de seguranca ancorados no incumbente, Jacobiano no novo centro."""
    vf,vc,detail=v292.voltage_guards(inc['ac'],slopes,args,priority)
    for key,val in base_floors.items():
        vc.pop(key,None)
        vf[key]=max(vf.get(key,-float('inf')),val)
    if set(vf)&set(vc):raise RuntimeError('Colisao de pisos/tetos apos recalibracao')
    return vf,vc,detail


def prepare(args):
    ctx=v292.prepare(args)
    old=Path(args.v292_dir).resolve()
    v28.ensure_output(ctx['output'],[old])
    if ((ctx['output']/'best').exists() or (ctx['output']/'iter_01').exists() or
            (ctx['output']/'final_report_v293.json').exists()):
        raise FileExistsError('V2.9.3 ja executada nesta pasta; use --output diferente')
    return ctx


def precheck(args,ctx):
    v292.precheck(args,ctx)
    old=Path(args.v292_dir).resolve()
    final=v27.read_json(old/'final_report_v292.json')
    if final.get('state')!='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V292':
        raise RuntimeError('V2.9.2 nao preservou o incumbente V2.8.1 corrigida')
    for filename in ('branches.csv','voltages.csv','source.csv','regulators.csv','capacitors.csv'):
        a=old/'best'/'AC'/filename;b=ctx['v281cdir']/'best'/'AC'/filename
        if not a.is_file() or not b.is_file() or v291.raw_hash(a)!=v291.raw_hash(b):
            raise RuntimeError(f'Incumbente em V2.9.2 alterado: {filename}')
    for filename in ('bess_dispatch.csv','vehicle_dispatch.csv',
                     'injections_reoptimized.csv','control_policy_v25.json'):
        a=old/'best'/filename;b=ctx['v281cdir']/'best'/filename
        if not a.is_file() or not b.is_file() or v291.raw_hash(a)!=v291.raw_hash(b):
            raise RuntimeError(f'Despacho V2.9.2 alterado: {filename}')
    prev=final.get('strategy_diagnostics',[])
    n_bad=sum(x.get('status')=='MILP_INFEASIBLE_OR_ERROR' for x in prev)
    info={'status':'V293_PRECHECK_OK','reference':'V281C_BT0',
          'previous_v292':str(old),'v292_infeasible_attempts':n_bad,
          'adaptive_recalibration':bool(args.adaptive_recalibration),
          'tap_change_threshold':args.tap_change_threshold,
          'diagnostic_solves_max':args.max_diagnostic_solves,
          'no_change_to_AC_eligibility':True,
          'previous_champion_hash_verified':True}
    v27.write_json(ctx['output']/'precheck_v293.json',info)
    print(f'[V2.9.3] Precheck OK; V2.9.2 com {n_bad} tentativas de MILP malsucedidas; '
          f'BT0 V2.8.1 corrigida reproduzida.',flush=True)


def run(args,ctx):
    out=ctx['output'];inc=ctx['incumbent']
    v27.write_json(out/'run_manifest_v293.json',vars(args))
    shutil.copytree(ctx['v281cdir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v293.json',{'source':'V281C_BT0_UNCHANGED',
                    'absolute_certification':False})
    idir=out/'iter_01';idir.mkdir(exist_ok=False)
    rows=[]; accepted=False; best=inc
    slopes,source=v292.get_slopes(ctx,args,inc,1,idir)
    seed,prior=v292.seed_from_v291(ctx,args,slopes)
    v27.write_json(idir/'seed_cuts_v293.json',seed)
    caps0,base_vfloors,focus=v29.additional_milp_guards(ctx,inc['ac'],args)
    critical23=v29.exact_key(inc['ac']['branches'],list(v291.KEY_LINE),v291.TARGET23,'loading_pct')
    caps0[v291.TARGET23]=min(caps0.get(v291.TARGET23,float('inf')),
                               critical23-args.target_hour23_gain_pp)
    priority={e[1] for k in ('under','over') for e in seed[k]}
    floors0,ceilings0,vdiag=updated_voltage_guards(inc,slopes,args,priority,base_vfloors)
    v27.write_json(idir/'guard_manifest_v293.json',{
        'slopes_source':source,'priority_voltage_keys':len(priority),
        'initial_voltage_guards':vdiag,'abs_thermal_target_incumbent':v291.absolute_thermal(inc['ac'])-
        args.predicted_absolute_gain_pph,'absolute_voltage_budget':v291.strict_voltage(inc['ac'])+
        args.strict_voltage_budget_puh})
    all_cuts=seed;center=inc;recalibrations=0;diagnostic_events=0
    for i,plan in enumerate(plans_v293(args.max_attempts)):
        trial=idir/f'trial_{i:02d}_{plan["name"]}'
        chosen_mode=plan['cut_mode']
        settings=make_cuts_for_mode(all_cuts,caps0,floors0,ceilings0,args,chosen_mode)
        caps,floors,ceilings,lb,vb=settings
        bases=(caps0,floors0,ceilings0)
        rec={'attempt':i,'strategy':plan['name'],'status':'PENDING',
             'trust_factor':plan['ratio'],'cuts_mode':chosen_mode,
             'n_learned_line_biases':len(lb),'n_learned_voltage_biases':len(vb),
             'linearization_center':'INCUMBENT_V281C' if center is inc else 'REJECTED_AC',
             'slopes_source':source,'AC_gate_reference':'V281C_BT0',
             'diagnostic_slacks_in_official_MILP':False}
        v27.write_json(idir/f'model_configuration_trial_{i:02d}_v293.json',{
            'line_caps':[{'key':list(k),'value':v} for k,v in sorted(caps.items())],
            'voltage_floors':[{'key':list(k),'value':v} for k,v in sorted(floors.items())],
            'voltage_ceilings':[{'key':list(k),'value':v} for k,v in sorted(ceilings.items())],
            'line_biases':[{'key':list(k),'bias':v} for k,v in sorted(lb.items())],
            'voltage_biases':[{'key':list(k),'bias':v} for k,v in sorted(vb.items())],
            'trust_ratio':plan['ratio'],'linear_center':rec['linearization_center']})
        print(f'[V2.9.3] Tentativa {i+1}/{args.max_attempts}: {plan["name"]}, '
              f'trust BESS={args.bess_trust_kw*plan["ratio"]:g} kW, '
              f'V2G={args.v2g_trust_kw*plan["ratio"]:g} kW.',flush=True)
        try:
            sol=solve_model(ctx,args,center,slopes,focus,settings,plan['ratio'])
            if sol['diagnostic_mode']:
                raise RuntimeError('Solucao diagnostica nao pode chegar a validacao AC')
            profiles=v29.validate_model_solution(ctx,sol,trial)
            ac=ctx['v24'].evaluate(ctx['repo'],profiles,ctx['state']['sites'],
                                     ctx['inv'],ctx['policy'],trial/'AC')
            metrics=v28.qmetrics(ctx,ac)
            ok,reasons,audit=v291.gate_v291(ctx,inc,ac,metrics,args)
            guards=v27.guard_values(ctx['guard_table'],ac['branches'])
            guards.to_csv(trial/'guardas_v293_ac.csv',index=False)
            prop={'bess':sol['bess'],'cars':sol['cars'],'profiles':profiles,
                  'ac':ac,'metrics':metrics,'path':trial}
            change=v292.changed_taps(inc['ac'],ac)
            rec.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                       ac=metrics,gate_reasons=reasons,gate_audit=audit,
                       changed_tap_hour_states=change,
                       model_stats=sol['model_stats'],solver_status=sol['solver_status'],
                       solver_gap=sol['mip_gap'])
            if ok:
                accepted=True;best=prop
                shutil.rmtree(out/'best')
                shutil.copytree(trial,out/'best')
                v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                v27.write_json(out/'best'/'ac_score_vs_original_s0.json',metrics['orig'])
                v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',metrics['policy'])
                v27.write_json(out/'best'/'provenance_v293.json',{
                    'source':str(trial),'AC_eligible':True,'absolute_certification':False})
            else:
                # Identificar todas as transicoes AC, sem restringir a 18h.
                learned=v292.full_ac_cuts(ctx,center,prop,ac,slopes,args)
                rec['new_cut_audit']=learned['summary']
                if any(learned[k] for k in ('lines','under','over')):
                    all_cuts=learned
                priority.update(e[1] for k in ('under','over') for e in learned[k])
                # O gatilho e IMEDIATO no primeiro AC rejeitado com taps divergentes.
                if should_recalibrate(args,change,recalibrations,i+1<args.max_attempts):
                    rec['recalibration_triggered_immediately']=True
                    try:
                        new_slopes,new_source=v292.get_slopes(ctx,args,prop,2,trial/'recalibration')
                        new_floors,new_ceilings,new_diag=updated_voltage_guards(
                             inc,new_slopes,args,priority,base_vfloors)
                        # Unidades, estados e cut biases calculados no centro anterior
                        # nao podem ser reutilizados na nova linearizacao.
                        center=prop;slopes=new_slopes;source=new_source
                        floors0,ceilings0=new_floors,new_ceilings
                        all_cuts={'lines':[],'under':[],'over':[],
                                  'summary':{'reason':'RESET_AFTER_NEW_AC_JACOBIAN'}}
                        recalibrations+=1
                        rec['recalibration']={'status':'OK','center':'REJECTED_AC',
                           'new_source':source,'guards':new_diag,
                           'previous_AC_gates_still_computed_vs':'V281C_BT0'}
                        print(f'[V2.9.3] Recalibracao imediata executada: '
                              f'{change} mudancas horarias de tap.',flush=True)
                    except Exception as exc:
                        rec['recalibration']={'status':'FAILED','error':str(exc)}
                        # Nao permitir reuso de calibracao parcialmente atualizada.
                        center=inc
                        try:
                            slopes,source=v292.get_slopes(ctx,args,inc,1,idir/'restored_reference')
                            floors0,ceilings0,_=updated_voltage_guards(inc,slopes,args,priority,base_vfloors)
                        except Exception:
                            rec['recalibration']['restore']='FAILED_ABORT'
                            raise
            print(f'[V2.9.3] AC T={metrics["policy"]["thermal_aggravation_pp_h"]:.3f} '
                  f'ABS={metrics["orig"]["strict_thermal_pp_h"]:.3f} '
                  f'V={metrics["policy"]["voltage_aggravation_pu_h"]:.4f} '
                  f'aceito={ok}',flush=True)
        except Exception as exc:
            infeasible=is_reported_infeasible(exc)
            rec.update(status='MILP_INFEASIBLE' if infeasible else 'ERROR',
                       error=str(exc),error_type=type(exc).__name__)
            v27.write_json(trial/'error_v293.json',{
                'error':str(exc),'type':type(exc).__name__,'traceback':traceback.format_exc()})
            if infeasible and diagnostic_events<args.max_diagnostic_events:
                diagnostic_events+=1
                print('[V2.9.3] MILP inviavel: diagnosticando cortes e envoltorias '
                      '(SEM replay AC dos testes).',flush=True)
                try:
                    rec['infeasibility_diagnosis']=diagnose_infeasibility(ctx,args,center,
                        slopes,focus,selected_cuts_for_mode(all_cuts,args,chosen_mode),
                        bases,plan['ratio'],trial/'diagnostics')
                except Exception as diag_exc:
                    rec['infeasibility_diagnosis']={'status':'ERROR_NOT_CERTIFICATION',
                        'error':str(diag_exc)}
            # Um centro rejeitado pode estar fora de qualquer regiao pequena
            # que recupere as guardas. Voltar ao centro conhecido e valido
            # para NOVOS MILPs, nunca aceitar a tentativa que falhou.
            if infeasible and center is not inc and i+1<args.max_attempts:
                try:
                    restored,restore_src=v292.get_slopes(ctx,args,inc,1,idir/'restore_incumbent')
                    fnew,cnew,_=updated_voltage_guards(inc,restored,args,priority,base_vfloors)
                    center=inc;slopes=restored;source=restore_src
                    floors0,ceilings0=fnew,cnew
                    all_cuts=seed
                    rec['fallback_center']={'status':'RESTORED_INCUMBENT',
                       'source':restore_src,'reason':'relinearized_center_infeasible'}
                except Exception as restore_exc:
                    rec['fallback_center']={'status':'FAILED','error':str(restore_exc)}
            print(f'[V2.9.3] {rec["status"]}: {exc}',flush=True)
        v27.write_json(trial/'report_v293.json',rec)
        if accepted:
            v27.write_json(out/'best'/'report_v293.json',rec)
        rows.append(rec)
        if accepted:break
    pd.DataFrame([{'attempt':r['attempt'],'strategy':r['strategy'],
        'status':r['status'],'reason':';'.join(r.get('gate_reasons',[])) or r.get('error',''),
        'thermal_pph':r.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'absolute_thermal_pph':r.get('ac',{}).get('orig',{}).get('strict_thermal_pp_h'),
        'voltage_puh':r.get('ac',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
        'tap_changes':r.get('changed_tap_hour_states'),
        'recalibration':r.get('recalibration',{}).get('status')}
        for r in rows]).to_csv(out/'todos_candidatos_ac_v293.csv',index=False)
    final={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else
            'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V293',
           'champion':'V293' if accepted else 'V281_CORRIGIDA_BT0',
           'n_accepted':int(accepted),'attempts':len(rows),
           'recalibrations':recalibrations,'infeasibility_diagnostic_events':diagnostic_events,
           'experiments':[{'name':r['strategy'],'status':r['status'],
                          'reasons':r.get('gate_reasons',[]),'error':r.get('error')}
                         for r in rows],
           'reference_metrics':inc['metrics'],'best_metrics':best['metrics'],
           'absolute_AC_certification':False,
           'notes':['Only full AC 24h plus unchanged gates decides acceptance',
                    'Elastic and group-ablation diagnostic results are never AC candidates',
                    'Controls may be discontinuous; Jacobian remains local',
                    'No N-1, protection, transformer, or stochastic certification']}
    v27.write_json(out/'final_report_v293.json',final)
    print(f'[V2.9.3] Resultado: {final["state"]} '
          f'({int(accepted)} candidato aceito).',flush=True)


def parser():
    p=v292.parser()
    p.description=__doc__
    p.add_argument('--v292-dir',required=True)
    p.set_defaults(output='./results/v293_milp_diagnostico_recalibracao',
                   max_attempts=4,adaptive_recalibration=True,max_recalibrations=1,
                   backtracks=0,max_iterations=1)
    p.add_argument('--no-adaptive-recalibration',dest='adaptive_recalibration',
                   action='store_false',help='Diagnosticar sem recalibracao adaptativa')
    p.add_argument('--max-diagnostic-events',type=int,default=1)
    p.add_argument('--max-diagnostic-solves',type=int,default=4)
    p.add_argument('--diagnostic-time-limit-s',type=int,default=75)
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    valid=[args.delta_kw,args.bess_trust_kw,args.v2g_trust_kw,
           args.time_limit_s,args.strict_voltage_budget_puh,args.max_line_growth_pp,
           args.nonoverloaded_margin_pp,args.diagnostic_time_limit_s]
    if (any(not np.isfinite(v) or v<=0 for v in valid) or
        not 1<=args.max_attempts<=6 or not 0<=args.max_diagnostic_events<=2 or
        not 1<=args.max_diagnostic_solves<=4 or not 0<args.mip_gap<1 or
        args.tap_change_threshold<1 or not 0<=args.max_recalibrations<=2 or
        args.backtracks!=0 or args.max_iterations!=1 or
        not 8<=args.max_dynamic_voltage_keys<=3000 or
        not 0<args.dynamic_voltage_drop_pu<=.001 or
        not 0<args.near_voltage_margin_pu<=.02 or
        args.voltage_budget_puh>args.historical_voltage_budget_puh):
        p.error('Parametros invalidos para V2.9.3')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.9.3] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
