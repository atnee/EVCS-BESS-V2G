#!/usr/bin/env python3
"""V2.8 — surrogate AC sequencial para despacho conjunto BESS/V2G.

Subespaco de combinacoes de despachos existentes, nao um novo MILP completo.
Recalibra sensibilidades direcionais por replays OpenDSS de 24 h em cada
iteracao, resolve LP de busca termico-eletrica e valida novamente 24 h AC.
Mantem V2.7 como incumbente se nenhuma proposta passar nas guardas AC.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import traceback

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from distributed_stochastic import validar_passo_v27 as v27

KEY_LINE=['hour','line','phase']
KEY_VOLT=['hour','bus','phase']
EPS=1e-8


def align_values(ref: pd.DataFrame, other: pd.DataFrame, keys: list[str], field: str) -> np.ndarray:
    """Impede associar derivadas de fases/horas de elementos diferentes."""
    for frame in (ref,other):
        if frame.duplicated(keys).any() or frame[keys+[field]].isna().any().any():
            raise ValueError(f'Dados AC duplicados/ausentes: {field}')
    a=ref.set_index(keys)
    b=other.set_index(keys)
    if len(a)!=len(b) or not a.index.difference(b.index).empty or not b.index.difference(a.index).empty:
        raise ValueError(f'Malhas AC incompatíveis: {field}')
    arr=b.reindex(a.index)[field].to_numpy(float)
    if not np.isfinite(arr).all():
        raise ValueError(f'Valor AC não finito: {field}')
    return arr


def derive_slope(base_ac:dict, probe_ac:dict, step:float) -> dict:
    if not 0<step<=1:
        raise ValueError('Passo de derivada inválido')
    return {
        'line':(align_values(base_ac['branches'],probe_ac['branches'],KEY_LINE,'loading_pct')-
                base_ac['branches']['loading_pct'].to_numpy(float))/step,
        'volt':(align_values(base_ac['voltages'],probe_ac['voltages'],KEY_VOLT,'v_pu')-
                base_ac['voltages']['v_pu'].to_numpy(float))/step,
        'source':(align_values(base_ac['source'],probe_ac['source'],['hour'],'p_kw')-
                  base_ac['source']['p_kw'].to_numpy(float))/step,
    }


def make_schedule(current:dict, targets:list[dict], weights:np.ndarray, cap:dict, repair_kw:float):
    """Soma de direcoes no despacho, reparo energetico por BESS e frota intacta."""
    w=np.asarray(weights,float)
    if w.ndim!=1 or len(w)!=len(targets) or not np.isfinite(w).all() or (w<-EPS).any() or w.sum()>1+EPS:
        raise ValueError('Pesos não-convexos')
    cars=np.asarray(current['cars'],float).copy()
    bess={s:np.asarray(x,float).copy() for s,x in current['bess'].items()}
    for coeff,target in zip(w,targets):
        cars += coeff*(target['cars']-current['cars'])
        for site in bess:
            bess[site] += coeff*(target['bess'][site]-current['bess'][site])
    bess,meta=v27.repair_bess_soc(bess,cap,repair_kw)
    return bess,cars,meta


def qmetrics(ctx,ac:dict):
    v22=ctx['v22']
    qo=v22.score_ac(ctx['s0_orig'],ac)
    qp=v22.score_ac(ctx['s0_policy'],ac)
    focus=v27.focus_metric(ctx['s0_policy']['branches'],ac['branches'],ctx['focus_line'],ctx['focus_phase'])
    return {'orig':qo,'policy':qp,'focus':focus,'ops':ctx['v24'].control_changes(ac)}


def metric_gradient(base:dict, probe:dict, step:float) -> dict:
    return {name:(value-base_value)/step for name,value,base_value in (
      ('thermal',probe['policy']['thermal_aggravation_pp_h'],base['policy']['thermal_aggravation_pp_h']),
      ('voltage',probe['policy']['voltage_aggravation_pu_h'],base['policy']['voltage_aggravation_pu_h']),
      ('volt_orig',probe['orig']['voltage_aggravation_pu_h'],base['orig']['voltage_aggravation_pu_h']),
      ('thermal_orig',probe['orig']['thermal_aggravation_pp_h'],base['orig']['thermal_aggravation_pp_h']),
      ('focus',probe['focus'],base['focus']),
      ('peak',probe['orig']['source_peak_kw'],base['orig']['source_peak_kw']),
      ('vmin',probe['orig']['vmin_pu'],base['orig']['vmin_pu']),
      ('maxload',probe['orig']['max_loading_pct'],base['orig']['max_loading_pct']),
    )}


def build_lp(base_ac:dict, gradients:list[dict], slopes:list[dict],
             guard_table:pd.DataFrame, args, incumbent_metrics:dict):
    """LP conjunto em coordenadas direcionais (w). Restrições são aproximações;
    somente a execução AC posterior pode aprovar uma solução.
    """
    n=len(slopes)
    if not n:
        raise ValueError('Não há direções AC recalibradas')
    if len(gradients)!=n:
        raise ValueError('Direções de métricas não coincidem')
    base_line=base_ac['branches'].reset_index(drop=True)
    base_volt=base_ac['voltages'].reset_index(drop=True)
    base_source=base_ac['source'].reset_index(drop=True)
    L=np.column_stack([d['line'] for d in slopes]); V=np.column_stack([d['volt'] for d in slopes])
    S=np.column_stack([d['source'] for d in slopes])
    T={key:np.array([g[key] for g in gradients],float) for key in gradients[0]}
    # Objetivo linearizado de duas grandezas, com foco térmico e pico.
    th=max(1.,incumbent_metrics['policy']['thermal_aggravation_pp_h'])
    vo=max(.1,incumbent_metrics['policy']['voltage_aggravation_pu_h'])
    fc=max(1.,incumbent_metrics['focus'])
    c=.66*T['thermal']/th + .55*T['voltage']/vo + .24*T['focus']/fc
    c+=.08*np.maximum(T['peak'],0)/max(100.,incumbent_metrics['orig']['source_peak_kw']*.01)
    rows=[];rhs=[];tags=[]
    def add(arr,limit,label):
        rows.append(np.asarray(arr,float));rhs.append(float(limit));tags.append(label)
    add(np.ones(n),args.trust_radius,'TRUST_RADIUS')
    add(T['voltage'],args.voltage_budget_puh,'VOLTAGE_SAME_POLICY_BUDGET')
    add(T['volt_orig'],args.voltage_budget_puh,'VOLTAGE_ORIGINAL_BUDGET')
    add(T['thermal_orig'],args.thermal_orig_budget_pph,'THERMAL_ORIGINAL_BUDGET')
    add(T['peak'],args.peak_allowance_kw,'PEAK_BUDGET')
    add(-T['vmin'],args.vmin_drop_pu,'VMIN_BUDGET')
    add(T['maxload'],args.max_loading_growth_pp,'MAX_LOADING_BUDGET')
    add(T['thermal'],-args.predicted_thermal_gain_pph,'MIN_THERMAL_GAIN')
    add(T['focus'],-args.predicted_focus_gain_pph,'MIN_FOCUS_GAIN')
    # Guardas V2.5 em hora/fase, sempre reforcadas com margem AC.
    bidx=pd.MultiIndex.from_frame(base_line[KEY_LINE]); v0=base_line.loading_pct.to_numpy(float)
    for _,r in guard_table.iterrows():
        key=(int(r['hour']),str(r['line']),str(r['phase']))
        where=bidx.get_indexer([key])[0]
        if where<0:raise ValueError(f'Guarda V2.5 não encontrada: {key}')
        add(L[where], min(99.9,float(r['target_pct'])+.02)-v0[where],f'RETAINED_GUARD:{key}')
    # Nao introduzir agravamentos novos nas linhas-fases proximo a 100%.
    mask=(v0>99.5)|(np.max(v0[:,None]+L*args.trust_radius,axis=1)>99.8)
    active=np.flatnonzero(mask)
    for i in active:
        label=f'LINE:{int(base_line.hour.iloc[i])}:{base_line.line.iloc[i]}/{base_line.phase.iloc[i]}'
        # Elementos acima de 100% são existentes: maximo deterioro muito pequeno;
        # abaixo de 100%: sem ultrapassar 100% na aproximacao.
        limit=max(100.,float(v0[i]))+args.local_loading_tolerance_pp
        add(L[i],limit-v0[i],label)
    # Foco especial: evitar transferência em 20h para três linhas que estavam <=100% na V2.5.
    critical={'ln6350556-2','ln6350556-3','ln5835160-1'}
    focus_mask=(base_line.hour==20)&(base_line.phase=='A')&base_line.line.isin(critical)&(v0>100)
    for i in np.flatnonzero(focus_mask):
        add(L[i],-args.hour20_improvement_pp,f'H20:{base_line.line.iloc[i]}')
    # Proteger tensoes piores nas 18h (sub/sobre) e outras violacoes relevantes.
    volt=base_volt.v_pu.to_numpy(float)
    hours=base_volt.hour.to_numpy(int)
    near=(volt<.952)|(volt>1.048)
    for i in np.flatnonzero(near):
        v=volt[i]
        if v<.952:
            # alvo de nao deterioracao para barras abaixo de 0.95 pu
            tol=args.local_voltage_tolerance_pu*(.5 if hours[i]==18 else 1.)
            add(-V[i],tol,f'VOLT_LOW:{hours[i]}:{base_volt.bus.iloc[i]}/{base_volt.phase.iloc[i]}')
        else:
            add(V[i],args.local_voltage_tolerance_pu,f'VOLT_HIGH:{hours[i]}:{base_volt.bus.iloc[i]}/{base_volt.phase.iloc[i]}')
    # Evitar crescimentos grandes do pico do alimentador em qualquer hora.
    for i in range(len(base_source)):
        add(S[i],args.local_peak_growth_kw,f'SOURCE_H{int(base_source.hour.iloc[i]):02d}')
    A=np.vstack(rows); b=np.asarray(rhs,float)
    if not np.isfinite(A).all() or not np.isfinite(b).all() or not np.isfinite(c).all():
        raise ValueError('Coeficientes LP não finitos')
    return c,A,b,tags,{'n_linear_constraints':len(tags),'n_line_constraints':len(active),
       'n_voltage_constraints':int(near.sum()),'n_guards':len(guard_table),'n_dirs':n,
       'predicted_gradient':{k:vv.tolist() for k,vv in T.items()}}


def solve_lp(c,A,b,trust_radius):
    if not (A.ndim==2 and A.shape[1]==len(c)==len(np.asarray(c))):
        raise ValueError('Dimensão do LP inválida')
    sol=linprog(c,A_ub=A,b_ub=b,bounds=[(0.,trust_radius)]*len(c),method='highs',
                options={'time_limit':60.0})
    if not sol.success:
        return None,{'state':'LP_INFEASIBLE_OR_FAILED','message':sol.message,'code':int(sol.status)}
    w=np.asarray(sol.x,float)
    if np.dot(c,w)>-1e-9 or w.max()<1e-6:
        return None,{'state':'NO_PREDICTED_IMPROVEMENT','objective':float(np.dot(c,w))}
    return w,{'state':'LP_SOLVED','objective':float(np.dot(c,w)),
              'predicted_margin_min':float(np.min(b-A@w)),
              'weights':w.tolist()}


def solve_lp_hierarchical(c,A,b,tags,trust_radius):
    """Planos de aproximação; nenhuma guarda AC é flexibilizada.

    H20 e as amostras locais são restrições de previsão AC linear.
    Se a previsão não for viável, removê-las permite propor um ponto para
    teste real. As guardas no gate_v28 continuam inalteradas.
    """
    filters=[(),('H20:',),('H20:','VOLT_LOW:','VOLT_HIGH:'),
             ('H20:','VOLT_LOW:','VOLT_HIGH:','LINE:')]
    attempts=[]
    for level,prefixes in enumerate(filters):
        mask=np.array([not any(name.startswith(prefix) for prefix in prefixes) for name in tags])
        weights,info=solve_lp(c,A[mask],b[mask],trust_radius)
        attempts.append({'level':level,'removed_forecast_only':list(prefixes),
                         'kept_constraints':int(mask.sum()),'result':info})
        if weights is not None:
            return weights,{'state':'LP_SOLVED' if level==0 else 'LP_FORECAST_CONSTRAINTS_RELAXED',
                  'level':level,'attempts':attempts,
                  'note':'Apenas restrições do surrogate; o gate OpenDSS AC continua integral',
                  'selected':info}
    return None,{'state':'NO_LP_PROPOSAL','attempts':attempts}


def hour20_events(ac:dict, v25_ac:dict):
    """Conta novos eventos de sobrecarga local em relacao a V2.5 às 20h."""
    frame=ac['branches'];reference=v25_ac['branches']
    a=reference.set_index(KEY_LINE)['loading_pct'];b=frame.set_index(KEY_LINE)['loading_pct']
    if len(a)!=len(b) or not a.index.difference(b.index).empty or not b.index.difference(a.index).empty:
        raise ValueError('Elementos faltantes na auditoria das 20h')
    candidate=b.reindex(a.index)
    indices=a.index
    critical=indices.get_level_values('hour')==20
    events=((a<=100.001)&(candidate>100.001)&critical)
    return int(events.sum()), [list(k) for k in indices[events][:30]]


def critical_hour_audit(before_ac:dict, after_ac:dict, args) -> tuple[list[str],dict]:
    """Confronta amostras AC reais nas horas críticas; sem confiar no surrogate."""
    before_l=before_ac['branches'].reset_index(drop=True)
    after_l=align_values(before_l,after_ac['branches'],KEY_LINE,'loading_pct')
    old_l=before_l.loading_pct.to_numpy(float)
    keys=pd.MultiIndex.from_frame(before_l[KEY_LINE])
    failures=[];out={}
    # Limitar piora da linha de 15h, que carrega o maior pico conhecido.
    key=(15,'ln6351510-1','C')
    i=keys.get_indexer([key])[0]
    if i<0:raise ValueError('Linha foco ausente às 15h')
    out['critical_line_15h_before_pct']=float(old_l[i])
    out['critical_line_15h_after_pct']=float(after_l[i])
    if after_l[i]>old_l[i]+args.hour15_max_worsening_pp+1e-6:
        failures.append('CRITICAL_15H_OVERLOAD_WORSENED')
    # As tres linhas de fase A às 20h nao devem sofrer agravamento.
    for name in ('ln6350556-2','ln6350556-3','ln5835160-1'):
        key=(20,name,'A');i=keys.get_indexer([key])[0]
        if i<0:raise ValueError(f'Linha de 20h ausente: {name}')
        out[f'{name}_20h_before_pct']=float(old_l[i])
        out[f'{name}_20h_after_pct']=float(after_l[i])
        if after_l[i]>old_l[i]+args.hour20_max_worsening_pp+1e-6:
            failures.append('H20_WORSENED:'+name)
    # Comparação fase A em todas as barras às 18h: mínimo e severidade acumulada.
    v_before=before_ac['voltages'].reset_index(drop=True)
    v_after=align_values(v_before,after_ac['voltages'],KEY_VOLT,'v_pu')
    a=v_before.v_pu.to_numpy(float)
    sel=(v_before.hour==18)&(v_before.phase=='A')
    if not sel.any():raise ValueError('Sem barras na fase A às 18h')
    old_min=float(np.min(a[sel]));new_min=float(np.min(v_after[sel]))
    old_under=float(np.maximum(.95-a[sel],0).sum())
    new_under=float(np.maximum(.95-v_after[sel],0).sum())
    out.update(voltage_min_18h_A_before_pu=old_min,voltage_min_18h_A_after_pu=new_min,
        undervoltage_18h_A_before_puh=old_under,undervoltage_18h_A_after_puh=new_under)
    if new_min<old_min-args.hour18_vmin_drop_pu-1e-8:
        failures.append('H18_PHASE_A_MIN_VOLTAGE_WORSENED')
    if new_under>old_under+args.hour18_under_budget_puh+1e-8:
        failures.append('H18_PHASE_A_UNDERVOLTAGE_AGGRAVATION')
    return failures,out


def gate_v28(ctx, ac:dict, base:dict, metrics:dict, args, before_ac:dict):
    # Gate V27 já testado + guardas adicionais da V2.8 contra incumbente.
    guard=v27.guard_values(ctx['guard_table'],ac['branches'])
    yes,reasons=v27.accept(ctx['ref_orig'],ctx['ref_policy'],metrics['orig'],metrics['policy'],
          ctx['ref_focus'],metrics['focus'],guard,ctx['ops25'],metrics['ops'],args)
    if metrics['policy']['thermal_aggravation_pp_h'] >= base['policy']['thermal_aggravation_pp_h']-args.min_actual_thermal_gain_pph:
        reasons.append('NO_THERMAL_GAIN_VS_INCUMBENT')
    if metrics['focus'] >= base['focus']-args.min_actual_focus_gain_pph:
        reasons.append('NO_FOCUS_GAIN_VS_INCUMBENT')
    if metrics['policy']['voltage_aggravation_pu_h'] > base['policy']['voltage_aggravation_pu_h']+args.voltage_budget_puh:
        reasons.append('VOLTAGE_WORSE_THAN_V27_BUDGET')
    if metrics['orig']['voltage_aggravation_pu_h'] > base['orig']['voltage_aggravation_pu_h']+args.voltage_budget_puh:
        reasons.append('VOLTAGE_ORIG_WORSE_THAN_V27_BUDGET')
    if metrics['orig']['strict_voltage_samples']>base['orig']['strict_voltage_samples']+args.max_added_voltage_samples:
        reasons.append('EXTRA_VOLTAGE_SAMPLES_VS_V27')
    if metrics['orig']['strict_overload_samples']>base['orig']['strict_overload_samples']+args.max_added_overload_samples:
        reasons.append('EXTRA_THERMAL_SAMPLES_VS_V27')
    if metrics['orig']['vmin_pu']<base['orig']['vmin_pu']-args.vmin_drop_pu:
        reasons.append('MIN_VOLTAGE_WORSE_THAN_V27')
    if metrics['orig']['max_loading_pct']>base['orig']['max_loading_pct']+args.max_loading_growth_pp:
        reasons.append('MAX_LOADING_WORSE_THAN_V27')
    local_reasons,local_audit=critical_hour_audit(before_ac,ac,args)
    reasons.extend(local_reasons)
    old20,_=hour20_events(ctx['ac27'],ctx['ac25'])
    new20,event_list=hour20_events(ac,ctx['ac25'])
    if new20>old20:
        reasons.append('NEW_HOUR20_EVENTS_VS_V27')
    return len(reasons)==0,sorted(set(reasons)),{**local_audit,'hour20_events_vs_v25':new20,'hour20_lines':event_list,
          'hour20_reference_events_vs_v25':old20,'passed_v27_gate':yes}


def ensure_output(out:Path,protected:list[Path]):
    out=out.resolve()
    for item in protected:
        p=item.resolve()
        if out==p or p in out.parents or out in p.parents:
            raise ValueError(f'Pasta de saída sobrepõe entrada: {out} / {p}')
    if (out/'final_report_v28.json').exists() or (out/'iter_01').exists():
        raise FileExistsError('V2.8 já executada neste diretório; escolha outro --output')


def prepare(args):
    out=Path(args.output).resolve();p27=Path(args.v27_dir).resolve()
    ensure_output(out,[Path(args.repo).resolve()/'distributed_stochastic',Path(args.baseline),
      Path(args.v22_dir),Path(args.v23_dir),Path(args.v24_dir),Path(args.v25_dir),Path(args.v26_dir),p27])
    for path in (p27/'final_report_v27.json',p27/'best'/'AC'/'branches.csv',p27/'best'/'report_v27.json'):
        if not path.is_file():raise FileNotFoundError(path)
    ctx=v27.configure(args)
    ctx['v27dir']=p27
    j=v27.read_json(p27/'final_report_v27.json')
    if j['policy']!=ctx['policy']:
        raise ValueError('Política V2.7 diferente da V2.5')
    best=p27/'best'
    v27.assert_dispatch_identity(best,ctx['state'])
    cb=ctx['v24'].read_bess(best,ctx['state']); cc=ctx['v24'].read_cars(best,ctx['state'])
    ctx['v24'].validate_resources(ctx['state']['team'],cb,cc,ctx['state']['places'],ctx['state']['cap'])
    ac=ctx['v22'].read_ac_folder(best/'AC')
    q=qmetrics(ctx,ac)
    rep=v27.read_json(best/'report_v27.json')
    v27.score_close(q['orig'],rep['vs_original_s0'],'V2.7 melhor vs S0 original')
    v27.score_close(q['policy'],rep['vs_policy_s0'],'V2.7 melhor vs S0 politica')
    if not rep['state']=='AC_ELIGIBLE_NOT_CERTIFIED':
        raise RuntimeError('V2.7 best precisa ter sido aceito para esta V2.8')
    ctx['ac27']=ac;ctx['start_metrics']=q;ctx['ops25']=ctx['v24'].control_changes(ctx['ac25'])
    ctx['start']={'bess':cb,'cars':cc,'ac':ac,'metrics':q,'path':best}
    directions={}
    for name,info in ctx['inputs'].items():
        directions['v26_'+name]={'bess':info['bess'],'cars':info['cars'],'path':info['path']}
    directions['v25']={'bess':ctx['base_bess'],'cars':ctx['base_cars'],'path':ctx['v25best']}
    for name in ('soft_alpha_0p125','anchors3_alpha_0p125'):
        path=p27/'candidates'/name
        if not (path/'report_v27.json').is_file():continue
        rep=v27.read_json(path/'report_v27.json')
        if rep['state']!='AC_ELIGIBLE_NOT_CERTIFIED':continue
        v27.assert_dispatch_identity(path,ctx['state'])
        b=ctx['v24'].read_bess(path,ctx['state'])
        c=ctx['v24'].read_cars(path,ctx['state'])
        ctx['v24'].validate_resources(ctx['state']['team'],b,c,ctx['state']['places'],ctx['state']['cap'])
        directions['v27_'+name]={'bess':b,'cars':c,'path':path}
    # Remover direcoes nulas (como o proprio incumbente); não inventar fontes.
    output={}
    for name,tar in directions.items():
        nonzero=np.max(np.abs(tar['cars']-cc))>1e-8 or any(np.max(np.abs(tar['bess'][s]-cb[s]))>1e-8 for s in cb)
        if nonzero:output[name]=tar
    if not output:raise RuntimeError('Nenhuma direção de despacho distinta da V2.7')
    ctx['directions']=output
    return ctx


def evaluate_schedule(ctx, current, targets, weights, folder:Path, repair_kw:float, *, full_report=True):
    state=ctx['state'];v24=ctx['v24'];v22=ctx['v22'];folder.mkdir(parents=True,exist_ok=False)
    bess,cars,repair=make_schedule(current,targets,weights,state['cap'],repair_kw)
    v24.validate_resources(state['team'],bess,cars,state['places'],state['cap'])
    prof=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],bess,cars,state['places'])
    if full_report:
        v22.write_dispatch(folder,bess,cars,state['team']['vehicles'],state['places'],state['cap'])
        prof.to_csv(folder/'injections_reoptimized.csv',index=False)
    v27.write_json(folder/'soc_repair_v28.json',repair)
    ac=v24.evaluate(ctx['repo'],prof,state['sites'],ctx['inv'],ctx['policy'],folder/'AC')
    m=qmetrics(ctx,ac)
    return {'bess':bess,'cars':cars,'ac':ac,'metrics':m,'path':folder}


def precheck(args,ctx):
    out=ctx['output'];out.mkdir(parents=True,exist_ok=True)
    state=ctx['start']
    check={'status':'V28_INPUT_VALIDATED','policy':ctx['policy'],
        'reference':'V2.7 AC eligible only; not an absolute grid certificate',
        'directions':list(ctx['directions']),
        'v27_reference_original':state['metrics']['orig'],
        'v27_reference_same_policy':state['metrics']['policy'],
        'focus_pph':state['metrics']['focus'],
        'S0_sources':'original and same-policy, independently scored',
        'focus':'joint thermal-voltage directional LP, fresh per-iteration 24h AC finite-difference calibration',
        'controls':'V2.5 fase_C_reduzida unchanged; automatic taps/caps replayed',
        'resource_model':'V2.7 SOC repair plus V2.4 resource validation',
        'certification':'not absolute; infrastructure, N-1, protections, transformers not verified'}
    v27.write_json(out/'precheck_v28.json',check)
    print('[V2.8] Precheck OK: V2.7 reproduzida e direções BESS/V2G validadas.')
    print(f'[V2.8] Direções disponíveis: {", ".join(ctx["directions"])}')


def run(args,ctx):
    out=ctx['output'];v27.write_json(out/'run_manifest_v28.json',vars(args))
    shutil.copytree(ctx['v27dir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v28.json',{'champion':'V2.7_UNCHANGED',
       'operational_certification':False})
    current=ctx['start'];start=ctx['start'];steps=[]; accepted=0
    for it in range(1,args.max_iterations+1):
        idir=out/f'iter_{it:02d}';idir.mkdir(parents=True,exist_ok=False)
        base=current['metrics'];targets=[];names=[];slopes=[];grad=[];probe_logs=[]
        print(f'[V2.8] Iteração {it}: recalibrando direções com OpenDSS 24h...',flush=True)
        for name,target in ctx['directions'].items():
            folder=idir/'probes'/name
            try:
                probe=evaluate_schedule(ctx,current,[target],np.array([args.fd_step]),folder,
                                        args.max_bess_repair_kw,full_report=False)
                slope=derive_slope(current['ac'],probe['ac'],args.fd_step)
                gradient=metric_gradient(base,probe['metrics'],args.fd_step)
                targets.append(target);names.append(name);slopes.append(slope);grad.append(gradient)
                probe_logs.append({'name':name,'status':'OK','metrics':probe['metrics'],
                                   'gradients':gradient,'folder':str(folder)})
                print(f'[V2.8] Derivada {name}: dT={gradient["thermal"]:+.3f}; '
                      f'dV={gradient["voltage"]:+.5f}',flush=True)
            except Exception as exc:
                probe_logs.append({'name':name,'status':'REJECTED_OR_ERROR','message':str(exc),
                                   'traceback':traceback.format_exc()})
                print(f'[V2.8] Direção {name} indisponível: {exc}',flush=True)
        v27.write_json(idir/'calibration_report_v28.json',{'probes':probe_logs,
             'fd_step':args.fd_step,'base_reference':base})
        if not slopes:
            steps.append({'iteration':it,'status':'NO_VALID_DIRECTIONS'});break
        try:
            cost,A,b,tags,summary=build_lp(current['ac'],grad,slopes,ctx['guard_table'],args,base)
            weights,lp=solve_lp_hierarchical(cost,A,b,tags,args.trust_radius)
            v27.write_json(idir/'surrogate_v28.json',{'direction_names':names,'lp':lp,'summary':summary,
                   'bounds':{'trust_radius':args.trust_radius,'voltage_budget_puh':args.voltage_budget_puh},
                   'constraint_names':tags})
        except Exception as exc:
            steps.append({'iteration':it,'status':'LP_SETUP_ERROR','reason':str(exc)})
            v27.write_json(idir/'error_lp_v28.json',{'error':str(exc),'traceback':traceback.format_exc()})
            break
        if weights is None:
            steps.append({'iteration':it,'status':lp['state'],'lp':lp});break
        round_accepted=False
        for bt,shrink in enumerate([1.,.5,.25][:args.backtracks+1]):
            folder=idir/f'trial_bt{bt}'
            row={'iteration':it,'backtrack':bt,'weight_scale':shrink,
                 'weights':{n:float(w*shrink) for n,w in zip(names,weights)},'folder':str(folder)}
            try:
                trial=evaluate_schedule(ctx,current,targets,weights*shrink,folder,
                                        args.max_bess_repair_kw)
                ok,reasons,extra=gate_v28(ctx,trial['ac'],current['metrics'],trial['metrics'],args,current['ac'])
                row.update({'status':'AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                            'reasons':reasons,'extra':extra,'metrics':trial['metrics']})
                v27.write_json(folder/'report_v28.json',row)
                print(f'[V2.8] it={it} bt={bt}: T={trial["metrics"]["policy"]["thermal_aggravation_pp_h"]:.3f}, '
                    f'V={trial["metrics"]["policy"]["voltage_aggravation_pu_h"]:.4f}, '
                    f'novas={trial["metrics"]["orig"]["new_overloaded_lines_vs_s0"]}, aceito={ok}',flush=True)
                if ok:
                    round_accepted=True;accepted+=1
                    current=trial
                    shutil.rmtree(out/'best')
                    shutil.copytree(folder,out/'best')
                    v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                    v27.write_json(out/'best'/'ac_score_vs_original_s0.json',trial['metrics']['orig'])
                    v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',trial['metrics']['policy'])
                    v27.write_json(out/'best'/'provenance_v28.json',{'champion':'V2.8_AC_ELIGIBLE',
                      'source':str(folder),'iteration':it,'operational_certification':False})
                    steps.append(row)
                    break
            except Exception as exc:
                row.update({'status':'ERROR_OR_RESOURCE_REJECTED','reason':str(exc)})
                v27.write_json(folder/'error_v28.json',{'message':str(exc),'traceback':traceback.format_exc()})
                print(f'[V2.8] it={it} bt={bt}: erro/rejeição: {exc}',flush=True)
            steps.append(row)
        pd.DataFrame([{'iteration':r.get('iteration'),'backtrack':r.get('backtrack'),
           'status':r.get('status'),'reason':';'.join(r.get('reasons',[])) if isinstance(r.get('reasons',[]),list) else r.get('reason',''),
           'thermal_policy_pph':r.get('metrics',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
           'voltage_policy_puh':r.get('metrics',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
           'folder':r.get('folder','')} for r in steps]).to_csv(out/'todos_candidatos_ac_v28.csv',index=False)
        if not round_accepted:
            break
    # Grava a tabela mesmo quando nenhuma proposta LP pôde ser testada no AC.
    pd.DataFrame([{'iteration':r.get('iteration'), 'backtrack':r.get('backtrack'),
        'status':r.get('status'),
        'reason':';'.join(r.get('reasons',[])) if isinstance(r.get('reasons',[]),list) else r.get('reason',''),
        'thermal_policy_pph':r.get('metrics',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'voltage_policy_puh':r.get('metrics',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
        'folder':r.get('folder','')} for r in steps],
        columns=['iteration','backtrack','status','reason','thermal_policy_pph',
                 'voltage_policy_puh','folder']).to_csv(out/'todos_candidatos_ac_v28.csv',index=False)
    report={'status':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else 'V27_PRESERVED_NO_ACCEPTED_V28',
      'accepted_v28':accepted,'champion':'V2.8' if accepted else 'V2.7',
      'reference_v27':start['metrics'],'best_metrics':current['metrics'],
      'iterations':steps,'policy':ctx['policy'],
      'methods':'fresh directional AC finite differences (24h), local LP joint thermal/voltage constraints, full 24h AC acceptance',
      'warning':'Ainda não é certificação de rede: S0 viola limites e transformadores/proteção/N-1 não verificados'}
    v27.write_json(out/'final_report_v28.json',report)
    print(f'[V2.8] Resultado: {report["status"]}; V2.8 aceitos={accepted}; melhor={report["champion"]}')
    print(f'[V2.8] Relatório: {out/"final_report_v28.json"}')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('baseline','v22-dir','v23-dir','v24-dir','v25-dir','v26-dir','v27-dir'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--repo',default='.')
    p.add_argument('--output',default='./results/v28_conjunto_ac')
    p.add_argument('--task',choices=('precheck','run'),default='precheck')
    p.add_argument('--candidates',default='soft,anchors3')  # v27.configure requer
    p.add_argument('--alphas',default='0.125')             # v27.configure nao usa diretamente
    p.add_argument('--fd-step',type=float,default=.0625)
    p.add_argument('--trust-radius',type=float,default=.18)
    p.add_argument('--max-iterations',type=int,default=2)
    p.add_argument('--backtracks',type=int,default=2)
    p.add_argument('--max-bess-repair-kw',type=float,default=15.)
    p.add_argument('--predicted-thermal-gain-pph',type=float,default=.12)
    p.add_argument('--predicted-focus-gain-pph',type=float,default=.05)
    p.add_argument('--min-actual-thermal-gain-pph',type=float,default=.05)
    p.add_argument('--min-actual-focus-gain-pph',type=float,default=.02)
    p.add_argument('--voltage-budget-puh',type=float,default=.03)
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
    # Compatibilidade com a guarda explicita v27.accept.
    p.add_argument('--min-thermal-gain',type=float,default=.01)
    p.add_argument('--min-focus-gain-pph',type=float,default=.5)
    p.add_argument('--max-new-voltage-samples',type=int,default=20)
    p.add_argument('--max-new-thermal-samples',type=int,default=4)
    args=p.parse_args(argv)
    nonnegative=(args.max_bess_repair_kw,args.predicted_thermal_gain_pph,
         args.predicted_focus_gain_pph,args.min_actual_thermal_gain_pph,
         args.min_actual_focus_gain_pph,args.voltage_budget_puh,args.thermal_orig_budget_pph,
         args.peak_allowance_kw,args.local_peak_growth_kw,args.vmin_drop_pu,
         args.max_loading_growth_pp,args.local_loading_tolerance_pp,
         args.local_voltage_tolerance_pu,args.hour20_improvement_pp,
         args.hour15_max_worsening_pp,args.hour20_max_worsening_pp,
         args.hour18_vmin_drop_pu,args.hour18_under_budget_puh,
         args.min_thermal_gain,args.min_focus_gain_pph)
    if not(0<args.fd_step<=.25 and 0<args.trust_radius<=1 and
           1<=args.max_iterations<=5 and 0<=args.backtracks<=2 and
           all(np.isfinite(x) and x>=0 for x in nonnegative) and
           args.max_added_voltage_samples>=0 and args.max_added_overload_samples>=0 and
           args.max_new_voltage_samples>=0 and args.max_new_thermal_samples>=0):
        p.error('Parâmetros fora das faixas permitidas')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.8] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
