#!/usr/bin/env python3
"""V2.8.1 — busca termica epsilon-constraint, validacao AC de 24 horas.

Evolui V2.8 sem modificar V2.2--V2.8. Minimizacao da severidade termica
(same-policy S0) com limite explicito de piora de tensao e salvaguardas AC.
As aproximacoes LP so produzem PROPOSTAS; a aceitacao exige OpenDSS real.
Nao certifica operabilidade absoluta da rede.
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
from scipy.optimize import linprog

from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic import validar_passo_v27 as v27

FLOAT_EPS = 1e-9


def epsilon_objective(gradients: list[dict], focus_weight: float = 0.02) -> np.ndarray:
    """Objetivo termico em unidades fisicas, tensao so via epsilon-constraint.

    O ganho na linha critica e desempate opcional, nao compensacao de uma piora
    de tensao. O LP retém MIN_THERMAL_GAIN e MIN_FOCUS_GAIN do V2.8.
    """
    if not gradients or not np.isfinite(focus_weight) or focus_weight < 0:
        raise ValueError('Gradientes ou peso de foco invalidos')
    g = np.asarray([[row['thermal'],row['focus']] for row in gradients],float)
    if not np.isfinite(g).all():
        raise ValueError('Gradientes nao finitos')
    return g[:,0] + float(focus_weight)*g[:,1]


def solve_epsilon_lp(c, A, b, tags, trust_radius, max_weight_per_direction=None):
    """Resolve 4 níveis de aproximações; nunca remove gates AC.

    Em cada nível aceita APENAS pontos com melhoria termica prevista.
    O LP permanece com limites agregados de tensao, termica original, pico,
    tensao minima e ganhos minimos. Só relaxa restricoes locais do SURROGATE.
    """
    c = np.asarray(c,float); A=np.asarray(A,float); b=np.asarray(b,float)
    if c.ndim != 1 or A.ndim != 2 or A.shape != (len(b),len(c)) or len(tags)!=len(b):
        raise ValueError('Dimensoes LP invalidas')
    if not np.isfinite(c).all() or not np.isfinite(A).all() or not np.isfinite(b).all():
        raise ValueError('Coeficientes LP invalidos')
    if not np.isfinite(trust_radius) or trust_radius<=0:
        raise ValueError('Trust radius invalido')
    if max_weight_per_direction is None:
        max_weight_per_direction = trust_radius
    if max_weight_per_direction<=0 or max_weight_per_direction>trust_radius:
        raise ValueError('Limite individual invalido')
    relaxations=[(),('H20:',),('H20:','VOLT_LOW:','VOLT_HIGH:'),
                 ('H20:','VOLT_LOW:','VOLT_HIGH:','LINE:')]
    attempts=[]
    for i,prefixes in enumerate(relaxations):
        selected=np.asarray([not any(name.startswith(p) for p in prefixes) for name in tags],bool)
        sol=linprog(c,A_ub=A[selected],b_ub=b[selected],
                    bounds=[(0.,max_weight_per_direction)]*len(c),method='highs',
                    options={'time_limit':60.0})
        entry={'level':i,'removed_forecast_only':list(prefixes),
               'kept_constraints':int(selected.sum()),
               'solver_status':int(sol.status),'message':str(sol.message)}
        if sol.success:
            w=np.asarray(sol.x,float)
            entry.update(weights=w.tolist(),objective_pph=float(c@w),
                min_surrogate_margin=float(np.min(b[selected]-A[selected]@w)))
            if float(c@w)<-1e-7 and float(w.sum())>1e-7:
                attempts.append(entry)
                return w,{'state':'LP_EPSILON_SOLUTION' if i==0 else 'LP_FORECAST_RELAXED',
                          'selected_level':i,'predicted_thermal_objective_pph':float(c@w),
                          'attempts':attempts,
                          'disclaimer':'Surrogate only; eligibility requires full AC gates'}
            entry['rejection']='NO_PREDICTED_THERMAL_REDUCTION'
        attempts.append(entry)
    return None,{'state':'NO_EPSILON_LP_PROPOSAL','attempts':attempts}


def split_directions(ctx, current:dict, mode: str) -> dict:
    """Adiciona duas direções independentes de componentes, sem nova alocação.

    Cada direção passa por reparo SOC e validação física individual antes de
    poder entrar no LP. É normal que alguma direção seja descartada.
    """
    choices={'none':(), 'soft':('soft',),'anchors3':('anchors3',),
             'both':('soft','anchors3')}
    if mode not in choices:raise ValueError('Split mode desconhecido')
    result=dict(ctx['directions'])
    for name in choices[mode]:
        if name not in ctx['inputs']:continue
        src=ctx['inputs'][name]
        result[f'split_{name}_bess']={'bess':src['bess'],
              'cars':current['cars'],'path':src['path']}
        result[f'split_{name}_v2g']={'bess':current['bess'],
              'cars':src['cars'],'path':src['path']}
    filtered={}
    for name,tar in result.items():
        if np.max(np.abs(tar['cars']-current['cars']))>1e-7 or any(
            np.max(np.abs(tar['bess'][s]-current['bess'][s]))>1e-7
            for s in current['bess']):
            filtered[name]=tar
    return filtered


def cumulative_gate(ctx, ac:dict, metrics:dict, current:dict, args):
    """Evita consumo renovado de orcamentos a cada iteracao da V2.8.1."""
    reasons=[]
    reference=ctx['start_metrics']
    for reference_key in ('orig','policy'):
        if (metrics[reference_key]['voltage_aggravation_pu_h'] >
            reference[reference_key]['voltage_aggravation_pu_h']+args.voltage_budget_puh+1e-9):
            reasons.append('CUMULATIVE_VOLTAGE_BUDGET_'+reference_key.upper())
    if (metrics['policy']['thermal_aggravation_pp_h'] >=
            current['metrics']['policy']['thermal_aggravation_pp_h']-args.min_actual_thermal_gain_pph):
        reasons.append('NO_INCREMENTAL_THERMAL_REDUCTION')
    if metrics['focus'] >= current['metrics']['focus']-args.min_actual_focus_gain_pph:
        reasons.append('NO_INCREMENTAL_FOCUS_REDUCTION')
    if (metrics['orig']['source_peak_kw'] >
        reference['orig']['source_peak_kw']+args.peak_allowance_kw+1e-8):
        reasons.append('CUMULATIVE_PEAK_GROWTH')
    if (metrics['orig']['vmin_pu'] <
        reference['orig']['vmin_pu']-args.vmin_drop_pu-1e-9):
        reasons.append('CUMULATIVE_VMIN_DROP')
    if (metrics['orig']['max_loading_pct'] >
        reference['orig']['max_loading_pct']+args.max_loading_growth_pp+1e-8):
        reasons.append('CUMULATIVE_MAX_LOADING_GROWTH')
    # Contagens não devem acumular deteriorações de uma iteração para outra.
    if (metrics['orig']['strict_voltage_samples'] >
         reference['orig']['strict_voltage_samples']+args.max_added_voltage_samples):
        reasons.append('CUMULATIVE_VOLTAGE_SAMPLES')
    if (metrics['orig']['strict_overload_samples'] >
         reference['orig']['strict_overload_samples']+args.max_added_overload_samples):
        reasons.append('CUMULATIVE_THERMAL_SAMPLES')
    local,info=v28.critical_hour_audit(ctx['ac27'],ac,args)
    reasons.extend('CUMULATIVE_'+s for s in local)
    return sorted(set(reasons)),info


def dual_voltage_budget_gate(ctx, ac, metrics, current, args):
    """Aplica as guardas V2.7 historicas e V2.8.1 incrementais separadamente.

    HISTORICO (V2.5): aplica 0.15 pu.h a ambos S0, como na V2.7.
    INCREMENTAL (V2.7): aplica 0.03 pu.h acumulados durante a V2.8.1.
    A regra de aceitacao AC de V2.7 nao deve receber o pequeno budget LP.
    Nao relaxa guardas termicas, SOC, tensao minima ou eventos locais.
    """
    if not (np.isfinite(args.historical_voltage_budget_puh) and
            np.isfinite(args.voltage_budget_puh) and
            0 <= args.voltage_budget_puh <= args.historical_voltage_budget_puh):
        raise ValueError('Orcamentos de tensao invalidos: historico deve ser >= incremental')
    historical_args = copy.copy(args)
    historical_args.voltage_budget_puh = args.historical_voltage_budget_puh
    _, historical_reasons, audit = v28.gate_v28(
        ctx, ac, current['metrics'], metrics, historical_args, current['ac'])
    incremental_reasons, cumulative_audit = cumulative_gate(
        ctx, ac, metrics, current, args)
    reasons = sorted(set(historical_reasons + incremental_reasons))
    detail = {
        'historical_reference': 'V2.5',
        'historical_voltage_budget_puh': args.historical_voltage_budget_puh,
        'incremental_reference': 'V2.7',
        'incremental_voltage_budget_puh': args.voltage_budget_puh,
        'historical_gate_reasons': historical_reasons,
        'incremental_gate_reasons': incremental_reasons,
        'local_audit': audit,
        'cumulative_local_audit': cumulative_audit,
    }
    return not reasons, reasons, detail


def verify_v28_reference(ctx, previous:Path, fd_step:float, repair_kw:float):
    """Autoriza reutilizar probes somente com mesmos despachos, política e passo."""
    j=v27.read_json(previous/'final_report_v28.json')
    manifest=v27.read_json(previous/'run_manifest_v28.json')
    if j['status']!='V27_PRESERVED_NO_ACCEPTED_V28':
        raise ValueError('Cache V2.8 nao preservou V2.7; nao reutilizar')
    if j['policy']!=ctx['policy']:
        raise ValueError('Politica do cache V2.8 divergente')
    if abs(float(manifest['fd_step'])-fd_step)>1e-12:
        raise ValueError('fd-step diferente do cache V2.8')
    if manifest['candidates']!='soft,anchors3':
        raise ValueError('Direcoes V2.8 diferentes; nao reutilizar')
    if abs(float(manifest['max_bess_repair_kw'])-repair_kw)>1e-10:
        raise ValueError('Reparo SOC diferente do cache V2.8')
    for arg_key, context_path in (('repo',ctx['repo']),
            ('baseline',ctx['baseline']),('v25_dir',ctx['v25best'].parent),
            ('v26_dir',ctx['v26dir']),('v27_dir',ctx['v27dir'])):
        if Path(manifest[arg_key]).resolve()!=Path(context_path).resolve():
            raise ValueError(f'Origem {arg_key} divergente do cache V2.8')
    if int(j['accepted_v28'])!=0:
        raise ValueError('Cache nao se refere a V2.7 inalterada')
    for side in ('orig','policy'):
        v27.score_close(ctx['start_metrics'][side],j['reference_v27'][side],
                        'Cache V2.8 e V2.7')
    # Confirmar as series AC completas, nao so os indicadores agregados.
    from pandas.testing import assert_frame_equal
    cached=ctx['v22'].read_ac_folder(previous/'best'/'AC')
    for kind,keys in (('branches',v28.KEY_LINE),('voltages',v28.KEY_VOLT),('source',['hour'])):
        ref=ctx['ac27'][kind].sort_values(keys).reset_index(drop=True)
        test=cached[kind].sort_values(keys).reset_index(drop=True)
        assert_frame_equal(ref,test,check_dtype=False,check_exact=False,atol=1e-9,rtol=0)
    return v27.read_json(previous/'iter_01'/'calibration_report_v28.json')


def cached_probe(ctx,previous:Path,calibration:dict,name:str,step:float,current:dict):
    if current is not ctx['start']:
        return None
    entry=next((p for p in calibration['probes'] if p['name']==name and p['status']=='OK'),None)
    if entry is None:return None
    folder=previous/'iter_01'/'probes'/name
    ac=ctx['v22'].read_ac_folder(folder/'AC')
    q=v28.qmetrics(ctx,ac)
    for side in ('orig','policy'):
        v27.score_close(q[side],entry['metrics'][side],f'cache probe {name}')
    slope=v28.derive_slope(current['ac'],ac,step)
    g=v28.metric_gradient(current['metrics'],q,step)
    return slope,g,q


def prepare(args):
    if not Path(args.v28_dir).resolve().is_dir():
        raise FileNotFoundError(args.v28_dir)
    ctx=v28.prepare(args)
    v28.ensure_output(ctx['output'],[Path(args.v28_dir).resolve()])
    if (ctx['output']/'final_report_v281.json').exists() or (ctx['output']/'best').exists() or (
            ctx['output']/'iter_01').exists():
        raise FileExistsError('V2.8.1 ja executada nessa pasta; escolha novo --output')
    ctx['v28dir']=Path(args.v28_dir).resolve()
    return ctx


def precheck(args,ctx):
    out=ctx['output'];out.mkdir(parents=True,exist_ok=True)
    names=list(split_directions(ctx,ctx['start'],args.split_strategy))
    report={'state':'V281_INPUT_VALIDATED','policy':ctx['policy'],
        'incumbent':'V2.7 AC_ELIGIBLE_NOT_CERTIFIED',
        'directions':names,'split_strategy':args.split_strategy,
        'v27_reference':ctx['start_metrics'],
        'method':'thermal epsilon constraint LP; 24h AC revalidation; cumulative guard',
        'historical_voltage_budget_puh':args.historical_voltage_budget_puh,
        'incremental_voltage_budget_puh':args.voltage_budget_puh,
        'unverified':'transformadores, protecoes, N-1, robustez fora da amostra'}
    v27.write_json(out/'precheck_v281.json',report)
    print(f'[V2.8.1] Precheck OK: {len(names)} direcoes; melhor atual V2.7.',flush=True)


def run(args,ctx):
    out=ctx['output'];v27.write_json(out/'run_manifest_v281.json',vars(args))
    shutil.copytree(ctx['v27dir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v281.json',{'champion':'V2.7_UNCHANGED',
        'operational_certification':False})
    current=ctx['start'];accepted=0;records=[];iteration_summaries=[]
    prior_cache=None
    if args.reuse_v28_probes:
        prior_cache=verify_v28_reference(ctx,ctx['v28dir'],args.fd_step,
                                          args.max_bess_repair_kw)
        print('[V2.8.1] Cache V2.8 validado; 4 probes AC podem ser reutilizados.',flush=True)
    for it in range(1,args.max_iterations+1):
        idir=out/f'iter_{it:02d}';idir.mkdir(parents=True,exist_ok=False)
        directions=split_directions(ctx,current,args.split_strategy)
        targets=[];names=[];slopes=[];grad=[];logs=[]
        print(f'[V2.8.1] Iteracao {it}: calibrando {len(directions)} direcoes...',flush=True)
        for name,target in directions.items():
            try:
                reuse=None
                if it==1 and prior_cache is not None and name in ctx['directions']:
                    reuse=cached_probe(ctx,ctx['v28dir'],prior_cache,name,args.fd_step,current)
                if reuse is not None:
                    slope,g,met=reuse;method='V2.8_REUSED_AC_VERIFIED'
                else:
                    folder=idir/'probes'/name
                    probe=v28.evaluate_schedule(ctx,current,[target],
                        np.array([args.fd_step]),folder,args.max_bess_repair_kw,
                        full_report=False)
                    slope=v28.derive_slope(current['ac'],probe['ac'],args.fd_step)
                    g=v28.metric_gradient(current['metrics'],probe['metrics'],args.fd_step)
                    met=probe['metrics'];method='V2.8.1_NEW_AC'
                names.append(name);targets.append(target);slopes.append(slope);grad.append(g)
                logs.append({'name':name,'status':'OK','source':method,
                             'gradients':g,'metrics':met})
                print(f'[V2.8.1] {name}: dT={g["thermal"]:+.3f} p.p.-h; '
                      f'dV={g["voltage"]:+.5f} p.u.-h; {method}',flush=True)
            except Exception as exc:
                logs.append({'name':name,'status':'PROBE_REJECTED_OR_ERROR',
                    'message':str(exc),'traceback':traceback.format_exc()})
                print(f'[V2.8.1] Probe {name} descartado: {exc}',flush=True)
        v27.write_json(idir/'calibration_report_v281.json',{'probes':logs,'fd_step':args.fd_step})
        if not slopes:
            iteration_summaries.append({'iteration':it,'state':'NO_VALID_AC_DIRECTIONS'});break
        try:
            _,A,b,tags,summ=v28.build_lp(current['ac'],grad,slopes,
                                           ctx['guard_table'],args,current['metrics'])
            c=epsilon_objective(grad,args.focus_weight)
            weights,lp=solve_epsilon_lp(c,A,b,tags,args.trust_radius,
                                        args.max_weight_per_direction)
            if weights is not None:
                lp['predicted_metric_changes']={key:float(np.dot(weights,
                    np.array([g[key] for g in grad],float)))
                    for key in ('thermal','voltage','volt_orig','focus','peak')}
            v27.write_json(idir/'surrogate_v281.json',{'direction_names':names,
                'state':lp['state'],'lp':lp,'thermal_cost_pph':c.tolist(),
                'linear_constraints':summ,'constraint_names':tags,
                'epsilon_voltage_budget_puh':args.voltage_budget_puh})
        except Exception as exc:
            iteration_summaries.append({'iteration':it,'state':'LP_ERROR','error':str(exc)})
            v27.write_json(idir/'error_lp_v281.json',{'error':str(exc),
                'traceback':traceback.format_exc()})
            break
        if weights is None:
            iteration_summaries.append({'iteration':it,'state':'NO_EPSILON_LP_PROPOSAL'});break
        ok_it=False
        for bt,shrink in enumerate([1.,.5,.25][:args.backtracks+1]):
            folder=idir/f'trial_bt{bt}'
            row={'iteration':it,'backtrack':bt,'weight_scale':shrink,
                 'weights':{n:float(w*shrink) for n,w in zip(names,weights)},
                 'folder':str(folder),'status':'PENDING'}
            try:
                trial=v28.evaluate_schedule(ctx,current,targets,weights*shrink,folder,
                                             args.max_bess_repair_kw)
                yes,reasons,details=dual_voltage_budget_gate(
                    ctx, trial['ac'], trial['metrics'], current, args)
                row.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if yes else 'AC_REJECTED',
                           reasons=reasons,local_audit=details['local_audit'],
                           cumulative_local_audit=details['cumulative_local_audit'],
                           budget_audit=details,metrics=trial['metrics'])
                v27.write_json(folder/'report_v281.json',row)
                print(f'[V2.8.1] it={it} bt={bt}: '
                      f'T={trial["metrics"]["policy"]["thermal_aggravation_pp_h"]:.3f}; '
                      f'V={trial["metrics"]["policy"]["voltage_aggravation_pu_h"]:.4f}; '
                      f'novas={trial["metrics"]["orig"]["new_overloaded_lines_vs_s0"]}; '
                      f'aceito={yes}',flush=True)
                if yes:
                    accepted+=1;ok_it=True;current=trial
                    shutil.rmtree(out/'best')
                    shutil.copytree(folder,out/'best')
                    v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                    v27.write_json(out/'best'/'ac_score_vs_original_s0.json',trial['metrics']['orig'])
                    v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',trial['metrics']['policy'])
                    v27.write_json(out/'best'/'provenance_v281.json',{'champion':'V2.8.1_AC_ELIGIBLE',
                        'source':str(folder),'iteration':it,'operational_certification':False})
            except Exception as exc:
                row.update(status='ERROR_OR_RESOURCE_REJECTED',reason=str(exc))
                folder.mkdir(parents=True,exist_ok=True)
                v27.write_json(folder/'error_v281.json',{'message':str(exc),
                   'traceback':traceback.format_exc()})
                print(f'[V2.8.1] it={it} bt={bt}: erro: {exc}',flush=True)
            records.append(row)
            if ok_it:break
        iteration_summaries.append({'iteration':it,'state':'AC_ACCEPTED' if ok_it else 'NO_AC_ACCEPTED',
                                    'trials':sum(r['iteration']==it for r in records)})
        if not ok_it:break
    pd.DataFrame([{'iteration':r.get('iteration'),'backtrack':r.get('backtrack'),
        'status':r.get('status'),
        'reasons':';'.join(r.get('reasons',[])) or r.get('reason',''),
        'thermal_policy_pph':r.get('metrics',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'voltage_policy_puh':r.get('metrics',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
        'folder':r.get('folder')} for r in records],
        columns=['iteration','backtrack','status','reasons','thermal_policy_pph',
                 'voltage_policy_puh','folder']).to_csv(out/'todos_candidatos_ac_v281.csv',index=False)
    report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else 'V27_PRESERVED_NO_ACCEPTED_V281',
         'accepted_v281':accepted,'champion':'V2.8.1' if accepted else 'V2.7',
         'policy':ctx['policy'],'reference_v27':ctx['start_metrics'],
         'best_metrics':current['metrics'],'iterations':iteration_summaries,
         'method':'Thermal epsilon-constraint directional LP + full 24h AC; historical V2.5 and incremental V2.7 gates',
         'historical_voltage_budget_puh':args.historical_voltage_budget_puh,
         'incremental_voltage_budget_puh':args.voltage_budget_puh,
         'absolute_certification':False,
         'limitations':'No new global MILP; no transformer/protection/N-1/stochastic certification'}
    v27.write_json(out/'final_report_v281.json',report)
    print(f'[V2.8.1] Final: {report["state"]}; aceitos={accepted}; campeao={report["champion"]}',flush=True)
    print(f'[V2.8.1] Relatorio: {out/"final_report_v281.json"}',flush=True)


def make_parser():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('baseline','v22-dir','v23-dir','v24-dir','v25-dir','v26-dir','v27-dir','v28-dir'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--repo',default='.')
    p.add_argument('--output',default='./results/v281_epsilon_ac')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--candidates',default='soft,anchors3')
    p.add_argument('--alphas',default='0.125')
    p.add_argument('--fd-step',type=float,default=.0625)
    p.add_argument('--trust-radius',type=float,default=.18)
    p.add_argument('--max-weight-per-direction',type=float,default=.18)
    p.add_argument('--max-iterations',type=int,default=2)
    p.add_argument('--backtracks',type=int,default=2)
    p.add_argument('--max-bess-repair-kw',type=float,default=15.)
    p.add_argument('--split-strategy',choices=('none','soft','anchors3','both'),default='soft')
    p.add_argument('--reuse-v28-probes',action='store_true')
    p.add_argument('--focus-weight',type=float,default=.02)
    # LP (previsao): mesmo envelope V2.8, objetivo epsilon-constraint corrigido.
    p.add_argument('--predicted-thermal-gain-pph',type=float,default=.12)
    p.add_argument('--predicted-focus-gain-pph',type=float,default=.05)
    p.add_argument('--voltage-budget-puh',type=float,default=.03,
                   help='Budget incremental acumulado desde V2.7 (pu.h)')
    p.add_argument('--historical-voltage-budget-puh',type=float,default=.15,
                   help='Budget historico desde V2.5 para as guardas V2.7 (pu.h)')
    p.add_argument('--thermal-orig-budget-pph',type=float,default=.02)
    p.add_argument('--peak-allowance-kw',type=float,default=60.)
    p.add_argument('--local-peak-growth-kw',type=float,default=50.)
    p.add_argument('--vmin-drop-pu',type=float,default=.0007)
    p.add_argument('--max-loading-growth-pp',type=float,default=.12)
    p.add_argument('--local-loading-tolerance-pp',type=float,default=.05)
    p.add_argument('--local-voltage-tolerance-pu',type=float,default=.0007)
    p.add_argument('--hour20-improvement-pp',type=float,default=.01)
    p.add_argument('--hour15-max-worsening-pp',type=float,default=.10)
    p.add_argument('--hour20-max-worsening-pp',type=float,default=.02)
    p.add_argument('--hour18-vmin-drop-pu',type=float,default=.0003)
    p.add_argument('--hour18-under-budget-puh',type=float,default=.015)
    p.add_argument('--max-added-voltage-samples',type=int,default=5)
    p.add_argument('--max-added-overload-samples',type=int,default=0)
    p.add_argument('--min-actual-thermal-gain-pph',type=float,default=.05)
    p.add_argument('--min-actual-focus-gain-pph',type=float,default=.02)
    # Herda guardas V2.7, nao afrouxar estes valores apenas para obter aceitacao.
    p.add_argument('--min-thermal-gain',type=float,default=.01)
    p.add_argument('--min-focus-gain-pph',type=float,default=.5)
    p.add_argument('--max-new-voltage-samples',type=int,default=20)
    p.add_argument('--max-new-thermal-samples',type=int,default=4)
    return p


def main(argv=None):
    p=make_parser(); args=p.parse_args(argv)
    values=(args.fd_step,args.trust_radius,args.max_weight_per_direction,
        args.max_bess_repair_kw,args.focus_weight,args.predicted_thermal_gain_pph,
        args.predicted_focus_gain_pph,args.min_actual_thermal_gain_pph,
        args.min_actual_focus_gain_pph,args.voltage_budget_puh,
        args.historical_voltage_budget_puh,
        args.thermal_orig_budget_pph,args.peak_allowance_kw,args.local_peak_growth_kw,
        args.vmin_drop_pu,args.max_loading_growth_pp,args.local_loading_tolerance_pp,
        args.local_voltage_tolerance_pu,args.hour20_improvement_pp,
        args.hour15_max_worsening_pp,args.hour20_max_worsening_pp,
        args.hour18_vmin_drop_pu,args.hour18_under_budget_puh,
        args.min_thermal_gain,args.min_focus_gain_pph)
    if not(0<args.fd_step<=.25 and 0<args.trust_radius<=1 and
          0<args.max_weight_per_direction<=args.trust_radius and
          1<=args.max_iterations<=5 and 0<=args.backtracks<=2 and
          0<=args.min_thermal_gain<1 and
          0<=args.voltage_budget_puh<=args.historical_voltage_budget_puh and
          all(np.isfinite(x) and x>=0 for x in values[3:]) and
          args.max_added_voltage_samples>=0 and args.max_added_overload_samples>=0 and
          args.max_new_voltage_samples>=0 and args.max_new_thermal_samples>=0):
        p.error('Parametros invalidos')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.8.1] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__=='__main__':
    raise SystemExit(main())
