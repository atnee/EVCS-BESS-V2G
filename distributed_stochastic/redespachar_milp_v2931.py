#!/usr/bin/env python3
"""V2.9.3.1: diagnostico elastic + recalibracao imediata apos mudanca de taps.

Reutiliza V2.9.2 sem modificar modulos anteriores. Diagnosticos matematicos
NUNCA geram candidatos elegiveis: somente um MILP sem folgas diagnosticas,
seguido do replay AC 24h e gates V2.9.1/V2.8.1, pode ser aceito.
"""
from __future__ import annotations

import json
import math
import copy
import hashlib
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
from distributed_stochastic._milp_independente_v2931 import make_milp_v2931


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


def solve_model(ctx,args,center,slopes,focus,settings,ratio,*,diagnostic=False,phase1=False,
                diag_line_keys=(),diag_under_keys=(),diag_over_keys=(),diag_seconds=None):
    caps,floors,ceilings,line_bias,voltage_bias=settings
    if phase1 and not diagnostic:
        raise ValueError("Phase I deve ser exclusivamente diagnostica")
    result=make_milp_v2931(
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
        diagnostic_elastic=diagnostic,phase1=phase1,
        elastic_line_keys=diag_line_keys,elastic_under_keys=diag_under_keys,
        elastic_over_keys=diag_over_keys,
        elastic_absolute_envelope=diagnostic,
        elastic_voltage_envelope=diagnostic)
    if bool(result.get('diagnostic_mode'))!=bool(diagnostic) or bool(result.get('phase1'))!=bool(phase1):
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
            # Os tres ensaios de ablacao sao STRICT: nenhuma chave elastica.
            # Somente o ensaio ELASTIC e Phase I (sem objetivo economico).
            if group=='full':
                result=solve_model(ctx,args,center,slopes,focus,settings,ratio,
                    diagnostic=True,phase1=True,
                    diag_line_keys=[e[1] for e in chosen['lines']],
                    diag_under_keys=[e[1] for e in chosen['under']],
                    diag_over_keys=[e[1] for e in chosen['over']],
                    diag_seconds=args.diagnostic_time_limit_s)
            else:
                result=solve_model(ctx,args,center,slopes,focus,settings,ratio,
                    diagnostic=False,phase1=False,
                    diag_seconds=args.diagnostic_time_limit_s)
            rec['status']='MILP_PRIMAL_FOUND'
            rec['solver_status']=result['solver_status']
            rec['phase1']=bool(result.get('phase1'))
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
    v28.ensure_output(ctx['output'],[old,Path(args.v293_dir).resolve()])
    if ((ctx['output']/'best').exists() or (ctx['output']/'iter_01').exists() or
            (ctx['output']/'final_report_v2931.json').exists()):
        raise FileExistsError('V2.9.3.1 ja executada nesta pasta; use --output diferente')
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
    # A V2.9.3 entregue pelo usuario so pode ser reutilizada apos verificar
    # que preservou o incumbente, sem assumir que qualquer simulacao e aceita.
    prev293=Path(args.v293_dir).resolve()
    prev293_report=v27.read_json(prev293/'final_report_v293.json')
    if prev293_report.get('state')!='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V293':
        raise RuntimeError('Historico V2.9.3 divergente; interromper antes de otimizar')
    verify_champion_files(prev293/'best',ctx['v281cdir']/'best')
    prev=final.get('strategy_diagnostics',[])
    n_bad=sum(x.get('status')=='MILP_INFEASIBLE_OR_ERROR' for x in prev)
    info={'status':'V2931_PRECHECK_OK','reference':'V281C_BT0',
          'previous_v292':str(old),'v292_infeasible_attempts':n_bad,
          'adaptive_recalibration':bool(args.adaptive_recalibration),
          'tap_change_threshold':args.tap_change_threshold,
          'diagnostic_solves_max':args.max_diagnostic_solves,
          'no_change_to_AC_eligibility':True,
          'previous_champion_hash_verified':True,
          'v293_prior_verification':'PASS',
          'phase1_diagnostic_never_AC':True,
          'restore_from_memory_no_cache':True}
    v27.write_json(ctx['output']/'precheck_v2931.json',info)
    print(f'[V2.9.3.1] Precheck OK; V2.9.2 com {n_bad} tentativas de MILP malsucedidas; '
          f'BT0 V2.8.1 corrigida reproduzida.',flush=True)


CHAMPION_FILES=(
    'AC/branches.csv','AC/voltages.csv','AC/source.csv',
    'AC/regulators.csv','AC/capacitors.csv',
    'bess_dispatch.csv','vehicle_dispatch.csv',
    'injections_reoptimized.csv','control_policy_v25.json')


def verify_champion_files(left,right):
    """Nenhum relatorio de sucesso substitui a identidade byte a byte."""
    digest={}
    for name in CHAMPION_FILES:
        a=Path(left)/name;b=Path(right)/name
        if not a.is_file() or not b.is_file():
            raise FileNotFoundError(f'Arquivo do incumbente ausente: {name}')
        ha=v291.raw_hash(a);hb=v291.raw_hash(b)
        if ha!=hb:raise RuntimeError(f'Incumbente historico alterado: {name}')
        digest[name]=ha
    return digest


def snapshot_incumbent(inc,slopes,source,original_best):
    """Snapshot independente do cache. Nunca modificar os arrays AC guardados."""
    return {'center':copy.deepcopy(inc),'slopes':copy.deepcopy(slopes),
            'source':str(source),'source_hashes':{
                name:v291.raw_hash(Path(original_best)/name) for name in CHAMPION_FILES}}


def restore_from_memory(snapshot,original_best):
    """Restaura atomicamente centro e Jacobianos SEM acessar cache V2.9."""
    for name,expected in snapshot['source_hashes'].items():
        if v291.raw_hash(Path(original_best)/name)!=expected:
            raise RuntimeError(f'Arquivo base modificado durante otimizacao: {name}')
    return copy.deepcopy(snapshot['center']),copy.deepcopy(snapshot['slopes']),snapshot['source']


def phase1_audit(ctx,args,center,slopes,focus,settings,ratio,root):
    """Phase I: apenas slacks eletricos; nunca segue para OpenDSS."""
    root.mkdir(parents=True,exist_ok=True)
    record={'diagnostic_only':True,'never_run_AC':True,'never_accept':True,
            'center':'REJECTED_AC','trust_factor':float(ratio)}
    try:
        r=solve_model(ctx,args,center,slopes,focus,settings,ratio,
              diagnostic=True,phase1=True,diag_seconds=args.diagnostic_time_limit_s)
        if not r['diagnostic_mode'] or not r['phase1']:
            raise RuntimeError('Phase I retornou candidato sem etiqueta de diagnostico')
        zero=True;counts={};maxima={};units={
            'learned_line':1e-4,'learned_under':1e-6,'learned_over':1e-6,
            'sample_line':1e-4,'focus':1e-4,'source_peak':.01,
            'aggregate_thermal':1e-4,'aggregate_voltage':1e-6}
        for group,entries in r['diagnostic_slacks'].items():
            vals=[float(x['slack']) for x in entries]
            if any(not np.isfinite(v) or v< -1e-8 for v in vals):
                raise RuntimeError('Folga diagnostica invalida')
            n=sum(v>units.get(group,1e-8) for v in vals)
            counts[group]=n;maxima[group]=max(vals,default=0.)
            if n:zero=False
        record.update(status='PHASE1_ZERO_SLACK' if zero else 'PHASE1_POSITIVE_SLACK',
                      zero_electrical_slacks=zero,nonzero_groups=counts,
                      max_slack_by_group=maxima,objective=r['objective'],
                      qualification='No certification; mathematical local surrogate only')
    except Exception as exc:
        record.update(status='PHASE1_UNKNOWN_OR_INFEASIBLE',zero_electrical_slacks=False,
                      error=str(exc))
    v27.write_json(root/'phase1_report_v2931.json',record)
    return record


def injection_signature(profiles):
    """Assinatura deterministica com todas as colunas de injecao e tipo numerico."""
    keys=['hour','bus','phase']
    need=keys+['p_kw','q_kvar']
    if not set(need).issubset(profiles):
        raise ValueError('Injecoes sem colunas essenciais para assinatura')
    if profiles.duplicated(keys).any():raise ValueError('Injecoes com chaves duplicadas')
    df=profiles[need].sort_values(keys).reset_index(drop=True)
    arr=df.to_csv(index=False,float_format='%.13g').encode('utf-8')
    return hashlib.sha256(arr).hexdigest()


def copy_best_atomically(out,trial,metrics,policy):
    """Prepara novo best, troca com rollback e nunca toca no incumbente original."""
    staging=out/'_candidate_best_staging';backup=out/'_previous_best_backup'
    if staging.exists() or backup.exists():
        raise RuntimeError('Pastas temporarias de publicacao presentes; preservar para auditoria')
    shutil.copytree(trial,staging)
    v27.write_json(staging/'control_policy_v25.json',policy)
    v27.write_json(staging/'ac_score_vs_original_s0.json',metrics['orig'])
    v27.write_json(staging/'ac_score_vs_policy_s0.json',metrics['policy'])
    v27.write_json(staging/'provenance_v2931.json',{
        'source':str(trial),'AC_eligible':True,'absolute_certification':False})
    previous=out/'best'
    previous.rename(backup)
    try:
        staging.rename(previous)
    except Exception:
        backup.rename(previous)
        raise
    shutil.rmtree(backup)


def prior_ac_if_identical(ctx,args,profiles):
    """Reuso opcional somente se injecoes E politica e scores baterem com V2.9.3."""
    folder=Path(args.v293_dir).resolve()/'iter_01'/'trial_01_no_seed_half_trust'
    recorded=folder/'injections_reoptimized.csv'
    if not recorded.is_file():return None
    try:
        old=pd.read_csv(recorded)
        if injection_signature(profiles)!=injection_signature(old):
            # Round-trip CSV pode alterar a representacao textual: comparar numeros.
            from pandas.testing import assert_frame_equal
            cols=['hour','bus','phase','p_kw','q_kvar'];keys=cols[:3]
            left=profiles[cols].sort_values(keys).reset_index(drop=True)
            right=old[cols].sort_values(keys).reset_index(drop=True)
            assert_frame_equal(left,right,check_dtype=False,atol=1e-9,rtol=0.)
        applied=v27.read_json(folder/'controles_aplicados.json')
        if applied['policy']!=ctx['policy']['name']:
            return None
        ac=ctx['v22'].read_ac_folder(folder/'AC')
        metrics=v28.qmetrics(ctx,ac)
        prior=v27.read_json(folder/'report_v293.json')
        if prior.get('status')!='AC_REJECTED':return None
        v29.assert_scores(metrics,prior['ac'],'V293_BТ1_reuse')
        return {'ac':ac,'metrics':metrics,'from':str(folder)}
    except Exception:
        return None  # Em caso de incerteza sempre executar OpenDSS.




def build_model_context(ctx,args,inc,slopes,seed,idir):
    caps0,base_floors,focus=v29.additional_milp_guards(ctx,inc['ac'],args)
    focus23=v29.exact_key(inc['ac']['branches'],list(v291.KEY_LINE),
                          v291.TARGET23,'loading_pct')
    caps0[v291.TARGET23]=min(caps0.get(v291.TARGET23,float('inf')),
                              focus23-args.target_hour23_gain_pp)
    priority={e[1] for k in ('under','over') for e in seed[k]}
    floors0,ceilings0,diagnostic=updated_voltage_guards(
            inc,slopes,args,priority,base_floors)
    if not caps0 or not floors0:
        raise RuntimeError('Guardas essenciais ausentes; interromper')
    v27.write_json(idir/'guard_manifest_v2931.json',{
       'initial_voltage_guards':diagnostic,
       'priority_voltage_keys':len(priority),
       'n_line_caps':len(caps0),
       'strict_voltage_budget_puh':v291.strict_voltage(inc['ac'])+
           args.strict_voltage_budget_puh,
       'absolute_thermal_target_pph':v291.absolute_thermal(inc['ac'])-
           args.predicted_absolute_gain_pph,
       'acceptance_unchanged':True})
    return (caps0,base_floors,focus,priority,floors0,ceilings0)


def diagnose_only(args,ctx):
    """Rodar apenas auditoria de MILP, nenhum AC. Pasta de saída nova."""
    out=ctx['output'];inc=ctx['incumbent']
    slopes,source=v292.get_slopes(ctx,args,inc,1,out/'diagnose_only')
    seed,_=v292.seed_from_v291(ctx,args,slopes)
    caps0,base_floors,focus,priority,floors0,ceilings0=build_model_context(
        ctx,args,inc,slopes,seed,out)
    bases=(caps0,floors0,ceilings0)
    diag=diagnose_infeasibility(ctx,args,inc,slopes,focus,seed,bases,
                              1.,out/'diagnose_only')
    v27.write_json(out/'diagnose_only_report_v2931.json',{
        'state':'DIAGNOSTIC_ONLY','no_opendss_run':True,
        'calibration_source':source,'diagnosis':diag,
        'incumbent_unmodified':True})
    print('[V2.9.3.1] Diagnostico MILP concluido, sem replay AC.',flush=True)


def run(args,ctx):
    out=ctx['output'];inc=ctx['incumbent'];original=ctx['v281cdir']/'best'
    v27.write_json(out/'run_manifest_v2931.json',vars(args))
    if (out/'best').exists() or (out/'iter_01').exists():
        raise FileExistsError('Pasta contem execucao anterior; escolha nova --output')
    source_hashes=verify_champion_files(Path(args.v293_dir)/'best',original)
    shutil.copytree(original,out/'best')
    verify_champion_files(out/'best',original)
    v27.write_json(out/'best'/'provenance_v2931.json',{
        'source':'V281_CORRIGIDA_BT0_UNCHANGED','absolute_certification':False})
    idir=out/'iter_01';idir.mkdir(exist_ok=False)
    v27.write_json(idir/'incumbent_sha256_v2931.json',source_hashes)
    history=[];accepted=False;recalibrations=0;diagnostic_events=0
    slopes,source=v292.get_slopes(ctx,args,inc,1,idir)
    snapshot=snapshot_incumbent(inc,slopes,source,original)
    center,slopes,source=restore_from_memory(snapshot,original)
    seed,_=v292.seed_from_v291(ctx,args,slopes)
    v27.write_json(idir/'seed_cuts_v2931.json',seed)
    caps0,base_floors,focus,priority,floors0,ceilings0=build_model_context(
        ctx,args,inc,slopes,seed,idir)
    all_cuts=seed
    validated_replays={}
    for i,plan in enumerate(plans_v293(args.max_attempts)):
        trial=idir/f'trial_{i:02d}_{plan["name"]}'
        # A pasta deve ser criada por v29.validate_model_solution somente depois do MILP.
        # A criacao antecipada causava WinError 183 no Windows.
        if trial.exists():
            raise FileExistsError(f'Tentativa anterior ainda presente: {trial}')
        mode=plan['cut_mode'];ratio=plan['ratio']
        settings=make_cuts_for_mode(all_cuts,caps0,floors0,ceilings0,args,mode)
        caps,floors,ceilings,lb,vb=settings
        rec={'attempt':i,'strategy':plan['name'],
             'status':'PENDING','trust_factor':ratio,'cuts_mode':mode,
             'center':'INCUMBENT' if center['path']==inc['path'] else 'REJECTED_AC',
             'calibration_source':source,
             'diagnostic_never_eligible':True,
             'n_learned_line_biases':len(lb),
             'n_learned_voltage_biases':len(vb)}
        model_configuration={
            'line_caps':[{'key':list(k),'value':v} for k,v in sorted(caps.items())],
            'voltage_floors':[{'key':list(k),'value':v} for k,v in sorted(floors.items())],
            'voltage_ceilings':[{'key':list(k),'value':v} for k,v in sorted(ceilings.items())],
            'line_biases':[{'key':list(k),'bias':v} for k,v in sorted(lb.items())],
            'voltage_biases':[{'key':list(k),'bias':v} for k,v in sorted(vb.items())],
            'center':rec['center'],'trust_factor':ratio}
        print(f'[V2.9.3.1] MILP tentativa {i+1}/{args.max_attempts}: {plan["name"]}; '
              f'centro={rec["center"]}, BESS={args.bess_trust_kw*ratio:g} kW, '
              f'V2G={args.v2g_trust_kw*ratio:g} kW.',flush=True)
        try:
            sol=solve_model(ctx,args,center,slopes,focus,settings,ratio)
            if sol.get('diagnostic_mode') or sol.get('phase1'):
                raise RuntimeError('FATAL: Solucao diagnostica indevidamente no fluxo oficial')
            # v29.validate_model_solution cria a pasta com exist_ok=False.
            # Gravar metadados apenas DEPOIS que ela conclui, para nao causar conflito.
            profiles=v29.validate_model_solution(ctx,sol,trial)
            v27.write_json(trial/'model_configuration_v2931.json',model_configuration)
            sig=injection_signature(profiles)
            prior=validated_replays.get(sig)
            if prior is None and args.reuse_verified_v293_ac:
                prior=prior_ac_if_identical(ctx,args,profiles)
            if prior is not None:
                ac=copy.deepcopy(prior['ac']);metrics=copy.deepcopy(prior['metrics'])
                if not (trial/'AC').exists():
                    (trial/'AC').mkdir()
                for k,df in ac.items():
                    df.to_csv(trial/'AC'/f'{k}.csv',index=False)
                rec['reused_AC']={'source':prior['from'],'profile_sha256':sig,
                                  'policy':ctx['policy']['name']}
            else:
                ac=ctx['v24'].evaluate(ctx['repo'],profiles,ctx['state']['sites'],
                                         ctx['inv'],ctx['policy'],trial/'AC')
                metrics=v28.qmetrics(ctx,ac)
                validated_replays[sig]={'ac':copy.deepcopy(ac),
                                       'metrics':copy.deepcopy(metrics),'from':str(trial)}
                rec['reused_AC']=None
            ok,reasons,audit=v291.gate_v291(ctx,inc,ac,metrics,args)
            guards=v27.guard_values(ctx['guard_table'],ac['branches'])
            guards.to_csv(trial/'guardas_v2931_ac.csv',index=False)
            prop={'bess':sol['bess'],'cars':sol['cars'],'profiles':profiles,
                  'ac':ac,'metrics':metrics,'path':trial}
            changed=v292.changed_taps(inc['ac'],ac)
            rec.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                       gate_reasons=reasons,gate_audit=audit,ac=metrics,
                       changed_tap_hour_states=changed,
                       solver_status=sol['solver_status'],solver_gap=sol['mip_gap'],
                       model_stats=sol['model_stats'],profile_sha256=sig)
            if ok:
                # A publicacao so ocorre apos passar pelos MESMOS gates V291.
                copy_best_atomically(out,trial,metrics,ctx['policy'])
                accepted=True
            else:
                learned=v292.full_ac_cuts(ctx,center,prop,ac,slopes,args)
                rec['new_cut_audit']=learned['summary']
                if any(learned[k] for k in ('lines','under','over')):
                    all_cuts=learned
                priority.update(e[1] for k in ('under','over') for e in learned[k])
                if should_recalibrate(args,changed,recalibrations,
                                      i+1<args.max_attempts):
                    rec['recalibration_triggered']=True
                    try:
                        new_slopes,new_source=v292.get_slopes(ctx,args,prop,2,trial/'recalibration')
                        new_floors,new_ceils,new_diag=updated_voltage_guards(
                            inc,new_slopes,args,priority,base_floors)
                        rec['recalibration']={'status':'COMPLETE','source':new_source,
                                               'guards':new_diag}
                        recalibrations+=1
                        # Antes de continuar com o novo centro rejeitado, Phase I
                        # diagnostica se as guardas podem ser atingidas localmente.
                        preliminary=make_cuts_for_mode(
                            {'lines':[],'under':[],'over':[]},caps0,
                            new_floors,new_ceils,args,'none')
                        feasibility=phase1_audit(ctx,args,prop,new_slopes,focus,
                            preliminary,ratio,trial/'phase1_rejected_center')
                        rec['recalibration']['phase1']=feasibility
                        if feasibility['status']=='PHASE1_ZERO_SLACK':
                            center=prop;slopes=new_slopes;source=new_source
                            floors0,ceilings0=new_floors,new_ceils
                            all_cuts={'lines':[],'under':[],'over':[],
                                      'summary':{'reason':'RECALIBRATED_CUTS_RESET'}}
                            rec['recalibration']['next_center']='REJECTED_AC_DIAGNOSTICALLY_REACHABLE'
                        else:
                            center,slopes,source=restore_from_memory(snapshot,original)
                            floors0,ceilings0,_=updated_voltage_guards(
                                inc,slopes,args,priority,base_floors)
                            all_cuts=seed
                            rec['recalibration']['next_center']='RESTORED_INCUMBENT_IN_MEMORY'
                    except Exception as exc:
                        center,slopes,source=restore_from_memory(snapshot,original)
                        floors0,ceilings0,_=updated_voltage_guards(
                            inc,slopes,args,priority,base_floors)
                        all_cuts=seed
                        rec['recalibration']={'status':'FAILED_RESTORED_IN_MEMORY',
                                                'error':str(exc)}
            print(f'[V2.9.3.1] AC T={metrics["policy"]["thermal_aggravation_pp_h"]:.3f}, '
                  f'ABS={metrics["orig"]["strict_thermal_pp_h"]:.3f}, '
                  f'V={metrics["policy"]["voltage_aggravation_pu_h"]:.4f}, '
                  f'aceito={ok}',flush=True)
        except Exception as exc:
            # Em infeasibilidade ou falha antes da validacao, preservar os logs
            # criando a pasta apenas aqui; jamais passar este caminho existente
            # para validate_model_solution.
            trial.mkdir(parents=True,exist_ok=True)
            v27.write_json(trial/'model_configuration_v2931.json',model_configuration)
            infeasible=is_reported_infeasible(exc)
            rec.update(status='MILP_INFEASIBLE' if infeasible else 'ERROR',
                       error=str(exc),error_type=type(exc).__name__)
            v27.write_json(trial/'error_v2931.json',{
                'error':str(exc),'type':type(exc).__name__,
                'traceback':traceback.format_exc()})
            if infeasible and diagnostic_events<args.max_diagnostic_events:
                diagnostic_events+=1
                try:
                    rec['infeasibility_diagnosis']=diagnose_infeasibility(
                        ctx,args,center,slopes,focus,
                        selected_cuts_for_mode(all_cuts,args,mode),
                        (caps0,floors0,ceilings0),ratio,trial/'diagnostics')
                except Exception as diag_error:
                    rec['infeasibility_diagnosis']={
                        'status':'DIAGNOSTIC_FAILED_NOT_CERTIFICATION',
                        'error':str(diag_error)}
            # Falhas de qualquer tipo no centro rejeitado NUNCA corrompem
            # o snapshot. O restore nao precisa de novo acesso ao cache V2.9.
            if center['path']!=inc['path']:
                center,slopes,source=restore_from_memory(snapshot,original)
                floors0,ceilings0,_=updated_voltage_guards(
                    inc,slopes,args,priority,base_floors)
                all_cuts=seed
                rec['fallback_center']={'status':'RESTORED_FROM_MEMORY',
                                        'slopes_source':source}
            print(f'[V2.9.3.1] {rec["status"]}: {exc}',flush=True)
        finally:
            v27.write_json(trial/'report_v2931.json',rec)
            if accepted:
                v27.write_json(out/'best'/'report_v2931.json',rec)
            history.append(rec)
        if accepted:break
    # Se nenhuma solucao passou AC, best deve ser IDÊNTICO ao original.
    if not accepted:
        verify_champion_files(out/'best',original)
    # V2.9.3 foi validada ANTES de iniciar o solver. Uma indisponibilidade
    # superveniente de um arquivo historico nao pode apagar o relatorio
    # desta execucao; registre o alerta em vez de confundir com rejeicao AC.
    try:
        verify_champion_files(Path(args.v293_dir)/'best',original)
        v293_postrun={'status':'PRESENT_AND_MATCHING'}
    except FileNotFoundError as exc:
        v293_postrun={'status':'UNAVAILABLE_AFTER_RUN',
                      'detail':str(exc),
                      'original_precheck':'VALIDATED_BEFORE_RUN'}
        print('[V2.9.3.1] AVISO: V2.9.3 indisponivel apos a execucao; '
              'incumbente e resultados atuais seguem sendo verificados.',flush=True)
    for name,h in snapshot['source_hashes'].items():
        if v291.raw_hash(original/name)!=h:
            raise RuntimeError(f'Incumbente alterado no disco apos execucao: {name}')
    pd.DataFrame([{
        'attempt':r['attempt'],'strategy':r['strategy'],
        'status':r['status'],
        'reason':';'.join(r.get('gate_reasons',[])) or r.get('error',''),
        'thermal_pph':r.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'absolute_thermal_pph':r.get('ac',{}).get('orig',{}).get('strict_thermal_pp_h'),
        'voltage_puh':r.get('ac',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
        'tap_changes':r.get('changed_tap_hour_states'),
        'recalibration':r.get('recalibration',{}).get('status')
    } for r in history]).to_csv(out/'todos_candidatos_ac_v2931.csv',index=False)
    final={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else
               'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V2931',
           'champion':'V2931' if accepted else 'V281_CORRIGIDA_BT0',
           'n_accepted':int(accepted), 'attempts':len(history),
           'recalibrations':recalibrations,
           'diagnostic_events':diagnostic_events,
           'experiments':[{'name':r['strategy'],'status':r['status'],
                           'reasons':r.get('gate_reasons',[]),
                           'error':r.get('error')} for r in history],
           'reference_metrics':inc['metrics'],
           'best_metrics':(history[-1]['ac'] if accepted else inc['metrics']),
           'best_files_verified':not accepted,
           'historical_v293_postrun':v293_postrun,
           'phase1_does_not_certify':True,
           'absolute_AC_certification':False,
           'limitations':['Jacobian local; AC com controles discretos',
             'Sem certificacao de N-1, transformadores, protecao ou amostras estocasticas']}
    v27.write_json(out/'final_report_v2931.json',final)
    print(f'[V2.9.3.1] Resultado: {final["state"]}; '
          f'{int(accepted)} candidato aceito.',flush=True)


def parser():
    p=v292.parser()
    p.description=__doc__
    p.add_argument('--v292-dir',required=True)
    p.add_argument('--v293-dir',required=True)
    p.set_defaults(output='./results/v2931_diagnostico_recuperacao',
                   max_attempts=4,adaptive_recalibration=True,max_recalibrations=1,
                   backtracks=0,max_iterations=1)
    p.add_argument('--no-adaptive-recalibration',dest='adaptive_recalibration',
                   action='store_false',help='Diagnosticar sem recalibracao adaptativa')
    p.add_argument('--max-diagnostic-events',type=int,default=1)
    p.add_argument('--max-diagnostic-solves',type=int,default=4)
    p.add_argument('--diagnostic-time-limit-s',type=int,default=75)
    p.add_argument('--reuse-verified-v293-ac',action='store_true',
                   help='Reutiliza replay anterior apenas se injecoes/politica/scores coincidirem')
    p.add_argument('--no-reuse-verified-v293-ac',dest='reuse_verified_v293_ac',action='store_false')
    p.set_defaults(reuse_verified_v293_ac=True)
    p.add_argument('--verify-only',action='store_true',
                   help='Com --task finalize, audita resultados existentes sem alterar arquivos')
    return p


def main(argv=None):
    p=parser()
    # v292 parser oferece precheck/run; adicionar modo de diagnostico isolado.
    for action in p._actions:
        if action.dest=='task':
            action.choices=tuple(set(action.choices)|{'diagnose','finalize'})
    args=p.parse_args(argv)
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
        if args.task=='finalize':
            from distributed_stochastic.finalizar_v2931 import finalize_existing_run
            result=finalize_existing_run(args.output,args.v281c_dir,args.v293_dir,
                                         verify_only=args.verify_only)
            print(f'[V2.9.3.1] FINALIZE: {result["status"]}; '
                  f'{result["state"]}; AC auditadas={result["ac_checked"]}; '
                  f'V2.9.3={result["v293_reference"]}',flush=True)
            return 0
        if args.verify_only:
            p.error('--verify-only requer --task finalize')
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        elif args.task=='diagnose':diagnose_only(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.9.3.1] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
