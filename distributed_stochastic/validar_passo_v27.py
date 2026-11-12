#!/usr/bin/env python3
"""V2.7: busca de passo (BESS/V2G) com replays AC e salvaguardas.

Evolucao conservadora da V2.6: combina despachos FISICAMENTE VALIDADOS da
V2.5 e candidatos V2.6, testa recursos de cada combinacao e reexecuta as 24 h
no OpenDSS. Nao afirma ter acrescentado restricoes ao MILP V2.6 nem entrega
certificacao de operabilidade absoluta. Nunca altera resultados anteriores.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import sys
import traceback
from typing import Any

import numpy as np
import pandas as pd

KEY = ('hour', 'line', 'phase')
EPS = 1.0e-3
REQUIRED_BASE = ('hour', 'site', 'p_kw')
REQUIRED_EV = ('vehicle_id', 'site', 'hour', 'p_kw')


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'JSON deve ser objeto: {path}')
    return value


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def parse_alphas(text: str) -> list[float]:
    vals = []
    for s in text.split(','):
        try:
            x = float(s.strip())
        except ValueError as exc:
            raise ValueError('Os alphas devem ser decimais entre 0 e 1') from exc
        if not np.isfinite(x) or not 0 < x <= 1:
            raise ValueError('alpha deve estar em (0,1]')
        if x not in vals:
            vals.append(x)
    if not vals:
        raise ValueError('Informe ao menos um alpha')
    return vals


def blend_schedules(base_bess: dict, base_cars: np.ndarray,
                    trial_bess: dict, trial_cars: np.ndarray, alpha: float):
    if not 0 < alpha <= 1:
        raise ValueError('alpha invalido')
    if set(base_bess) != set(trial_bess):
        raise ValueError('Sites BESS diferentes')
    b = {}
    for site in base_bess:
        u, v = np.asarray(base_bess[site], float), np.asarray(trial_bess[site], float)
        if u.shape != (24,) or v.shape != (24,) or not np.isfinite(u).all() or not np.isfinite(v).all():
            raise ValueError('Formato ou valores BESS invalidos')
        b[site] = (1 - alpha)*u + alpha*v
    u, v = np.asarray(base_cars, float), np.asarray(trial_cars, float)
    if u.shape != (150,24) or v.shape != (150,24) or not np.isfinite(u).all() or not np.isfinite(v).all():
        raise ValueError('Formato V2G deve ser 150 x 24 com dados finitos')
    return b, (1 - alpha)*u + alpha*v


def repair_bess_soc(bess: dict, cap: dict, max_correction_kw: float = 15.):
    """LP de pequeno porte: repara SOC dos BESS apos a combinacao convexa.

    O saldo energetico nao e exatamente linear em p neto ao misturar carga e
    descarga de sinais diferentes. Mantem a direcao de cada hora, usa a
    eficiencia declarada na V2.4, limites de SOC 10..90%, terminal 30%,
    sem recarga 17--20 e minimo desvio L1 no despacho em kW.
    O reparo nao substitui validate_resources nem o replay AC.
    """
    from scipy.optimize import linprog
    if not np.isfinite(max_correction_kw) or max_correction_kw < 0:
        raise ValueError('max_correction_kw invalido')
    result, meta = {}, {}
    for site, values in bess.items():
        p=np.asarray(values,float)
        if p.shape!=(24,) or not np.isfinite(p).all():
            raise ValueError('Perfil BESS nao finito')
        kw=float(cap[site]['power_kw']); kwh=float(cap[site]['energy_kwh'])
        sign=np.sign(p)
        target=np.abs(p)
        coefficient=np.where(sign>0,1/.95,np.where(sign<0,-.95,0.))
        # variaveis x[0:24]=|p|, x[24:48]=|x-|p_blend||
        objective=np.r_[np.zeros(24),np.ones(24)]
        ab, bb=[],[]
        for h in range(24):
            t=np.zeros(48);t[h]=1;t[24+h]=-1
            ab.append(t);bb.append(float(target[h]))
            t=np.zeros(48);t[h]=-1;t[24+h]=-1
            ab.append(t);bb.append(float(-target[h]))
            prefix=np.zeros(48);prefix[:h+1]=coefficient[:h+1]
            ab.append(prefix);bb.append(.2*kwh)
            ab.append(-prefix);bb.append(.6*kwh)
        bounds=[]
        for h in range(24):
            lo=max(0.,float(target[h]-max_correction_kw))
            hi=min(kw,float(target[h]+max_correction_kw))
            if sign[h]==0 or (17<=h<20 and sign[h]<0):
                lo=hi=0.
            if lo>hi+1e-8:
                raise ValueError(f'Sem espaco para corrigir BESS {site} h={h}')
            bounds.append((lo,hi))
        bounds.extend([(0.,None)]*24)
        ae=np.r_[coefficient,np.zeros(24)][None,:]
        sol=linprog(objective,A_ub=np.asarray(ab),b_ub=np.asarray(bb),
            A_eq=ae,b_eq=[0.],bounds=bounds,method='highs')
        if not sol.success:
            raise ValueError(f'BESS {site}: reparo SOC inviavel: {sol.message}')
        fixed=sign*sol.x[:24]
        result[site]=fixed
        meta[site]={'max_adjustment_kw':float(np.max(np.abs(fixed-p))),
                    'total_adjustment_kw_hours':float(np.abs(fixed-p).sum()),
                    'objective_l1':float(sol.fun)}
    return result,meta


def assert_dispatch_identity(folder: Path, state: dict) -> None:
    """Confere cobertura, IDs e estacoes, evitando troca silenciosa de frota."""
    bpath, cpath = folder/'bess_dispatch.csv', folder/'vehicle_dispatch.csv'
    b, c = pd.read_csv(bpath), pd.read_csv(cpath)
    if not set(REQUIRED_BASE).issubset(b.columns) or not set(REQUIRED_EV).issubset(c.columns):
        raise ValueError(f'CSV incompleto: {folder}')
    expected_sites = set(state['cap'])
    if (set(b.site) != expected_sites or len(b) != 24*len(expected_sites) or
            b.duplicated(['site','hour']).any() or set(b.hour) != set(range(24))):
        raise ValueError(f'BESS com locais ou horarios invalidos: {folder}')
    ids = [str(v.id) for v in state['team']['vehicles']]
    if set(c.vehicle_id) != set(ids) or len(c) != 150*24 or c.duplicated(['vehicle_id','hour']).any():
        raise ValueError(f'Veiculos/horas incompletos ou repetidos: {folder}')
    sites = c.groupby('vehicle_id').site.agg(lambda x: tuple(sorted(set(x))))
    for idx, vehicle_id in enumerate(ids):
        if sites[vehicle_id] != (state['places'][idx],):
            raise ValueError(f'Estacao modificada para veiculo {vehicle_id}: {folder}')
    if set(c.hour) != set(range(24)):
        raise ValueError(f'Horas V2G incompletas: {folder}')


def score_close(actual: dict, expected: dict, label: str, atol: float = 1e-3) -> None:
    for k in ('thermal_aggravation_pp_h','voltage_aggravation_pu_h',
              'strict_voltage_samples','strict_overload_samples',
              'new_overloaded_lines_vs_s0','source_peak_kw'):
        a, b = actual.get(k), expected.get(k)
        if a is None or b is None or abs(float(a)-float(b)) > atol:
            raise RuntimeError(f'S0 ou politica mudaram ({label}): {k}: {a} != {b}')


def guard_values(guard_table: pd.DataFrame, branches: pd.DataFrame, tolerance_pp: float = .02):
    cols = list(KEY)+['loading_pct']
    check = guard_table[list(KEY)+['target_pct']].merge(
        branches[cols], on=list(KEY), validate='one_to_one', how='left', indicator=True)
    if len(check) != len(guard_table) or (check['_merge'] != 'both').any() or check.loading_pct.isna().any():
        raise ValueError('Linhas-fases-horas de guarda ausentes no AC')
    check['exceed_target_pp'] = check.loading_pct - check.target_pct - tolerance_pp
    return check


def focus_metric(reference: pd.DataFrame, candidate: pd.DataFrame,
                 focus_line: str, focus_phase: str) -> float:
    """Agravamento positivo p.p.-h da linha critica, referenciado ao S0-politica."""
    base = reference.loc[(reference.line == focus_line)&(reference.phase == focus_phase),['hour','loading_pct']]
    cand = candidate.loc[(candidate.line == focus_line)&(candidate.phase == focus_phase),['hour','loading_pct']]
    joined = base.merge(cand,on='hour',how='outer',indicator=True,
                        validate='one_to_one',suffixes=('_s0','_cand'))
    if len(joined) != 24 or (joined['_merge'] != 'both').any() or set(joined.hour) != set(range(24)):
        raise ValueError('Linha critica sem 24 amostras comparaveis')
    lim = np.maximum(100., joined.loading_pct_s0.to_numpy(float))
    return float(np.maximum(joined.loading_pct_cand.to_numpy(float)-lim,0.).sum())


def accept(ref_orig: dict, ref_policy: dict, orig: dict, policy: dict,
           focus_before: float, focus_after: float, guard_checks: pd.DataFrame,
           ops_ref: dict, ops: dict, args) -> tuple[bool,list[str]]:
    """Guarda cientifica: elegibilidade para analise, NAO certificacao da rede."""
    reasons=[]
    if orig['new_overloaded_lines_vs_s0'] != 0:
        reasons.append('NEW_LINES_VS_ORIGINAL_S0')
    if policy['new_overloaded_lines_vs_s0'] != 0:
        reasons.append('NEW_LINES_VS_POLICY_S0')
    if (guard_checks.exceed_target_pp > EPS).any() or (guard_checks.loading_pct > 100+EPS).any():
        reasons.append('RETAINED_LINE_GUARDS_AC')
    if policy['thermal_aggravation_pp_h'] > ref_policy['thermal_aggravation_pp_h']*(1-args.min_thermal_gain):
        reasons.append('THERMAL_GAIN_INSUFFICIENT')
    if orig['thermal_aggravation_pp_h'] > ref_orig['thermal_aggravation_pp_h'] + .02:
        reasons.append('THERMAL_WORSENED_VS_ORIGINAL_S0')
    if focus_after >= focus_before - args.min_focus_gain_pph:
        reasons.append('CRITICAL_LINE_GAIN_INSUFFICIENT')
    if policy['voltage_aggravation_pu_h'] > ref_policy['voltage_aggravation_pu_h'] + args.voltage_budget_puh:
        reasons.append('VOLTAGE_BUDGET_EXCEEDED')
    if orig['voltage_aggravation_pu_h'] > ref_orig['voltage_aggravation_pu_h'] + args.voltage_budget_puh:
        reasons.append('ORIGINAL_S0_VOLTAGE_BUDGET_EXCEEDED')
    if orig['strict_voltage_samples'] > ref_orig['strict_voltage_samples'] + args.max_new_voltage_samples:
        reasons.append('VOLTAGE_SAMPLE_COUNT_EXCEEDED')
    if orig['strict_overload_samples'] > ref_orig['strict_overload_samples'] + args.max_new_thermal_samples:
        reasons.append('THERMAL_SAMPLE_COUNT_EXCEEDED')
    if orig['vmin_pu'] < ref_orig['vmin_pu'] - args.vmin_drop_pu:
        reasons.append('MINIMUM_VOLTAGE_WORSENED')
    if orig['max_loading_pct'] > ref_orig['max_loading_pct'] + args.max_loading_growth_pp:
        reasons.append('MAX_LINE_LOADING_WORSENED')
    if orig['source_peak_kw'] > ref_orig['source_peak_kw'] + args.peak_allowance_kw:
        reasons.append('SOURCE_PEAK_EXCEEDED')
    if ops['tap_absolute_steps_total'] > ops_ref['tap_absolute_steps_total']*1.25 + 8:
        reasons.append('EXCESSIVE_TAP_STEPS')
    if ops['capacitor_switch_events'] > ops_ref['capacitor_switch_events'] + 6:
        reasons.append('EXCESSIVE_CAP_SWITCHING')
    return (not reasons),reasons


def no_overwrite(output: Path, protected: list[Path]) -> None:
    out = output.resolve()
    for item in protected:
        p = item.resolve()
        if out == p or p in out.parents or out in p.parents:
            raise ValueError(f'Saida V2.7 se sobrepoe a dados anteriores: {out} / {p}')
    if (out/'final_report_v27.json').exists():
        raise FileExistsError('V2.7 ja finalizada nessa pasta. Use nova --output')
    if (out/'candidates').exists():
        raise FileExistsError('Candidatos V2.7 ja presentes: use outra pasta para nao misturar rodadas')


def configure(args):
    repo = Path(args.repo).resolve()
    baseline = Path(args.baseline).resolve()
    v22dir = Path(args.v22_dir).resolve()
    v23dir = Path(args.v23_dir).resolve()
    v24dir = Path(args.v24_dir).resolve()
    v25dir = Path(args.v25_dir).resolve()
    v26dir = Path(args.v26_dir).resolve()
    output = Path(args.output).resolve()
    no_overwrite(output,[repo/'distributed_stochastic',baseline,v22dir,v23dir,v24dir,v25dir,v26dir])
    for p in (repo/'distributed_stochastic'/'coordenar_controles_v24.py',
              v24dir/'inventario_controles_v24.json',
              v25dir/'best'/'control_policy_v25.json',
              v26dir/'final_report_v26.json',
              v26dir/'precheck_v26.json'):
        if not p.is_file():
            raise FileNotFoundError(p)
    sys.path[:0] = [str(repo),str(repo/'src'),str(repo/'opendss_rebuild')]
    from distributed_stochastic import coordenar_controles_v24 as v24
    from distributed_stochastic import restaurar_viabilidade_v22 as v22
    v24.ensure_paths(repo,baseline,v22dir,v23dir,output)
    state = v24.load_state(repo,baseline,v22dir,v23dir)
    v25best = v25dir/'best'
    v26 = read_json(v26dir/'final_report_v26.json')
    pre26 = read_json(v26dir/'precheck_v26.json')
    policy = read_json(v25best/'control_policy_v25.json')
    if policy != pre26['policy'] or policy != v26['policy']:
        raise RuntimeError('Politica de controle divergente entre V2.5/V2.6')
    if not (v25dir/'policies'/policy['name']/'S0_policy_AC').is_dir():
        raise FileNotFoundError('S0 com mesma politica da V2.5 ausente')
    inv = read_json(v24dir/'inventario_controles_v24.json')
    s0_orig = state['refs']['S0']
    s0_policy = v22.read_ac_folder(v25dir/'policies'/policy['name']/'S0_policy_AC')
    ac25 = v22.read_ac_folder(v25best/'AC')
    ref_orig = v22.score_ac(s0_orig,ac25)
    ref_policy = v22.score_ac(s0_policy,ac25)
    score_close(ref_orig,v26['v25_baseline_original'],'V2.5 S0 original')
    score_close(ref_policy,v26['v25_baseline_same_policy'],'V2.5 S0 mesma politica')
    guard_csv = v25best/'targeted_line_guards_ac.csv'
    guards = pd.read_csv(guard_csv)
    if guards.empty or not set(KEY+('target_pct',)).issubset(guards.columns):
        raise ValueError('As guardas AC da V2.5 sao obrigatorias')
    if guards.duplicated(list(KEY)).any():
        raise ValueError('Guardas duplicadas')
    reference_focus = v26['v25_focus_temporal']
    focus_line,focus_phase = reference_focus['critical_line_phase'].rsplit('/',1)
    ref_focus = focus_metric(s0_policy['branches'], ac25['branches'],focus_line,focus_phase)
    if abs(ref_focus - reference_focus['critical_line_aggravation_pph'])>1e-3:
        raise RuntimeError('Linha critica da V2.5 nao reproduzida')
    assert_dispatch_identity(v25best,state)
    base_bess=v24.read_bess(v25best,state)
    base_cars=v24.read_cars(v25best,state)
    v24.validate_resources(state['team'],base_bess,base_cars,state['places'],state['cap'])
    inputs={}
    for name in args.candidates.split(','):
        name=name.strip()
        if name not in {'soft','anchors3'} or name in inputs:
            raise ValueError('Candidatos devem ser soft,anchors3 sem repeticoes')
        path=v26dir/'iter_01'/f'{name}_bt0'
        rep=read_json(path/'report_v26.json')
        if not rep.get('vs_policy_s0') or not (path/'AC'/'voltages.csv').is_file():
            raise ValueError(f'Candidato V2.6 incompleto: {path}')
        if rep['policy']!=policy:
            raise RuntimeError('Candidato V2.6 executado sob politica diferente')
        candidate_ac=v22.read_ac_folder(path/'AC')
        score_close(v22.score_ac(s0_orig,candidate_ac),rep['vs_original_s0'],
                    f'V2.6 {name} vs original S0')
        score_close(v22.score_ac(s0_policy,candidate_ac),rep['vs_policy_s0'],
                    f'V2.6 {name} vs policy S0')
        assert_dispatch_identity(path,state)
        cb=v24.read_bess(path,state)
        cc=v24.read_cars(path,state)
        v24.validate_resources(state['team'],cb,cc,state['places'],state['cap'])
        inputs[name]={'path':path,'bess':cb,'cars':cc}
    return dict(repo=repo,baseline=baseline,v24=v24,v22=v22,state=state,
        v25best=v25best, v26dir=v26dir, output=output, inv=inv,
        policy=policy, s0_orig=s0_orig,s0_policy=s0_policy,ac25=ac25,
        ref_orig=ref_orig, ref_policy=ref_policy, guard_table=guards,
        focus_line=focus_line, focus_phase=focus_phase, ref_focus=ref_focus,
        base_bess=base_bess,base_cars=base_cars,inputs=inputs)


def precheck(args, ctx):
    out=ctx['output'];out.mkdir(parents=True,exist_ok=True)
    probes=[]
    for name,source in ctx['inputs'].items():
        for alpha in parse_alphas(args.alphas):
            try:
                b,c=blend_schedules(ctx['base_bess'],ctx['base_cars'],
                                    source['bess'],source['cars'],alpha)
                b,repair=repair_bess_soc(b,ctx['state']['cap'],args.max_bess_repair_kw)
                ctx['v24'].validate_resources(ctx['state']['team'],b,c,
                        ctx['state']['places'],ctx['state']['cap'])
                probes.append({'strategy':name,'alpha':alpha,'resources_ok':True,
                    'max_bess_repair_kw':max(x['max_adjustment_kw'] for x in repair.values())})
            except Exception as exc:
                probes.append({'strategy':name,'alpha':alpha,'resources_ok':False,
                    'reason':str(exc)})
    if not any(x['resources_ok'] for x in probes):
        write_json(out/'precheck_v27.json',{'state':'RESOURCE_PREFLIGHT_FAILED',
                                           'resource_probes':probes})
        raise RuntimeError('Nenhum passo V2.7 preserva os contratos de armazenamento; ver precheck_v27.json')
    report={'state':'V27_INPUT_VALIDATED',
        'method':'AC line-search on V2.5 -> previously solved MILP V2.6 dispatches',
        'v25_preserved':True, 'policy':ctx['policy'],
        'candidates':list(ctx['inputs']), 'alphas':parse_alphas(args.alphas),
        'bess_sites':list(ctx['base_bess']),
        'vehicles':int(ctx['base_cars'].shape[0]),
        's0_original_score':ctx['ref_orig'],
        's0_same_policy_score':ctx['ref_policy'],
        'focus_line_phase':ctx['focus_line']+'/'+ctx['focus_phase'],
        'focus_aggravation_pph':ctx['ref_focus'],
        'guard_count':len(ctx['guard_table']),
        'resource_validation':'V2.4 validate_resources; every blended schedule checked again',
        'resource_probes':probes,
        'note':'Nao otimiza locais nem garante MILP-optimalidade ou certificacao absoluta'}
    write_json(out/'precheck_v27.json',report)
    print('[V2.7] Precheck OK; V2.5 reproduzida, duas referencias S0, recursos e V2.6 conferidos.',flush=True)
    print(f'[V2.7] Linha critica {report["focus_line_phase"]}: {ctx["ref_focus"]:.3f} p.p.-h; '
          f'guardas={len(ctx["guard_table"])}; recursos validos='
          f'{sum(x["resources_ok"] for x in probes)}/{len(probes)}',flush=True)


def run(args, ctx):
    out = ctx['output']
    v24,v22,state = ctx['v24'],ctx['v22'],ctx['state']
    best=out/'best'
    shutil.copytree(ctx['v25best'],best)
    write_json(best/'provenance_v27.json',{
        'champion':'v25_unchanged','source':str(ctx['v25best']),
        'operational_certification':False})
    ops_ref=v24.control_changes(ctx['ac25'])
    rows=[]; best_key=None; count_ok=0
    for name,source in ctx['inputs'].items():
        for alpha in parse_alphas(args.alphas):
            folder=out/'candidates'/f'{name}_alpha_{alpha:.3f}'.replace('.','p')
            folder.mkdir(parents=True,exist_ok=False)
            row={'strategy':name,'alpha':alpha,'status':'PENDING',
                'source_v26':str(source['path']), 'folder':str(folder),'accepted':False}
            try:
                bess,cars=blend_schedules(ctx['base_bess'],ctx['base_cars'],
                              source['bess'],source['cars'],alpha)
                bess,repair_meta=repair_bess_soc(bess,state['cap'],args.max_bess_repair_kw)
                write_json(folder/'soc_repair_v27.json',repair_meta)
                v24.validate_resources(state['team'],bess,cars,state['places'],state['cap'])
                profiles=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                              bess,cars,state['places'])
                # Reconstroi perfil de referencia para detectar discrepancias e dupla contagem.
                state['profiles']=profiles
                v22.write_dispatch(folder,bess,cars,state['team']['vehicles'],state['places'],state['cap'])
                profiles.to_csv(folder/'injections_reoptimized.csv',index=False)
                ac=v24.evaluate(ctx['repo'],profiles,state['sites'],ctx['inv'],
                                 ctx['policy'],folder/'AC')
                q=v22.score_ac(ctx['s0_orig'],ac)
                qp=v22.score_ac(ctx['s0_policy'],ac)
                ops=v24.control_changes(ac)
                checks=guard_values(ctx['guard_table'],ac['branches'])
                checks.to_csv(folder/'guardas_v27_ac.csv',index=False)
                focus=focus_metric(ctx['s0_policy']['branches'],ac['branches'],
                                   ctx['focus_line'],ctx['focus_phase'])
                yes,reasons=accept(ctx['ref_orig'],ctx['ref_policy'],q,qp,
                                   ctx['ref_focus'],focus,checks,ops_ref,ops,args)
                report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if yes else 'AC_REJECTED',
                    'strategy':name,'alpha':alpha,'reasons':reasons,
                    'vs_original_s0':q,'vs_policy_s0':qp,
                    'focus_aggravation_pph':focus,'focus_gain_pph':ctx['ref_focus']-focus,
                    'control_operations':ops,'absolute_ac_pass':bool(q['strict_pass']),
                    'operational_certification':False,
                    'note':'Interpolacao pode tornar recursos inviaveis; valida-se ANTES do replay AC'}
                write_json(folder/'report_v27.json',report)
                row.update(status=report['state'], accepted=yes,
                    thermal_policy_pph=qp['thermal_aggravation_pp_h'],
                    voltage_policy_puh=qp['voltage_aggravation_pu_h'],
                    focus_line_pph=focus,peak_kw=q['source_peak_kw'],
                    vmin_pu=q['vmin_pu'],new_lines_original=q['new_overloaded_lines_vs_s0'],
                    new_lines_policy=qp['new_overloaded_lines_vs_s0'],
                    reason=';'.join(reasons))
                print(f'[V2.7] {name} alpha={alpha:g}: termica={qp["thermal_aggravation_pp_h"]:.2f}, '
                      f'tensao={qp["voltage_aggravation_pu_h"]:.3f}, '
                      f'novas linhas={q["new_overloaded_lines_vs_s0"]}/{qp["new_overloaded_lines_vs_s0"]}, '
                      f'aceito={yes}',flush=True)
                if yes:
                    count_ok+=1
                    rank=(qp['thermal_aggravation_pp_h'],focus,
                          qp['voltage_aggravation_pu_h'],q['source_peak_kw'])
                    if best_key is None or rank<best_key:
                        best_key=rank
                        shutil.rmtree(best)
                        shutil.copytree(folder,best)
                        write_json(best/'control_policy_v25.json',ctx['policy'])
                        write_json(best/'ac_score_vs_original_s0.json',q)
                        write_json(best/'ac_score_vs_policy_s0.json',qp)
                        write_json(best/'provenance_v27.json',{
                            'champion':'v27_AC_eligible', 'source':str(folder),
                            'strategy':name,'alpha':alpha,
                            'operational_certification':False})
                        row['selected_when_run']=True
            except Exception as exc:
                row['status']='ERROR_OR_RESOURCE_REJECTED'
                row['reason']=f'{type(exc).__name__}: {exc}'
                write_json(folder/'error_v27.json',{'error':str(exc),
                    'exception_type':type(exc).__name__,'traceback':traceback.format_exc()})
                print(f'[V2.7] {name} alpha={alpha:g}: rejeitado/erro: {exc}',flush=True)
            finally:
                rows.append(row)
                pd.DataFrame(rows).to_csv(out/'todos_candidatos_ac_v27.csv',index=False)
    prov=read_json(best/'provenance_v27.json')
    report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if count_ok else 'AC_VIOLATIONS_REMAIN',
            'champion':prov['champion'],'champion_provenance':prov,
            'attempts':len(rows),'accepted_ac_candidates':count_ok,
            'v25_baseline_original':ctx['ref_orig'],
            'v25_baseline_same_policy':ctx['ref_policy'],
            'policy':ctx['policy'],
            'guard_definition':'New overloaded lines forbidden vs original and same-policy S0; retained V2.5 line guards AC 99.9% + 0.02 p.p.; voltage and resource constraints',
            'note':'Aceitacao so significa elegibilidade para analise; S0 ja viola limites. Nao certifica transformadores, protecao, N-1, rede BT ou robustez fora da amostra.'}
    write_json(out/'final_report_v27.json',report)
    print(f'[V2.7] Resultado: {report["state"]}; aceitos={count_ok}; melhor={report["champion"]}',flush=True)
    print(f'[V2.7] Relatorio: {out/"final_report_v27.json"}',flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v22-dir',required=True)
    p.add_argument('--v23-dir',required=True)
    p.add_argument('--v24-dir',required=True)
    p.add_argument('--v25-dir',required=True)
    p.add_argument('--v26-dir',required=True)
    p.add_argument('--output',default='./results/v27_passo_ac')
    p.add_argument('--task',choices=['precheck','run'],default='precheck')
    p.add_argument('--candidates',default='soft,anchors3')
    p.add_argument('--alphas',default='0.125,0.25,0.5')
    p.add_argument('--max-bess-repair-kw',type=float,default=15.)
    p.add_argument('--min-thermal-gain',type=float,default=.01)
    p.add_argument('--min-focus-gain-pph',type=float,default=.5)
    p.add_argument('--voltage-budget-puh',type=float,default=.15)
    p.add_argument('--peak-allowance-kw',type=float,default=120.)
    p.add_argument('--vmin-drop-pu',type=float,default=.002)
    p.add_argument('--max-loading-growth-pp',type=float,default=.15)
    p.add_argument('--max-new-voltage-samples',type=int,default=20)
    p.add_argument('--max-new-thermal-samples',type=int,default=4)
    args=p.parse_args(argv)
    try:
        parse_alphas(args.alphas)
        if not 0 <= args.min_thermal_gain < 1 or args.min_focus_gain_pph < 0 or args.voltage_budget_puh < 0:
            p.error('Orcamentos invalidos')
        if min(args.max_bess_repair_kw,args.peak_allowance_kw,args.vmin_drop_pu,args.max_loading_growth_pp,
               args.max_new_voltage_samples,args.max_new_thermal_samples)<0:
            p.error('Limites nao podem ser negativos')
        ctx=configure(args)
        precheck(args,ctx)
        if args.task=='run':
            run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.7] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
