#!/usr/bin/env python3
"""V2.6 — mitigacao intertemporal de sobrecargas IEEE 8500 / BESS + V2G.

Parte EXCLUSIVAMENTE da melhor operacao V2.5 na politica fase_C_reduzida.
Reotimiza por linha-fase-hora com cortes AC reais; cada candidato e novamente
resolvido em OpenDSS por 24 h. Folgas MILP jamais certificam operabilidade.
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
from distributed_stochastic import restaurar_viabilidade_v22 as v22
from distributed_stochastic import restaurar_tensao_v23 as v23
from distributed_stochastic import coordenar_controles_v24 as v24
from distributed_stochastic import cootimizar_controles_v25 as v25
from distributed_stochastic._milp_temporal_v26 import make_milp

KEY = ('hour', 'line', 'phase')
PRIMARY_LINE = 'ln6351510-1'
PRIMARY_PHASE = 'C'
ANCHOR_HOURS = (8, 15, 23)


def paired_thermal(s0, ac):
    """Uma linha/fase/hora por amostra; nao confunde IDs de linhas em serie."""
    a = s0['branches'][list(KEY) + ['loading_pct']]
    b = ac['branches'][list(KEY) + ['loading_pct']]
    j = a.merge(b, on=list(KEY), suffixes=('_s0', '_ac'), how='outer',
                validate='one_to_one', indicator=True)
    if (j['_merge'] != 'both').any() or set(j.hour) != set(range(24)):
        raise RuntimeError('Diferença de topologia/horas entre S0 política e S4')
    j = j.drop(columns='_merge')
    j['reference_limit_pct'] = np.maximum(100., j.loading_pct_s0)
    j['over_100_pp'] = np.maximum(0., j.loading_pct_ac - 100.)
    j['aggravation_pp'] = np.maximum(0., j.loading_pct_ac - j.reference_limit_pct)
    return j


def temporal_targets(s0, ac, *, margin_pct=.10, top_k=24):
    """Todos os horarios criticos da linha principal + outros eventos top-K.

    Criterio: corte relativa de corrente sem somar ramos com nomes semelhantes.
    S0 ja sobrecarregado -> alvo nao agravamento; caso contrario -> 100-margem.
    """
    if not 0. <= margin_pct <= 1. or top_k < 0:
        raise ValueError('Margem ou numero de cortes invalido')
    j = paired_thermal(s0, ac)
    hot = j.loc[j.aggravation_pp > 1e-3].copy()
    focus = hot.loc[(hot.line == PRIMARY_LINE) & (hot.phase == PRIMARY_PHASE)]
    others = hot.loc[~((hot.line == PRIMARY_LINE) & (hot.phase == PRIMARY_PHASE))]
    others = others.sort_values(['aggravation_pp', 'hour'], ascending=[False, True]).head(top_k)
    selected = pd.concat([focus, others], ignore_index=True)
    selected['target_pct'] = np.where(selected.loading_pct_s0 <= 100.,
                                      100. - margin_pct, selected.loading_pct_s0)
    selected['priority'] = np.where((selected.line == PRIMARY_LINE) &
                                     (selected.phase == PRIMARY_PHASE), 'focus_24h', 'other_hotspot')
    return selected.sort_values(['priority','aggravation_pp','hour'],
                                ascending=[False,False,True]).reset_index(drop=True)


def directed_cuts(targets, *, strategy='anchors3', soft_weight=2500., keep_guards=None):
    """Cortes obrigatorios nas ancoras, brandos nos demais horarios.

    Estrategia 'soft' dispensa cortes obrigatorios NAS ANCORAS, mas mantem guardas
    que ja estavam resolvidas na V2.5.
    """
    if strategy not in ('anchors3', 'anchors5', 'soft'):
        raise ValueError(f'Estrategia desconhecida: {strategy}')
    if not np.isfinite(soft_weight) or soft_weight <= 0:
        raise ValueError('Peso temporal invalido')
    hard = {} if keep_guards is None else dict(keep_guards)
    soft = {}
    for r in targets.itertuples(index=False):
        key = (int(r.hour), str(r.line), str(r.phase))
        target = float(r.target_pct)
        anchor = (r.line == PRIMARY_LINE and r.phase == PRIMARY_PHASE and
                  ((r.hour in ANCHOR_HOURS and strategy == 'anchors3') or
                   (r.hour in (8, 14, 15, 21, 23) and strategy == 'anchors5')))
        if anchor:
            # Entre as ancoras, o S0 original nao sobrecarregou a linha nestas horas.
            if r.loading_pct_s0 > 100.:
                raise ValueError(f'Ancora {key} ja sobrecarregada no S0 da politica')
            hard[key] = min(hard.get(key, target), target)
        elif key not in hard:
            weight = soft_weight if r.line == PRIMARY_LINE else soft_weight * .50
            soft[key] = (target, weight)
    return hard, soft


def old_v25_guards(v25_best):
    path = v25_best/'targeted_line_guards_ac.csv'
    if not path.is_file():
        raise FileNotFoundError(f'Guardas herdadas da V2.5: {path}')
    frame = pd.read_csv(path)
    need = set(KEY)|{'target_pct','ac_loading_pct'}
    if not need.issubset(frame.columns) or frame.duplicated(list(KEY)).any():
        raise RuntimeError('Guardas V2.5 ausentes, incompletas ou duplicadas')
    if (frame['ac_loading_pct'] > 100.001).any():
        raise RuntimeError('Uma guarda V2.5 ainda esta acima de 100%, revisar os resultados')
    return {(int(r.hour), str(r.line), str(r.phase)): float(r.target_pct)
            for r in frame.itertuples(index=False)}


def load_v25_state(repo, baseline, v22_dir, v23_dir, v25_dir):
    state = v24.load_state(repo, baseline, v22_dir, v23_dir)
    best = v25_dir/'best'
    for f in ['control_policy_v25.json', 'ac_score_vs_policy_s0.json',
              'ac_score_vs_original_s0.json', 'bess_dispatch.csv',
              'vehicle_dispatch.csv', 'injections_reoptimized.csv']:
        if not (best/f).is_file():
            raise FileNotFoundError(best/f)
    policy = json.loads((best/'control_policy_v25.json').read_text(encoding='utf-8'))
    if policy.get('name') != 'fase_C_reduzida':
        raise ValueError('A V2.6 foi calibrada para fase_C_reduzida; V2.5 usa outra politica')
    refdir = v25_dir/'policies'/policy['name']
    manfile = refdir/'reference_policy_manifest.json'
    if not manfile.is_file():
        raise FileNotFoundError(manfile)
    manifest = json.loads(manfile.read_text(encoding='utf-8'))
    if manifest.get('policy_signature') != json.dumps(policy, sort_keys=True):
        raise RuntimeError('S0 sob outra politica de regulação — nao comparar')
    state['bess'] = v24.read_bess(best, state)
    state['cars'] = v24.read_cars(best, state)
    v24.validate_resources(state['team'], state['bess'], state['cars'],state['places'],state['cap'])
    regen = state['build_injections'](state['s1'], state['team']['old_fleet_profile'],
                                      state['bess'], state['cars'], state['places'])
    saved = pd.read_csv(best/'injections_reoptimized.csv')
    k = ['hour','bus','phase']
    for df in (regen, saved):
        df['bus'] = df.bus.astype(str).str.lower()
        df['phase'] = df.phase.astype(str).str.upper()
    j = regen.merge(saved,on=k,suffixes=('_new','_old'),how='outer',
                    validate='one_to_one', indicator=True)
    if (j._merge != 'both').any() or any((j[c+'_new']-j[c+'_old']).abs().max() > 1e-3
                                     for c in ('p_kw','q_kvar')):
        raise RuntimeError('Injeções V2.5 não reproduzidas. Não iniciar V2.6.')
    state['profiles'] = regen
    state['ac'] = v22.read_ac_folder(best/'AC')
    state['s0policy'] = v22.read_ac_folder(refdir/'S0_policy_AC')
    state['score_original'] = v22.score_ac(state['refs']['S0'], state['ac'])
    state['score_policy'] = v22.score_ac(state['s0policy'], state['ac'])
    for filename, computed in (('ac_score_vs_policy_s0.json', state['score_policy']),
                               ('ac_score_vs_original_s0.json', state['score_original'])):
        recorded = json.loads((best/filename).read_text(encoding='utf-8'))
        for key in ('strict_overload_samples','strict_voltage_samples',
                    'thermal_aggravation_samples','voltage_aggravation_samples',
                    'new_overloaded_lines_vs_s0'):
            if computed[key] != recorded[key]:
                raise RuntimeError(f'V2.5 AC inconsistente em {filename}:{key}')
        for key in ('thermal_aggravation_pp_h','voltage_aggravation_pu_h'):
            if abs(computed[key]-recorded[key]) > 1e-5:
                raise RuntimeError(f'Severidade V2.5 AC inconsistente em {filename}:{key}')
    state['policy'] = policy
    state['retained_guards'] = old_v25_guards(best)
    return state


def temporal_assessment(s0pol, ac, *, old_ac=None):
    j = paired_thermal(s0pol, ac)
    focus = j[(j.line == PRIMARY_LINE) & (j.phase == PRIMARY_PHASE)].sort_values('hour')
    if len(focus) != 24:
        raise RuntimeError('A linha critica nao foi encontrada em todas as 24 horas')
    result = {'critical_line_phase':f'{PRIMARY_LINE}/{PRIMARY_PHASE}',
        'critical_line_overload_hours': [int(x) for x in focus.loc[focus.over_100_pp>1e-3, 'hour']],
        'critical_line_absolute_overload_pph':float(focus.over_100_pp.sum()),
        'critical_line_aggravation_pph':float(focus.aggravation_pp.sum()),
        'critical_line_max_loading_pct':float(focus.loading_pct_ac.max()),
        'thermal_total_aggravation_pph':float(j.aggravation_pp.sum()),
        'over_100_samples':int((j.loading_pct_ac > 100.001).sum())}
    if old_ac is not None:
        b = temporal_assessment(s0pol, old_ac)
        # V26_TUPLE_FIX: temporal_assessment pode retornar (resumo_dict, tabela).
        if isinstance(b, tuple):
            if not b or not isinstance(b[0], dict):
                raise TypeError("V2.6: primeiro item do retorno temporal deve ser dict")
            b = b[0]
        elif not isinstance(b, dict):
            raise TypeError(f"V2.6: resumo temporal invalido: {type(b).__name__}")
        result['critical_line_gain_pph'] = b['critical_line_aggravation_pph'] - result['critical_line_aggravation_pph']
    return result, j


def accept_candidate(base_orig, base_pol, qorig, qpol, old_temporal, new_temporal,
                     *, min_thermal_gain=.01, voltage_allowance=.15, peak_allowance=200.,
                     max_loading_allowance=.25, original_thermal_allowance=.5,
                     control_old=None, control_new=None, retained_hard=None, ac=None):
    """Guarda multiobjetivo AC. Nao certifica a rede, mesmo quando aceita."""
    if qorig['new_overloaded_lines_vs_s0'] > 0:
        return False,'NEW_LINES_VS_ORIGINAL_S0'
    if qpol['new_overloaded_lines_vs_s0'] > 0:
        return False,'NEW_LINES_VS_SAME_POLICY_S0'
    if qpol['thermal_aggravation_pp_h'] > base_pol['thermal_aggravation_pp_h'] * (1-min_thermal_gain):
        return False,'INSUFFICIENT_TEMPORAL_THERMAL_GAIN'
    if qorig['thermal_aggravation_pp_h'] > base_orig['thermal_aggravation_pp_h'] + original_thermal_allowance:
        return False,'THERMAL_REGRESSION_VS_ORIGINAL_S0'
    if qpol['voltage_aggravation_pu_h'] > base_pol['voltage_aggravation_pu_h'] + voltage_allowance:
        return False,'VOLTAGE_REGRESSION_VS_POLICY_S0'
    if qorig['voltage_aggravation_pu_h'] > base_orig['voltage_aggravation_pu_h'] + voltage_allowance:
        return False,'VOLTAGE_REGRESSION_VS_ORIGINAL_S0'
    if qpol['max_loading_pct'] > base_pol['max_loading_pct'] + max_loading_allowance:
        return False,'NEW_GLOBAL_MAX_LOADING'
    if qpol['source_peak_kw'] > base_pol['source_peak_kw'] + peak_allowance:
        return False,'SOURCE_PEAK_REGRESSION'
    if new_temporal['critical_line_aggravation_pph'] >= old_temporal['critical_line_aggravation_pph'] - 1e-3:
        return False,'FOCUS_LINE_NOT_IMPROVED'
    if retained_hard:
        if ac is None:
            raise ValueError('AC faltando ao testar guardas herdadas')
        observed = v25.guard_report(ac, retained_hard)
        if ((observed.ac_loading_pct > 100.001) | (observed.exceeds_target_pp > .02)).any():
            return False,'PREVIOUSLY_FIXED_OVERLOAD_REOPENED'
    if control_old is not None and control_new is not None:
        if control_new['tap_absolute_steps_total'] > control_old['tap_absolute_steps_total']*1.4+12:
            return False,'EXCESSIVE_TAP_OPERATIONS'
        if control_new['capacitor_switch_events'] > control_old['capacitor_switch_events']+8:
            return False,'EXCESSIVE_CAPACITOR_OPERATIONS'
    return True,('ABSOLUTE_AC_PASS' if qorig['strict_pass'] else
                 'CANDIDATE_IMPROVED_AC_NOT_ABSOLUTELY_FEASIBLE')


def output_best(out, folder, policy, qoriginal, qpolicy, temporal, source):
    # Prepara novo champion sem tocar no diretorio V2.5 do usuario.
    candidate = out/'_best_next'
    if candidate.exists():shutil.rmtree(candidate)
    shutil.copytree(folder, candidate)
    v22.as_json(candidate/'ac_score.json', qoriginal)
    v22.as_json(candidate/'ac_score_vs_original_s0.json', qoriginal)
    v22.as_json(candidate/'ac_score_vs_policy_s0.json', qpolicy)
    v22.as_json(candidate/'control_policy_v26.json', policy)
    v22.as_json(candidate/'temporal_assessment_v26.json', temporal)
    v22.as_json(candidate/'provenance_v26.json', {'source':source,
        'absolute_ac_pass':bool(qoriginal['strict_pass']),
        'relative_original_s0_pass':bool(qoriginal['relative_s0_pass']),
        'relative_policy_s0_pass':bool(qpolicy['relative_s0_pass']),
        'scope':'24h AC, not network/infrastructure/N-1/uncertainty certification'})
    best = out/'best'
    if best.exists():shutil.rmtree(best)
    candidate.rename(best)


def run(args):
    repo=Path(args.repo).resolve(); baseline=Path(args.baseline).resolve()
    v22dir=Path(args.v22_dir).resolve();v23dir=Path(args.v23_dir).resolve()
    v24dir=Path(args.v24_dir).resolve();v25dir=Path(args.v25_dir).resolve()
    out=Path(args.output).resolve()
    v25.ensure_safe_output(out,[v22dir,v23dir,v24dir,v25dir,baseline,repo/'distributed_stochastic'])
    if args.task=='run' and (out/'final_report_v26.json').exists():
        raise FileExistsError('A V2.6 ja foi executada nessa pasta. Escolha nova pasta --output.')
    v24.ensure_paths(repo,baseline,v22dir,v23dir,out)
    for f in (v24dir/'inventario_controles_v24.json',v25dir/'final_report_v25.json'):
        if not f.is_file():raise FileNotFoundError(f)
    sys.path[:0]=[str(repo),str(repo/'src'),str(repo/'opendss_rebuild')]
    state=load_v25_state(repo,baseline,v22dir,v23dir,v25dir)
    inv=json.loads((v24dir/'inventario_controles_v24.json').read_text(encoding='utf-8'))
    targets=temporal_targets(state['s0policy'],state['ac'],margin_pct=args.margin_pct,top_k=args.top_k)
    assess0, paired=temporal_assessment(state['s0policy'],state['ac'])
    out.mkdir(parents=True,exist_ok=True)
    targets.to_csv(out/'targets_temporais_v26.csv',index=False)
    paired.to_csv(out/'linha_fase_hora_v25_vs_S0_politica.csv',index=False)
    v22.as_json(out/'precheck_v26.json',{
        'state':'V25_INPUT_VALIDATED', 'policy':state['policy'],
        'n_bess':len(state['cap']), 'n_vehicles':len(state['cars']),
        'sites_bess':sorted(state['cap']),'sites_v2g':sorted(set(state['places'])),
        'v25_original_s0':state['score_original'], 'v25_policy_s0':state['score_policy'],
        'v25_temporal':assess0,'retained_hard_line_keys':len(state['retained_guards']),
        'temporal_target_keys':len(targets),
        'warning':'S0 ja pode violar a rede; V2.6 nao certifica N-1, protecoes ou rede BT.'})
    if args.task == 'precheck':
        print('[V2.6] V2.5 reproduzida; foco temporal carregado; ver precheck_v26.json')
        print(f'[V2.6] Linha critica: {assess0["critical_line_aggravation_pph"]:.3f} p.p.-h;'
              f' alvos temporais={len(targets)}; guardas herdadas={len(state["retained_guards"])}')
        return
    _, engine = v22.make_engine(repo,state['profiles'],state['sites'])
    if v24.inventory(engine) != inv:
        raise RuntimeError('Inventario OpenDSS alterado em relacao a V2.4; abortar')
    del engine
    if not (out/'best').exists():
        shutil.copytree(v25dir/'best',out/'best')
        v22.as_json(out/'best'/'provenance_v26.json',{
            'source':'V2.5_unchanged','absolute_ac_pass':state['score_original']['strict_pass'],
            'retained_due_to':'No V2.6 accepted candidate yet'})
    base_original=state['score_original'];base_policy=state['score_policy']
    base_controls=v24.control_changes(state['ac'])
    best_temporal=assess0
    best_orig=base_original;best_pol=base_policy
    champion='v25_unchanged'
    all_rows=[{'status':'REFERENCE_V25','accepted':True,'strategy':'incumbent','iteration':0,
        'thermal_policy_pph':base_policy['thermal_aggravation_pp_h'],
        'thermal_original_pph':base_original['thermal_aggravation_pp_h'],
        'voltage_policy_puh':base_policy['voltage_aggravation_pu_h'],
        'voltage_original_puh':base_original['voltage_aggravation_pu_h'],
        'focus_line_pph':assess0['critical_line_aggravation_pph'],
        'peak_kw':base_policy['source_peak_kw'],'new_lines_policy':0,
        'new_lines_original':0,'strict_pass':base_original['strict_pass']}]
    current = {k:copy.deepcopy(state[k]) for k in ('cars','bess','profiles')}
    current['ac']=state['ac']
    min_gain=args.min_thermal_gain
    for iteration in range(1,args.max_iterations+1):
        idir=out/f'iter_{iteration:02d}';idir.mkdir(exist_ok=True)
        print(f'[V2.6] Iteracao {iteration}/{args.max_iterations}: calibracao OpenDSS cronologica...',flush=True)
        with v24.temporary_control_policy(inv,state['policy']):
            slopes=v24.calibrate_isolated_v24(repo,current['profiles'],current['ac'],
                     state['s0policy'],state['sites'],args.delta_kw,idir/'calibration')
        local_targets=temporal_targets(state['s0policy'],current['ac'],
                                      margin_pct=args.margin_pct, top_k=args.top_k)
        local_targets.to_csv(idir/'targets_temporais.csv',index=False)
        winner=None; winner_rank=None
        for strategy in args.strategies:
            for bt in range(args.backtracks+1):
                trial=idir/f'{strategy}_bt{bt}'
                trial.mkdir(exist_ok=True)
                try:
                    hard,soft=directed_cuts(local_targets,strategy=strategy,
                        soft_weight=args.soft_weight,keep_guards=state['retained_guards'])
                    # Sem hard cuts infeasiveis como 110% em todos os horarios;
                    # as ancoras e as guardas herdadas ficam realmente duras.
                    sol=make_milp(state['team'],state['places'],state['cap'],
                        current['bess'],current['cars'],current['ac'],state['s0policy'],slopes,
                        criterion='relative',trust_bess=args.bess_trust_kw*(.5**bt),
                        trust_v2g=args.v2g_trust_kw*(.5**bt),time_limit=args.time_limit_s,
                        mip_gap=args.mip_gap,voltage_weights=v23.PROFILES[args.profile],
                        thermal_budget=best_pol['thermal_aggravation_pp_h']*1.20+12,
                        hard_line_caps=hard,temporal_soft_caps=soft)
                    v24.validate_resources(state['team'],sol['bess'],sol['cars'],
                                           state['places'],state['cap'])
                    injections=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                                       sol['bess'],sol['cars'],state['places'])
                    v22.write_dispatch(trial,sol['bess'],sol['cars'],state['team']['vehicles'],
                                       state['places'],state['cap'])
                    injections.to_csv(trial/'injections_reoptimized.csv',index=False)
                    ac=v24.evaluate(repo,injections,state['sites'],inv,state['policy'],trial/'AC')
                    qo=v22.score_ac(state['refs']['S0'],ac)
                    qp=v22.score_ac(state['s0policy'],ac)
                    focus, phase_table=temporal_assessment(state['s0policy'],ac,old_ac=state['ac'])
                    phase_table.to_csv(trial/'paired_thermal_24h.csv',index=False)
                    op=v24.control_changes(ac)
                    ok,why=accept_candidate(best_orig,best_pol,qo,qp,best_temporal,focus,
                        min_thermal_gain=min_gain,voltage_allowance=args.voltage_allowance_puh,
                        peak_allowance=args.peak_allowance_kw,
                        original_thermal_allowance=args.original_thermal_allowance_pph,
                        control_old=base_controls,control_new=op,
                        retained_hard=hard,ac=ac)
                    # Aprovacao no AC: as tres ancoras tambem precisam da margem
                    # predefinida, nao apenas ficar abaixo de 100%.
                    guards_frame=v25.guard_report(ac,hard)
                    guards_frame.to_csv(trial/'guardas_temporais_ac.csv',index=False)
                    if ok and (guards_frame.exceeds_target_pp > .02).any():
                        ok,why=False,'HARD_TEMPORAL_TARGET_NOT_MET_IN_AC'
                    report={'status':'AC_ACCEPTED_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                        'reason':why,'policy':state['policy'],
                        'vs_original_s0':qo,'vs_policy_s0':qp,
                        'focus_temporal':focus,'control_operations':op,
                        'milp':{'message':sol['solver_message'],'gap':sol['mip_gap'],
                            'stats':sol['model_stats'],'objective':sol['objective'],
                            'hard_keys':sol['hard_line_caps'],'soft_keys':sol['soft_temporal_caps'],
                            'soft_slack_pp':sol['soft_temporal_slack_pp'],
                            'soft_cost':sol['soft_temporal_cost']},
                        'absolute_ac_pass':qo['strict_pass'],
                        'relative_original_s0_pass':qo['relative_s0_pass'],
                        'relative_policy_s0_pass':qp['relative_s0_pass']}
                    v22.as_json(trial/'report_v26.json',report)
                    row={'iteration':iteration,'strategy':strategy,'backtrack':bt,
                        'accepted':ok,'status':report['status'],'reason':why,
                        'thermal_policy_pph':qp['thermal_aggravation_pp_h'],
                        'thermal_original_pph':qo['thermal_aggravation_pp_h'],
                        'voltage_policy_puh':qp['voltage_aggravation_pu_h'],
                        'voltage_original_puh':qo['voltage_aggravation_pu_h'],
                        'focus_line_pph':focus['critical_line_aggravation_pph'],
                        'peak_kw':qp['source_peak_kw'],
                        'max_loading_pct':qp['max_loading_pct'],
                        'new_lines_policy':qp['new_overloaded_lines_vs_s0'],
                        'new_lines_original':qo['new_overloaded_lines_vs_s0'],
                        'strict_pass':qo['strict_pass'],'solver_gap':sol['mip_gap'],
                        'folder':str(trial)}
                    all_rows.append(row)
                    print(f' [V2.6] {strategy} bt{bt}: thermal={qp["thermal_aggravation_pp_h"]:.2f}, '
                          f'focus={focus["critical_line_aggravation_pph"]:.2f}, '
                          f'voltage={qp["voltage_aggravation_pu_h"]:.3f}, accepted={ok} ({why})',flush=True)
                    rank=(qp['thermal_aggravation_pp_h'] +
                          30.*qp['voltage_aggravation_pu_h'],
                          qp['max_loading_pct'],qp['source_peak_kw'])
                    if ok and (winner_rank is None or rank < winner_rank):
                        winner_rank=rank
                        winner=(trial,sol,injections,ac,qo,qp,focus,row)
                except Exception as exc:
                    error={'strategy':strategy,'backtrack':bt,'error':str(exc),
                           'traceback':traceback.format_exc()}
                    v22.as_json(trial/'error_v26.json',error)
                    all_rows.append({'iteration':iteration,'strategy':strategy,
                        'backtrack':bt,'accepted':False,'status':'ERROR','reason':str(exc),
                        'folder':str(trial)})
                    print(f' [V2.6] {strategy} bt{bt}: erro: {exc}',flush=True)
        if winner is None:
            print('[V2.6] Nenhuma melhoria AC aceita nesta iteracao.',flush=True)
            break
        trial,sol,inj,ac,qo,qp,focus,row=winner
        champion=f'iter_{iteration:02d}/{trial.name}'
        output_best(out,trial,state['policy'],qo,qp,focus,champion)
        current.update({'bess':sol['bess'],'cars':sol['cars'],'profiles':inj,'ac':ac})
        best_orig,best_pol,best_temporal=qo,qp,focus
        base_controls=v24.control_changes(ac)
        print(f'[V2.6] Novo melhor AC: {champion}',flush=True)
    # Fronteira de Pareto com todos os candidatos AC; nao equivale a certificacao.
    # Do not discard the status/accepted flags or include failed solves in pareto.
    all_rows=v25.pareto(all_rows)
    pd.DataFrame(all_rows).to_csv(out/'todos_candidatos_ac_v26.csv',index=False)
    pd.DataFrame([r for r in all_rows if r.get('pareto_ac')]).to_csv(
        out/'fronteira_pareto_ac_v26.csv',index=False)
    classification=('ABSOLUTE_AC_PASS' if best_orig['strict_pass'] else
        'BOTH_RELATIVE_S0_PASS_NOT_ABSOLUTE' if best_orig['relative_s0_pass'] and best_pol['relative_s0_pass']
        else 'AC_VIOLATIONS_REMAIN')
    final={'state':classification,'champion':champion,'accepted_ac_candidates':sum(
        r.get('accepted',False) and r.get('iteration',0)>0 for r in all_rows),
        'v25_baseline_original':base_original,'v25_baseline_same_policy':base_policy,
        'best_vs_original_s0':best_orig,'best_vs_policy_s0':best_pol,
        'v25_focus_temporal':assess0,'best_focus_temporal':best_temporal,
        'policy':state['policy'],'n_vehicle':len(state['cars']),
        'warning':'Aceitacao AC nao e certificacao eletrica. A rede S0 tem violacoes. '
        'Limites de transformadores/protecao, capacidade de carregadores, N-1 e estocasticidade nao certificados.',
        'unverified':['Transformer ratings','charger bidirectionality','LV feeders',
                      'protection and harmonics','N-1','stochastic out-of-sample']}
    v22.as_json(out/'final_report_v26.json',final)
    print('[V2.6] Relatorio:',out/'final_report_v26.json',flush=True)
    if champion=='v25_unchanged':
        print('[V2.6] V2.5 PRESERVADA: nao houve melhoria AC aceita.',flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v22-dir',required=True)
    p.add_argument('--v23-dir',required=True)
    p.add_argument('--v24-dir',required=True)
    p.add_argument('--v25-dir',required=True)
    p.add_argument('--output',default='results/v26_temporal')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--strategies',default='anchors3,soft',
                   help='anchors3, anchors5 e/ou soft; separadas por virgula')
    p.add_argument('--profile',choices=('balanced','under','over'),default='balanced')
    p.add_argument('--max-iterations',type=int,default=1)
    p.add_argument('--backtracks',type=int,default=0)
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--bess-trust-kw',type=float,default=300.)
    p.add_argument('--v2g-trust-kw',type=float,default=150.)
    p.add_argument('--margin-pct',type=float,default=.10)
    p.add_argument('--top-k',type=int,default=24)
    p.add_argument('--soft-weight',type=float,default=2500.)
    p.add_argument('--min-thermal-gain',type=float,default=.01)
    p.add_argument('--voltage-allowance-puh',type=float,default=.15)
    p.add_argument('--original-thermal-allowance-pph',type=float,default=.5)
    p.add_argument('--peak-allowance-kw',type=float,default=200.)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    args=p.parse_args(argv)
    args.strategies=[x.strip() for x in args.strategies.split(',') if x.strip()]
    if (not args.strategies or len(args.strategies)!=len(set(args.strategies)) or
        not set(args.strategies).issubset({'anchors3','anchors5','soft'}) or
        args.max_iterations<1 or args.backtracks<0 or args.delta_kw<=0 or
        args.bess_trust_kw<=0 or args.v2g_trust_kw<=0 or args.time_limit_s<=0 or
        not (0<args.mip_gap<1) or not 0<=args.margin_pct<=1 or args.top_k<0 or
        args.soft_weight<=0 or not 0<=args.min_thermal_gain<1 or
        args.voltage_allowance_puh<0 or args.original_thermal_allowance_pph<0 or
        args.peak_allowance_kw<0):
        p.error('Parametro de otimizacao invalido')
    run(args)


if __name__ == '__main__':
    main()
