#!/usr/bin/env python3
"""V2.5 — busca condicional RegControl + despacho BESS/V2G, com guardas térmicas duras.

Usa as políticas V2.4 JÁ simuladas, recalibra Jacobianos OpenDSS no seu
próprio estado AC, resolve o MILP dos 150 VEs e 3 BESS e valida 24 h em AC.
Relata duas referências: S0 original e S0 com a MESMA política de controle.
Não alega factibilidade quando há violações AC ou restrições de infraestrutura
não verificadas. Todas as saídas são novas; V2.2-2.4 ficam intocadas.
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
from distributed_stochastic import coordenar_controles_v24 as v24
from distributed_stochastic import restaurar_viabilidade_v22 as v22
from distributed_stochastic import restaurar_tensao_v23 as v23
from distributed_stochastic._milp_controles_v25 import make_milp

KEY = ['hour', 'line', 'phase']
EPS = 0.001


def ensure_safe_output(out, prior):
    for p in prior:
        if out == p or out in p.parents or p in out.parents:
            raise ValueError('Pasta de saída deve ser separada das entradas originais')


def detect_new_line_events(s0, ac, guard_margin_pct=0.1, eps=EPS):
    """Guarda linhas que NÃO estavam sobrecarregadas em nenhum horário do S0.

    Para uma linha que se torna sobrecarregada, protege suas fases/horas >= 99.7%
    ao redor do evento. Evita tratar duas seções em série como uma só linha.
    """
    if not 0 <= guard_margin_pct <= 1.0:
        raise ValueError('Margem térmica fora de 0..1 ponto percentual')
    s0df = s0['branches'][KEY + ['loading_pct']]
    acdf = ac['branches'][KEY + ['loading_pct']]
    j = s0df.merge(acdf, on=KEY, how='outer', validate='one_to_one', indicator=True,
                   suffixes=('_s0','_ac'))
    if (j._merge != 'both').any():
        raise ValueError('Referência S0 e candidato AC contêm elementos físicos diferentes')
    old = set(j.loc[j.loading_pct_s0 > 100 + eps, 'line'])
    bad = j.loc[(j.loading_pct_ac > 100 + eps) & (~j.line.isin(old))]
    new_ids = set(bad.line)
    # Guardar também as fases/horas próximas a 100% dessas mesmas seções.
    near = j.loc[j.line.isin(new_ids) & (j.loading_pct_ac >= 99.7)]
    return {tuple(r): 100.0 - guard_margin_pct for r in near[KEY].itertuples(index=False, name=None)}


def guard_report(ac, caps):
    lines = v22.get_indexed(ac['branches'], KEY, 'loading_pct')
    rows = []
    for k, lim in sorted(caps.items()):
        val = float(lines[k]); rows.append({'hour':int(k[0]),'line':k[1], 'phase':k[2],
            'target_pct':float(lim), 'ac_loading_pct':val,
            'above_100_pct':max(0.0,val-100.0), 'exceeds_target_pp':max(0.0,val-lim)})
    return pd.DataFrame(rows, columns=['hour','line','phase','target_pct',
        'ac_loading_pct','above_100_pct','exceeds_target_pp'])


def load_policy_candidates(v24dir, policy_names, original_s0):
    if len(set(policy_names)) != len(policy_names):
        raise ValueError('Políticas duplicadas')
    root = v24dir / 'candidates'
    cases = {}
    for name in policy_names:
        matches = sorted(root.glob('*_' + name))
        if len(matches) != 1:
            raise FileNotFoundError(f'Esperado exatamente um candidato V2.4 para {name}: {matches}')
        folder = matches[0]
        report = json.loads((folder/'report_v24.json').read_text(encoding='utf-8'))
        if report.get('name') != name or report.get('policy',{}).get('name') != name:
            raise ValueError(f'Identidade da política inconsistente: {name}')
        ac = v22.read_ac_folder(folder/'AC')
        quality = v22.score_ac(original_s0, ac)
        for key in ('new_overloaded_lines_vs_s0','thermal_aggravation_samples',
                    'voltage_aggravation_samples'):
            if quality[key] != report['ac_score'][key]:
                raise RuntimeError(f'Arquivos da política {name} inconsistentes ({key})')
        if abs(quality['voltage_aggravation_pu_h'] - report['ac_score']['voltage_aggravation_pu_h']) > 1e-5:
            raise RuntimeError(f'Arquivos de tensão da política {name} inconsistentes')
        cases[name] = dict(policy=report['policy'], ac=ac, origin_report=report,
                           baseline_q_original=quality, folder=folder)
    return cases


def same_policy_s0(repo, baseline, state, inv, cfg, output):
    """S0 sem EVCS/BESS/V2G, sob a política ativa do candidato.

    A comparação com o S0 original continua sendo armazenada separadamente;
    não se considera S0 sob setpoint diferente como uma referência idêntica.
    """
    s0_file = baseline/'S0'/'injections_replayed.csv'
    if not s0_file.is_file():
        raise FileNotFoundError(f'S0 sem DER necessário para comparação: {s0_file}')
    manifest_path = output/'reference_policy_manifest.json'
    cfg_signature = json.dumps(cfg, sort_keys=True)
    if manifest_path.is_file():
        saved = json.loads(manifest_path.read_text(encoding='utf-8'))
        if saved.get('policy_signature') != cfg_signature:
            raise RuntimeError('Referência S0 em cache pertence a outra política. Use pasta nova.')
        ac = v22.read_ac_folder(output/'S0_policy_AC')
        return ac
    profiles = pd.read_csv(s0_file)
    if not {'hour','bus','phase','p_kw','q_kvar'}.issubset(profiles.columns):
        raise ValueError('Perfil S0 incompatível')
    if set(profiles.hour) != set(range(24)):
        raise ValueError('S0 deve incluir todas as horas de 0 a 23')
    ac = v24.evaluate(repo, profiles, state['sites'], inv, cfg, output/'S0_policy_AC')
    if cfg['name'] == 'original':
        v24.compare_replay_full(state['refs']['S0'], ac)
    v22.as_json(manifest_path, {'policy_signature':cfg_signature,
        's0_definition':'NO EVCS, BESS or V2G; 24 hourly automatic controls',
        'interpretation':'S0 sob nova política é comparador adicional, não substitui S0 original'})
    return ac


def compare_and_accept(reference_v23, q, qc, voltage_before, voltage_after,
                       guard_ac, *, max_thermal_growth=.12, min_voltage_reduction=.05,
                       peak_allowance_kw=300.):
    """Regra de entrada na fronteira de interesse; NÃO é certificado de rede."""
    if q['new_overloaded_lines_vs_s0'] != 0:
        return False, 'NEW_LINES_VS_ORIGINAL_S0'
    if qc['new_overloaded_lines_vs_s0'] != 0:
        return False, 'NEW_LINES_VS_SAME_POLICY_S0'
    if not guard_ac.empty and (guard_ac['above_100_pct'] > EPS).any():
        return False, 'TARGETED_LINES_STILL_OVER_100'
    if not guard_ac.empty and (guard_ac['exceeds_target_pp'] > .02).any():
        return False, 'TARGETED_LINES_MARGIN_NOT_MET_IN_AC'
    if q['thermal_aggravation_pp_h'] > reference_v23['thermal_aggravation_pp_h']*(1+max_thermal_growth)+.5:
        return False, 'THERMAL_GROWTH_VS_V23'
    if q['source_peak_kw'] > reference_v23['source_peak_kw'] + peak_allowance_kw:
        return False, 'SOURCE_PEAK_INCREASE'
    if q['strict_pass']:
        return True, 'ABSOLUTE_AC_PASS'
    if q['relative_s0_pass'] and qc['relative_s0_pass']:
        return True, 'BOTH_RELATIVE_AC_PASS_NOT_ABSOLUTE'
    if voltage_after['voltage_rel_puh'] > voltage_before['voltage_rel_puh'] * (1-min_voltage_reduction):
        return False, 'VOLTAGE_GAIN_TOO_SMALL_VS_V23'
    return True, 'PARETO_CANDIDATE_AC_VIOLATIONS_REMAIN'


def pareto(rows):
    """Fronteira AC com métricas verificadas em relação ao S0 original.

    A fronteira inclui propostas não aceitas, claramente rotuladas. O status
    legal/operacional de cada ponto permanece separado.
    """
    metrics=('thermal_original_pph','voltage_original_puh','peak_kw')
    for i, p in enumerate(rows):
        if not all(k in p and pd.notna(p[k]) and np.isfinite(float(p[k])) for k in metrics):
            p['pareto_ac'] = False; continue
        p['pareto_ac'] = not any(j!=i and
            all(k in other and pd.notna(other[k]) and np.isfinite(float(other[k])) for k in metrics) and
            all(float(other[k]) <= float(p[k])+1e-8 for k in metrics) and
            any(float(other[k]) < float(p[k])-1e-7 for k in metrics)
            for j, other in enumerate(rows))
    return rows


def save_best(out, state, ac, policy, q, qc, voltage, source, guards):
    best=out/'best';best.mkdir(parents=True,exist_ok=True)
    v22.write_dispatch(best,state['bess'],state['cars'],state['team']['vehicles'],
                       state['places'],state['cap'])
    state['profiles'].to_csv(best/'injections_reoptimized.csv', index=False)
    for key, df in ac.items():
        (best/'AC').mkdir(parents=True,exist_ok=True)
        df.to_csv(best/'AC'/f'{key}.csv',index=False)
    v22.as_json(best/'ac_score.json',q)
    v22.as_json(best/'ac_score_vs_original_s0.json',q)
    v22.as_json(best/'ac_score_vs_policy_s0.json',qc)
    v22.as_json(best/'control_policy_v25.json',policy)
    v22.as_json(best/'voltage_components_v25.json',voltage)
    v22.as_json(best/'provenance_v25.json',{'source':source,'AC_certification':bool(q['strict_pass']),
        'remaining_absolute_violations':q['strict_voltage_samples']+q['strict_overload_samples']})
    guard_report(ac,guards).to_csv(best/'targeted_line_guards_ac.csv',index=False)


def execute_case(repo, baseline, state, inv, name, case, out, args, ref_q, ref_voltage):
    folder=out/'policies'/name
    folder.mkdir(parents=True,exist_ok=True)
    cfg=case['policy']
    # For cross-policy validity, NO DER S0 must be re-simulated under same controls.
    s0policy=same_policy_s0(repo,baseline,state,inv,cfg,folder)
    base=case['ac']
    baseline_original=case['baseline_q_original']
    baseline_policy=v22.score_ac(s0policy,base)
    guards=detect_new_line_events(state['refs']['S0'],base,args.guard_margin_pct)
    # Also protect lines emerging relative to S0 under the SAME policy.
    guards.update(detect_new_line_events(s0policy,base,args.guard_margin_pct))
    gframe=guard_report(base,guards)
    gframe.to_csv(folder/'initial_hard_guards.csv',index=False)
    v22.as_json(folder/'policy_reference.json',{
        'policy':cfg,'policy_v24_original_s0_score':baseline_original,
        'policy_s0_absolute':v22.score_ac(s0policy,s0policy),
        'policy_v23_operating_vs_same_policy_s0':baseline_policy,
        'policy_original_s0_difference':v22.score_ac(state['refs']['S0'],s0policy),
        'hard_guard_count':len(guards),'interpretation':'Duas referências S0 independentes; sem declarar viabilidade por mudança de setpoint'})
    print(f'[V2.5] Política={name}; guardas duras={len(guards)};'
          f' thermal={baseline_original["thermal_aggravation_pp_h"]:.3f} p.p.-h, '
          f'voltage={baseline_original["voltage_aggravation_pu_h"]:.4f} p.u.-h',flush=True)
    current={'cars':state['cars'].copy(), 'bess':{s:x.copy() for s,x in state['bess'].items()},
             'profiles':state['profiles'].copy(), 'ac':base}
    attempts=[]
    for iteration in range(1,args.max_iterations+1):
        iteration_dir=folder/f'iter_{iteration:02d}'
        iteration_dir.mkdir(exist_ok=True)
        with v24.temporary_control_policy(inv,cfg):
            slopes=v24.calibrate_isolated_v24(repo,current['profiles'],current['ac'],
                  s0policy,state['sites'],args.delta_kw,iteration_dir/'calibration')
        any_accepted=False
        for bt in range(args.backtracks+1):
            trial=iteration_dir/f'trial_{bt:02d}'
            trial.mkdir(exist_ok=True)
            trust_b=args.bess_trust_kw*(0.5**bt)
            trust_v=args.v2g_trust_kw*(0.5**bt)
            try:
                sol=make_milp(state['team'],state['places'],state['cap'],current['bess'],
                  current['cars'],current['ac'],s0policy,slopes,criterion=args.criterion,
                  trust_bess=trust_b,trust_v2g=trust_v,time_limit=args.time_limit_s,
                  mip_gap=args.mip_gap,voltage_weights=v23.PROFILES[args.profile],
                  thermal_budget=float(baseline_policy['thermal_aggravation_pp_h'])*1.12+8.0,
                  hard_line_caps=guards)
                v24.validate_resources(state['team'],sol['bess'],sol['cars'],state['places'],state['cap'])
                trial_profiles=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                     sol['bess'],sol['cars'],state['places'])
                v22.write_dispatch(trial,sol['bess'],sol['cars'],state['team']['vehicles'],
                                    state['places'],state['cap'])
                trial_profiles.to_csv(trial/'injections_reoptimized.csv',index=False)
                ac=v24.evaluate(repo,trial_profiles,state['sites'],inv,cfg,trial/'AC')
                q=v22.score_ac(state['refs']['S0'],ac)
                qc=v22.score_ac(s0policy,ac)
                voltage=v24.voltage_components(state['refs']['S0'],ac)
                g=guard_report(ac,guards)
                g.to_csv(trial/'targeted_line_guards_ac.csv',index=False)
                ok,why=compare_and_accept(ref_q,q,qc,ref_voltage,voltage,g,
                    max_thermal_growth=args.max_thermal_growth,
                    min_voltage_reduction=args.min_voltage_reduction)
                # Operational requirements: no new overload line under either S0.
                ops=v24.control_changes(ac)
                ops_before=v24.control_changes(base)
                if ok and ops['tap_absolute_steps_total'] > ops_before['tap_absolute_steps_total']*1.4 + 12:
                    ok,why=False,'EXCESSIVE_TAP_OPERATIONS'
                if ok and ops['capacitor_switch_events'] > ops_before['capacitor_switch_events']+8:
                    ok,why=False,'EXCESSIVE_CAPACITOR_OPERATIONS'
                report={'status':'AC_ACCEPTED_CANDIDATE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                    'reason':why,'policy':cfg,'ac_original_s0':q,'ac_same_policy_s0':qc,
                    'voltage_components':voltage,'control_operations':ops,
                    'milp_status':sol['solver_message'],'mip_gap':sol['mip_gap'],
                    'milp_slacks':sol['slack_sum'],'hard_guard_count':len(guards),
                    'hard_guard_ac_max_excess_pp':float(g.exceeds_target_pp.max()) if len(g) else 0.,
                    'absolute_ac_pass':bool(q['strict_pass']),
                    'note':'Aprovação para análise Pareto não equivale a certificação AC absoluta'}
                v22.as_json(trial/'report_v25.json',report)
                attempts.append({'policy':name,'iteration':iteration,'backtrack':bt,
                    'status':report['status'],'reason':why,'accepted':ok,
                    'thermal_original_pph':q['thermal_aggravation_pp_h'],
                    'voltage_original_puh':q['voltage_aggravation_pu_h'],
                    'thermal_policy_pph':qc['thermal_aggravation_pp_h'],
                    'voltage_policy_puh':qc['voltage_aggravation_pu_h'],
                    'new_lines_original':q['new_overloaded_lines_vs_s0'],
                    'new_lines_policy':qc['new_overloaded_lines_vs_s0'],
                    'peak_kw':q['source_peak_kw'],'absolute_pass':q['strict_pass'],
                    'relative_original_pass':q['relative_s0_pass'],
                    'relative_policy_pass':qc['relative_s0_pass'],'candidate_folder':str(trial)})
                print(f' [V2.5] {name}, it={iteration} bt={bt}: novas linhas orig/pol='
                      f'{q["new_overloaded_lines_vs_s0"]}/{qc["new_overloaded_lines_vs_s0"]}, '
                      f'térmica={q["thermal_aggravation_pp_h"]:.2f}, '
                      f'tensão={q["voltage_aggravation_pu_h"]:.3f}, aceito={ok}',flush=True)
                if ok:
                    current.update({'cars':sol['cars'],'bess':sol['bess'],
                                   'profiles':trial_profiles,'ac':ac})
                    # The accepted candidate might bring new <100% near-critical keys;
                    # enforce those already in the next MILP only if independently justified.
                    any_accepted=True
                    break
            except Exception as exc:
                v22.as_json(trial/'error_v25.json',{'error':str(exc),
                                  'traceback':traceback.format_exc()})
                attempts.append({'policy':name,'iteration':iteration,'backtrack':bt,
                                 'status':'ERROR','reason':str(exc),
                                 'candidate_folder':str(trial)})
                print(f' [V2.5] Tentativa falhou: {exc}',flush=True)
        if not any_accepted:
            break
    return attempts, current


def run(args):
    repo=Path(args.repo).resolve();baseline=Path(args.baseline).resolve()
    v22_dir=Path(args.v22_dir).resolve();v23_dir=Path(args.v23_dir).resolve()
    v24_dir=Path(args.v24_dir).resolve();out=Path(args.output).resolve()
    ensure_safe_output(out,[v22_dir,v23_dir,v24_dir,repo/'distributed_stochastic',baseline])
    if out.exists() and (out/'final_report_v25.json').exists():
        raise FileExistsError(f'V2.5 já existe em {out}; use --output com outra pasta')
    v24.ensure_paths(repo,baseline,v22_dir,v23_dir,out)
    sys.path[:0]=[str(repo),str(repo/'src'),str(repo/'opendss_rebuild')]
    state=v24.load_state(repo,baseline,v22_dir,v23_dir)
    cases=load_policy_candidates(v24_dir,args.policies.split(','),state['refs']['S0'])
    out.mkdir(parents=True,exist_ok=True)
    inv_file=v24_dir/'inventario_controles_v24.json'
    if not inv_file.is_file():raise FileNotFoundError(inv_file)
    inv=json.loads(inv_file.read_text(encoding='utf-8'))
    # Check that the live circuit control inventory matches the archived one.
    if args.task=='run':
        _,engine=v22.make_engine(repo,state['profiles'],state['sites'])
        if v24.inventory(engine) != inv:
            raise RuntimeError('Inventário V2.4 e OpenDSS local divergentes. Abortar.')
        del engine
    ref_q=state['score'];ref_voltage=v24.voltage_components(state['refs']['S0'],state['ac'])
    summary={'status':'INPUT_VALIDATED','v23_reference':ref_q,
       'v23_voltage':ref_voltage,'policy_names':list(cases),
       'policy_baselines':{k:v['baseline_q_original'] for k,v in cases.items()},
       'number_bess':len(state['cap']), 'number_vehicles':len(state['cars']),
       'scope':'MILP + 24h OpenDSS auto; no automatic certification of infrastructure',
       'two_S0_references':True}
    v22.as_json(out/'precheck_v25.json',summary)
    if args.task=='precheck':
        print('[V2.5] Dados V2.3/V2.4 e políticas validados; ver precheck_v25.json')
        return
    # Preserve um incumbente REAL se nenhum novo AC candidato for aceito.
    if not (out/'best').exists():
        shutil.copytree(v23_dir/'best',out/'best')
        v22.as_json(out/'best'/'control_policy_v25.json', {'name':'original',
          'reg_vreg_offset_v':{},'cap_onoff_offset_v':{}})
        v22.as_json(out/'best'/'provenance_v25.json', {
          'source':'V2.3_original_unchanged','AC_certification':bool(ref_q['strict_pass']),
          'remaining_absolute_violations':ref_q['strict_voltage_samples']+ref_q['strict_overload_samples']})
    rows=[{'policy':'original','iteration':0,'backtrack':-1,'status':'REFERENCE_V23',
         'accepted':True,'reason':'baseline_original_control',
         'thermal_original_pph':ref_q['thermal_aggravation_pp_h'],
         'voltage_original_puh':ref_q['voltage_aggravation_pu_h'],
         'peak_kw':ref_q['source_peak_kw'],'new_lines_original':ref_q['new_overloaded_lines_vs_s0'],
         'absolute_pass':ref_q['strict_pass']}]
    # The top-ranked candidate must be an actual AC accepted result; V2.3 is retained otherwise.
    champion=None; champion_metric=None
    all_results={}
    for name,case in cases.items():
        try:
            attempts,final_state=execute_case(repo,baseline,state,inv,name,case,out,args,ref_q,ref_voltage)
            rows += attempts
            accepted=[t for t in attempts if t.get('accepted')]
            all_results[name]={'attempted':len(attempts),'accepted':len(accepted),
                               'errors':sum(t.get('status')=='ERROR' for t in attempts)}
            if accepted:
                t=accepted[-1];last=Path(t['candidate_folder'])
                # Select the best voltage among the accepted real AC results.
                rank=(float(t['voltage_original_puh']),float(t['thermal_original_pph']),
                      float(t['peak_kw']))
                if champion_metric is None or rank<champion_metric:
                    champion_metric=rank;champion=(name,last,case['policy'])
                    lastac=v22.read_ac_folder(last/'AC')
                    lastq=v22.score_ac(state['refs']['S0'],lastac)
                    pol_s0=v22.read_ac_folder(out/'policies'/name/'S0_policy_AC')
                    lastqp=v22.score_ac(pol_s0,lastac)
                    lastv=v24.voltage_components(state['refs']['S0'],lastac)
                    folder_state=copy.copy(state)
                    bdf=pd.read_csv(last/'bess_dispatch.csv')
                    folder_state['bess']={s:bdf.loc[bdf.site==s].set_index('hour').reindex(range(24)).p_kw.to_numpy(float)
                                          for s in state['cap']}
                    cdf=pd.read_csv(last/'vehicle_dispatch.csv')
                    ids=[v.id for v in state['team']['vehicles']]
                    folder_state['cars']=cdf.pivot(index='vehicle_id',columns='hour',values='p_kw').reindex(
                         index=ids,columns=range(24)).to_numpy(float)
                    folder_state['profiles']=pd.read_csv(last/'injections_reoptimized.csv')
                    hard=detect_new_line_events(state['refs']['S0'],case['ac'],args.guard_margin_pct)
                    hard.update(detect_new_line_events(pol_s0,case['ac'],args.guard_margin_pct))
                    save_best(out,folder_state,lastac,case['policy'],lastq,lastqp,lastv,
                              f'{name}/{last.name}',hard)
        except Exception as exc:
            all_results[name]={'error':str(exc)}
            v22.as_json(out/'policies'/name/'policy_error_v25.json',{
                'error':str(exc),'traceback':traceback.format_exc()})
            print(f'[V2.5] Falha na política {name}: {exc}',flush=True)
    all_rows=pareto(rows)
    pd.DataFrame(all_rows).to_csv(out/'todos_candidatos_ac_v25.csv',index=False)
    pd.DataFrame([r for r in all_rows if r.get('pareto_ac')]).to_csv(
        out/'fronteira_pareto_ac_v25.csv',index=False)
    bestq=v22.score_ac(state['refs']['S0'],v22.read_ac_folder(out/'best'/'AC')) if champion else ref_q
    classification=('ABSOLUTE_AC_PASS' if bestq['strict_pass'] else
                   'RELATIVE_S0_AC_PASS_NOT_ABSOLUTE' if bestq['relative_s0_pass'] else
                   'AC_VIOLATIONS_REMAIN')
    v22.as_json(out/'final_report_v25.json',{
        'state':classification,'champion':champion[0] if champion else 'v23_reference_unchanged',
        'champion_source':str(champion[1]) if champion else str(v23_dir/'best'),
        'accepted_ac_candidates':sum(r.get('accepted') is True and r.get('iteration',0)>0 for r in rows),
        'best_ac_vs_original_s0':bestq,'case_status':all_results,
        'policy_specific_s0_evaluated':True,
        'warning':'MILP slacks and local Jacobian are NOT AC feasibility. Original IEEE 8500 itself violates bounds. No hardware/network/uncertainty certification.',
        'unverified':['Transformer ratings','charger bidirectionality and capacity','protections','low-voltage service','N-1','stochastic robustness']})
    print('[V2.5] Relatório:',out/'final_report_v25.json',flush=True)
    if champion is None:
        print('[V2.5] Nenhuma proposta aceita; V2.3 preservada. NÃO declarar viabilidade AC.',flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v22-dir',required=True)
    p.add_argument('--v23-dir',required=True)
    p.add_argument('--v24-dir',required=True)
    p.add_argument('--output',default='results/v25_cootimizacao')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--policies',default='fase_C_reduzida,vreg4_A_C',
                   help='Políticas existentes nos resultados do sweep da V2.4, separadas por vírgulas')
    p.add_argument('--criterion',choices=('relative','strict'),default='relative')
    p.add_argument('--profile',choices=('under','balanced','over'),default='balanced')
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--max-iterations',type=int,default=1)
    p.add_argument('--backtracks',type=int,default=1)
    p.add_argument('--bess-trust-kw',type=float,default=150.)
    p.add_argument('--v2g-trust-kw',type=float,default=100.)
    p.add_argument('--guard-margin-pct',type=float,default=.10)
    p.add_argument('--max-thermal-growth',type=float,default=.12)
    p.add_argument('--min-voltage-reduction',type=float,default=.05)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    args=p.parse_args(argv)
    args.policies=','.join(x.strip() for x in args.policies.split(',') if x.strip())
    if not args.policies or args.max_iterations<1 or args.backtracks<0 or args.delta_kw<=0 or args.bess_trust_kw<=0 or args.v2g_trust_kw<=0 or args.time_limit_s<=0 or not 0<args.mip_gap<1 or not 0<=args.guard_margin_pct<=1. or not 0<=args.max_thermal_growth<=1 or not 0<=args.min_voltage_reduction<1:
        p.error('Parâmetros inválidos')
    run(args)


if __name__=='__main__':main()
