#!/usr/bin/env python3
"""V2.9.1: MILP individual com envoltoria termica absoluta e cortes AC.

Evolui a V2.9 sem sobrescrever resultados anteriores. A calibracao pode ser
reutilizada da V2.9 somente apos verificacao de identidade do incumbente,
politica C, passo e dimensoes. Aprovacao so apos OpenDSS 24h.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import traceback
import numpy as np
import pandas as pd

from distributed_stochastic import redespachar_milp_v29 as v29
from distributed_stochastic import otimizar_conjunto_v28 as v28
from distributed_stochastic import otimizar_conjunto_v281 as v281
from distributed_stochastic import validar_passo_v27 as v27
from distributed_stochastic._milp_independente_v291 import make_milp_v291

TARGET23=(23,'ln6351510-1','C')
KEY_LINE=('hour','line','phase')
KEY_VOLT=('hour','bus','phase')
EPS=1e-9


def raw_hash(path:Path)->str:
    x=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            x.update(chunk)
    return x.hexdigest()


def parse_slopes(root:Path, sites:list[str]):
    """Importa as derivadas registradas em disco (sem inferir sinais)."""
    specs={
        'lines':('derivadas_linhas_v22.csv.gz',KEY_LINE,'d_loading_pct_per_kw'),
        'volts':('derivadas_tensoes_v22.csv.gz',KEY_VOLT,'d_v_pu_per_kw'),
        'source':('derivadas_fonte_v22.csv',('hour',),'d_source_kw_per_kw'),
    }
    results={}
    expected={(str(s),d) for s in sites for d in (-1,1)}
    for kind,(filename,cols,val) in specs.items():
        f=root/filename
        if not f.is_file():raise FileNotFoundError(f)
        df=pd.read_csv(f)
        needed=set(cols)|{'site','direction',val}
        if not needed.issubset(df):raise ValueError(f'Colunas ausentes em {f}')
        keycols=list(cols)+['site','direction']
        if df.duplicated(keycols).any() or df.empty or not np.isfinite(df[val].to_numpy(float)).all():
            raise ValueError(f'Derivadas duplicadas/vazias/nao finitas em {f}')
        parsed={}
        for r in df.itertuples(index=False):
            key=(int(r.hour),) if kind=='source' else (int(r.hour),str(getattr(r,cols[1])),str(r.phase))
            site=str(r.site);direction=int(r.direction)
            if (site,direction) not in expected or int(r.direction) not in (-1,1):
                raise ValueError(f'Site ou direcao inesperados: {site} {direction}')
            parsed.setdefault(key,{})[site,direction]=float(getattr(r,val))
        for key,coefs in parsed.items():
            if set(coefs)!=expected:raise ValueError(f'Jacobiano incompleto ({kind}): {key}')
        results[kind]=parsed
    if set(results['source'])!={(i,) for i in range(24)}:
        raise ValueError('Derivadas da fonte nao cobrem 24 horas')
    # O algoritmo original usa source[h], nao source[(h,)].
    results['source']={h[0]:c for h,c in results['source'].items()}
    return results


def reuse_v29_slopes(ctx, args, current, iteration:int):
    """Checa referencia exata antes de reutilizar calculos eletricos V2.9."""
    if iteration!=1 or not args.reuse_v29_calibration:return None
    prev=Path(args.v29_dir).resolve()
    file=prev/'final_report_v29.json'
    manifest_file=prev/'run_manifest_v29.json'
    if not file.is_file() or not manifest_file.is_file():
        raise FileNotFoundError('Resultados completos da V2.9 necessarios para reuso')
    report=v27.read_json(file); manifest=v27.read_json(manifest_file)
    if report.get('state')!='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V29':
        raise ValueError('Reuso autorizado apenas quando a V2.9 preservou a BT0')
    if not math.isclose(float(manifest['delta_kw']),args.delta_kw,abs_tol=1e-12):
        raise ValueError('Delta de calibracao diferente: sem reutilizacao')
    if Path(manifest['v281c_dir']).resolve()!=Path(args.v281c_dir).resolve():
        raise ValueError('Incumbente V2.8.1 corrigida diverge do cache V2.9')
    for name in ('branches','voltages','source','regulators','capacitors'):
        a=prev/'best'/'AC'/f'{name}.csv';b=Path(args.v281c_dir)/'best'/'AC'/f'{name}.csv'
        if not a.is_file() or not b.is_file() or raw_hash(a)!=raw_hash(b):
            raise ValueError(f'AC V2.9 e BT0 diferem: {name}')
    for filename in ('bess_dispatch.csv','vehicle_dispatch.csv','injections_reoptimized.csv','control_policy_v25.json'):
        a=prev/'best'/filename;b=Path(args.v281c_dir)/'best'/filename
        if not a.is_file() or not b.is_file() or raw_hash(a)!=raw_hash(b):
            raise ValueError(f'Despacho ou politica mudou: {filename}')
    manifest=v27.read_json(prev/'iter_01'/'calibration'/'calibration_manifest.json')
    if not math.isclose(float(manifest['delta_kw']),args.delta_kw,abs_tol=1e-12):
        raise ValueError('Manifesto da calibracao V2.9 difere do passo solicitado')
    if manifest.get('method')!='ISOLATED_CHRONOLOGICAL_REPLAY_EACH_HOUR':
        raise ValueError('Metodo de calibracao V2.9 inesperado')
    err=manifest.get('baseline_replay_errors',{})
    if any(not np.isfinite(float(e)) or abs(float(e))>1e-5 for e in err.values()):
        raise ValueError('Reproducao AC da calibracao falhou')
    sites=sorted(set(ctx['state']['cap'])|set(ctx['state']['places']))
    if sorted(manifest.get('buses',[]))!=sites:
        raise ValueError('Sites de calibracao divergentes')
    slopes=parse_slopes(prev/'iter_01'/'calibration',sites)
    for group in ('lines','volts'):
        df=current['ac']['branches' if group=='lines' else 'voltages']
        valid=set(tuple(x) for x in df[list(KEY_LINE if group=='lines' else KEY_VOLT)].itertuples(index=False,name=None))
        if not set(slopes[group]).issubset(valid):raise ValueError('Sensibilidades desatualizadas')
    return slopes


def absolute_thermal(ac):
    loads=ac['branches'].loading_pct.to_numpy(float)
    if not np.isfinite(loads).all():raise ValueError('Carregamentos nao finitos')
    return float(np.maximum(loads-100.,0.).sum())


def strict_voltage(ac):
    values=ac['voltages'].v_pu.to_numpy(float)
    if not np.isfinite(values).all():raise ValueError('Tensoes nao finitas')
    return float((np.maximum(.95-values,0.)+np.maximum(values-1.05,0.)).sum())


def diff_injections(ctx,current,sol):
    places=ctx['state']['places']
    sites=sorted(set(places)|set(ctx['state']['cap']))
    change={}
    for site in sites:
        if site in ctx['state']['cap']:
            old=np.asarray(current['bess'][site],float).copy()
            new=np.asarray(sol['bess'][site],float).copy()
        else:
            old=np.zeros(24);new=np.zeros(24)
        indices=[i for i,p in enumerate(places) if p==site]
        if indices:
            old=old+np.asarray(current['cars'][indices],float).sum(axis=0)
            new=new+np.asarray(sol['cars'][indices],float).sum(axis=0)
        change[site]=new-old
    return change


def predict_value(key,coeff,base_value,changes):
    h=int(key[0]);out=float(base_value)
    for site,delta in changes.items():
        amount=float(delta[h])
        if amount:
            out+=amount*float(coeff[site,1 if amount>0 else -1])
    return out


def learn_cuts(ctx,current,sol,ac,slopes,caps,floors,args):
    """Aumenta margens SOMENTE quando os limites AC falham. Nao modifica gates."""
    changes=diff_injections(ctx,current,sol)
    base_l=current['ac']['branches'].set_index(list(KEY_LINE)).loading_pct
    actual_l=ac['branches'].set_index(list(KEY_LINE)).loading_pct
    if not base_l.index.equals(actual_l.index):
        # Ordenacoes diferentes sao permitidas; chaves fisicas diferentes nao.
        if not base_l.index.is_unique or not actual_l.index.is_unique or set(base_l.index)!=set(actual_l.index):
            raise ValueError('Malha AC de linhas alterada durante tentativa')
    base_v=current['ac']['voltages'].set_index(list(KEY_VOLT)).v_pu
    actual_v=ac['voltages'].set_index(list(KEY_VOLT)).v_pu
    if not base_v.index.is_unique or not actual_v.index.is_unique or set(base_v.index)!=set(actual_v.index):
        raise ValueError('Malha AC de tensao alterada durante tentativa')
    line_candidates=[];voltage_candidates=[]
    for key,coeff in slopes['lines'].items():
        old=float(base_l[key]);new=float(actual_l[key]);pred=predict_value(key,coeff,old,changes)
        limit=caps.get(key, (100.0 if old<=100 else old+args.max_line_growth_pp))
        # A linha critica 23h NUNCA pode piorar nem cruzar limite de alvo.
        if key==TARGET23: limit=min(limit,old-args.target_hour23_gain_pp)
        if new>limit+0.0005:
            resid=max(0.,new-pred)+args.cut_safety_pp
            line_candidates.append((new-limit,key,float(min(args.max_ac_line_bias_pp,resid)),float(limit)))
    for key,coeff in slopes['volts'].items():
        if key not in floors:continue
        old=float(base_v[key]);new=float(actual_v[key]);pred=predict_value(key,coeff,old,changes)
        if new<float(floors[key])-1e-7:
            resid=max(0.,pred-new)+args.cut_voltage_safety_pu
            voltage_candidates.append((floors[key]-new,key,
                float(min(args.max_ac_voltage_bias_pu,resid))))
    line_candidates.sort(reverse=True)
    voltage_candidates.sort(reverse=True)
    added_lines={};added_volts={}
    for _,key,bias,goal in line_candidates[:args.max_ac_cuts]:
        caps[key]=min(caps.get(key,float('inf')),goal)
        added_lines[key]=bias
    for _,key,bias in voltage_candidates[:args.max_ac_cuts]:
        added_volts[key]=bias
    return added_lines,added_volts,{'critical_lines':len(line_candidates),
        'critical_voltages':len(voltage_candidates),'line_keys':[list(x) for x in added_lines],
        'voltage_keys':[list(x) for x in added_volts]}


def gate_v291(ctx,current,ac,metrics,args):
    ok,reasons,audit=v281.dual_voltage_budget_gate(ctx,ac,metrics,current,args)
    base=current['metrics']['orig']; candidate=metrics['orig']
    absolute_diff=float(candidate['strict_thermal_pp_h']-base['strict_thermal_pp_h'])
    if absolute_diff > -args.min_actual_absolute_gain_pph+EPS:
        reasons.append('NO_ABSOLUTE_THERMAL_REDUCTION')
    if candidate['strict_overload_samples']>base['strict_overload_samples']:
        reasons.append('OVERLOAD_SAMPLE_GROWTH_VS_INCUMBENT')
    if candidate['max_loading_pct']>base['max_loading_pct']+args.max_line_growth_pp+EPS:
        reasons.append('GLOBAL_LOADING_GROWTH')
    if candidate['strict_voltage_pu_h']>base['strict_voltage_pu_h']+args.strict_voltage_budget_puh+EPS:
        reasons.append('STRICT_VOLTAGE_SEVERITY_GROWTH')
    before=v29.exact_key(current['ac']['branches'],list(KEY_LINE),TARGET23,'loading_pct')
    after=v29.exact_key(ac['branches'],list(KEY_LINE),TARGET23,'loading_pct')
    if after>before-args.min_actual_hour23_gain_pp+EPS:
        reasons.append('HOUR23_FOCUS_NOT_IMPROVED')
    for n in v29.TARGET20:
        key=(20,n,'A')
        before20=v29.exact_key(current['ac']['branches'],list(KEY_LINE),key,'loading_pct')
        after20=v29.exact_key(ac['branches'],list(KEY_LINE),key,'loading_pct')
        if after20>before20+args.hour20_max_worsening_pp+EPS:
            reasons.append('HOUR20_LOADING_WORSE')
    reasons=sorted(set(reasons))
    detail=dict(audit)
    detail.update(strict_abs_thermal_current=float(base['strict_thermal_pp_h']),
        strict_abs_thermal_candidate=float(candidate['strict_thermal_pp_h']),
        strict_voltage_current=float(base['strict_voltage_pu_h']),
        strict_voltage_candidate=float(candidate['strict_voltage_pu_h']),
        critical_hour23_before=before,critical_hour23_after=after)
    return not reasons,reasons,detail


def prepare(args):
    ctx=v29.prepare(args)
    v28.ensure_output(ctx['output'],[Path(args.v29_dir).resolve()])
    if Path(args.v29_dir).resolve()==Path(args.output).resolve():
        raise ValueError('Saida V2.9.1 nao pode substituir V2.9')
    return ctx


def precheck(args,ctx):
    v29.precheck(args,ctx)
    src=ctx['incumbent']
    out=ctx['output']
    cached=reuse_v29_slopes(ctx,args,src,1)
    caps,floors,focus=v29.additional_milp_guards(ctx,src['ac'],args)
    origin=src['metrics']['orig']
    info={'status':'V291_PRECHECK_OK','source':'V281_CORRIGIDA_BT0',
       'reuse_v29_calibration':cached is not None,
       'current_absolute_thermal_pph':float(origin['strict_thermal_pp_h']),
       'current_absolute_voltage_puh':float(origin['strict_voltage_pu_h']),
       'current_overload_samples':int(origin['strict_overload_samples']),
       'focus23_pct':v29.exact_key(src['ac']['branches'],list(KEY_LINE),TARGET23,'loading_pct'),
       'n_hard_line_guards':len(caps)+1,'n_hard_voltage_guards':len(floors),
       'constraints':'MILP local; eligibility only after OpenDSS 24h'}
    v27.write_json(out/'precheck_v291.json',info)
    print(f'[V2.9.1] Precheck OK; severidade absoluta={origin["strict_thermal_pp_h"]:.3f} pp.h; '
          f'23h={info["focus23_pct"]:.3f}%; cache V2.9={cached is not None}',flush=True)


def run(args,ctx):
    out=ctx['output']
    incumbent=ctx['incumbent'];current=incumbent
    v27.write_json(out/'run_manifest_v291.json',vars(args))
    shutil.copytree(ctx['v281cdir']/'best',out/'best')
    v27.write_json(out/'best'/'provenance_v291.json',{'source':'V2.8.1_CORRIGIDA_BT0_UNCHANGED',
                                                  'absolute_certification':False})
    history=[];steps=[];accepted=0
    for it in range(1,args.max_iterations+1):
        idir=out/f'iter_{it:02d}';idir.mkdir(parents=True,exist_ok=False)
        print(f'[V2.9.1] Calibrando / importando sensibilidades, iteracao {it}...',flush=True)
        try:
            slopes=reuse_v29_slopes(ctx,args,current,it)
            if slopes is None:
                with ctx['v24'].temporary_control_policy(ctx['inv'],ctx['policy']):
                    slopes=ctx['v24'].calibrate_isolated_v24(
                        ctx['repo'],current['profiles'],current['ac'],ctx['s0_policy'],
                        ctx['state']['sites'],args.delta_kw,idir/'calibration')
            caps,floors,focus=v29.additional_milp_guards(ctx,current['ac'],args)
            focus23=v29.exact_key(current['ac']['branches'],list(KEY_LINE),TARGET23,'loading_pct')
            caps[TARGET23]=min(caps.get(TARGET23,float('inf')),
                               focus23-args.target_hour23_gain_pp)
            missing=[k for k in caps if k not in slopes['lines']]
            missing += [k for k in floors if k not in slopes['volts']]
            if missing:raise ValueError(f'Guardas sem sensibilidades: {missing[:10]}')
            v27.write_json(idir/'calibration_summary_v291.json',{
                'source':'v29_verified_reuse' if it==1 and args.reuse_v29_calibration else 'new_ac',
                'line_keys':len(slopes['lines']),'voltage_keys':len(slopes['volts']),
                'guard_lines':len(caps),'guard_voltages':len(floors)})
        except Exception as exc:
            steps.append({'iteration':it,'state':'CALIBRATION_FAILED','error':str(exc)})
            v27.write_json(idir/'error_calibration_v291.json',{'error':str(exc),
                       'traceback':traceback.format_exc()})
            print(f'[V2.9.1] Calibracao falhou: {exc}',flush=True)
            break
        line_bias={};voltage_bias={};found=False
        for bt in range(args.backtracks+1):
            folder=idir/f'trial_bt{bt}'
            trust_b=args.bess_trust_kw*(.5**bt)
            trust_v=args.v2g_trust_kw*(.5**bt)
            record={'iteration':it,'backtrack':bt,'trust_bess_kw':trust_b,
                'trust_v2g_kw':trust_v}
            print(f'[V2.9.1] MILP it={it}, bt={bt}, BESS={trust_b:.1f}, '
                  f'V2G={trust_v:.1f}',flush=True)
            try:
                # A melhoria absoluta prevista e cumulativa, sem relaxar fisica.
                abs_target=absolute_thermal(current['ac'])-args.predicted_absolute_gain_pph
                strict_budget=strict_voltage(current['ac'])+args.strict_voltage_budget_puh
                sol=make_milp_v291(ctx['state']['team'],ctx['state']['places'],ctx['state']['cap'],
                    current['bess'],current['cars'],current['ac'],ctx['s0_policy'],slopes,
                    criterion='relative',trust_bess=trust_b,trust_v2g=trust_v,
                    time_limit=args.time_limit_s,mip_gap=args.mip_gap,
                    hard_line_caps=caps,protected_voltage=floors,
                    focus_target=focus,peak_growth_kw=args.local_peak_growth_kw,
                    absolute_thermal_target=abs_target,strict_voltage_budget_puh=strict_budget,
                    max_line_growth_pp=args.max_line_growth_pp,
                    nonoverloaded_margin_pp=args.nonoverloaded_margin_pp,
                    hard_line_bias=line_bias,hard_voltage_bias=voltage_bias)
                profiles=v29.validate_model_solution(ctx,sol,folder)
                ac=ctx['v24'].evaluate(ctx['repo'],profiles,ctx['state']['sites'],
                                      ctx['inv'],ctx['policy'],folder/'AC')
                metrics=v28.qmetrics(ctx,ac)
                ok,reasons,audit=gate_v291(ctx,current,ac,metrics,args)
                guards=v27.guard_values(ctx['guard_table'],ac['branches'])
                guards.to_csv(folder/'guardas_v291_ac.csv',index=False)
                record.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if ok else 'AC_REJECTED',
                    reasons=reasons,ac=metrics,gate_audit=audit,
                    solver={'status':sol['solver_status'],'message':sol['solver_message'],
                            'gap':sol['mip_gap'],'objective':sol['objective'],
                            'model_stats':sol['model_stats'],
                            'strict_envelope_variables':sol['absolute_thermal_envelope_rows'],
                            'voltage_envelope_variables':sol['absolute_voltage_envelope_rows']})
                if ok:
                    found=True;accepted+=1
                    current={'bess':sol['bess'],'cars':sol['cars'],'profiles':profiles,
                             'ac':ac,'metrics':metrics,'path':folder}
                    shutil.rmtree(out/'best')
                    shutil.copytree(folder,out/'best')
                    v27.write_json(out/'best'/'control_policy_v25.json',ctx['policy'])
                    v27.write_json(out/'best'/'ac_score_vs_original_s0.json',metrics['orig'])
                    v27.write_json(out/'best'/'ac_score_vs_policy_s0.json',metrics['policy'])
                    v27.write_json(out/'best'/'provenance_v291.json',{
                       'source':str(folder),'eligible':True,'absolute_certification':False})
                elif bt<args.backtracks:
                    lb,vb,diag=learn_cuts(ctx,current,sol,ac,slopes,caps,floors,args)
                    for key,bias in lb.items():line_bias[key]=max(line_bias.get(key,0.),bias)
                    for key,bias in vb.items():voltage_bias[key]=max(voltage_bias.get(key,0.),bias)
                    record['learned_ac_cuts']=diag
                    v27.write_json(folder/'cuts_learned_v291.json',{
                       'line_bias':[{ 'key':list(k),'bias_pp':v} for k,v in line_bias.items()],
                       'voltage_bias':[{ 'key':list(k),'bias_pu':v} for k,v in voltage_bias.items()],
                       'summary':diag})
                v27.write_json(folder/'report_v291.json',record)
                print(f'[V2.9.1] AC T={metrics["policy"]["thermal_aggravation_pp_h"]:.3f}, '
                      f'ABS={metrics["orig"]["strict_thermal_pp_h"]:.3f}, '
                      f'V={metrics["policy"]["voltage_aggravation_pu_h"]:.4f}, '
                      f'aceito={ok}',flush=True)
            except Exception as exc:
                record.update(status='ERROR_OR_MILP_INFEASIBLE',error=str(exc))
                folder.mkdir(parents=True,exist_ok=True)
                v27.write_json(folder/'error_v291.json',{'error':str(exc),
                           'traceback':traceback.format_exc()})
                print(f'[V2.9.1] Tentativa falhou: {exc}',flush=True)
            history.append(record)
            if found:break
        steps.append({'iteration':it,'state':'AC_ACCEPTED' if found else 'NO_AC_ACCEPTED',
                     'attempts':sum(x['iteration']==it for x in history)})
        if not found:break
    pd.DataFrame([{'iteration':r['iteration'],'backtrack':r['backtrack'],
        'status':r['status'],'reasons':';'.join(r.get('reasons',[])) or r.get('error',''),
        'thermal_policy_pph':r.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
        'absolute_thermal_pph':r.get('ac',{}).get('orig',{}).get('strict_thermal_pp_h'),
        'strict_overload_samples':r.get('ac',{}).get('orig',{}).get('strict_overload_samples')}
        for r in history],columns=['iteration','backtrack','status','reasons',
             'thermal_policy_pph','absolute_thermal_pph','strict_overload_samples']).to_csv(
             out/'todos_candidatos_ac_v291.csv',index=False)
    report={'state':'AC_ELIGIBLE_NOT_CERTIFIED' if accepted else
                   'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V291',
        'champion':'V2.9.1' if accepted else 'V2.8.1_CORRIGIDA_BT0',
        'accepted_iterations':accepted,'attempts':len(history),'steps':steps,
        'reference_v281c':incumbent['metrics'],'best_metrics':current['metrics'],
        'policy':ctx['policy'],'absolute_certification':False,
        'scope':'New individual BESS V2G MILP, absolute thermal/voltage envelope and AC cutting planes',
        'limitations':['Jacobian AC local aproxima controles discretos; nao prova viabilidade global',
                       'Nao avalia N-1, protecoes, transformadores nem robustez estocastica']}
    v27.write_json(out/'final_report_v291.json',report)
    print(f'[V2.9.1] Resultado: {report["state"]} ({accepted} candidatos aceitos)',flush=True)


def parser():
    p=v29.parser()
    p.description=__doc__
    p.add_argument('--v29-dir',required=True)
    p.set_defaults(output='./results/v291_milp_cortes_ac')
    p.add_argument('--reuse-v29-calibration',action='store_true')
    p.add_argument('--target-hour23-gain-pp',type=float,default=.02)
    p.add_argument('--min-actual-hour23-gain-pp',type=float,default=.005)
    p.add_argument('--predicted-absolute-gain-pph',type=float,default=.05)
    p.add_argument('--min-actual-absolute-gain-pph',type=float,default=.04)
    p.add_argument('--strict-voltage-budget-puh',type=float,default=.02)
    p.add_argument('--max-line-growth-pp',type=float,default=.02)
    p.add_argument('--nonoverloaded-margin-pp',type=float,default=.005)
    p.add_argument('--max-ac-cuts',type=int,default=16)
    p.add_argument('--cut-safety-pp',type=float,default=.03)
    p.add_argument('--max-ac-line-bias-pp',type=float,default=2.)
    p.add_argument('--cut-voltage-safety-pu',type=float,default=.00005)
    p.add_argument('--max-ac-voltage-bias-pu',type=float,default=.003)
    # A V2.9.1 protege contagem tambem vs incumbente (nao so V2.7).
    p.set_defaults(max_added_overload_samples=0)
    return p


def main(argv=None):
    p=parser(); args=p.parse_args(argv)
    checks=(args.delta_kw,args.bess_trust_kw,args.v2g_trust_kw,args.time_limit_s,
      args.target_hour23_gain_pp,args.predicted_absolute_gain_pph,
      args.strict_voltage_budget_puh,args.max_line_growth_pp,
      args.nonoverloaded_margin_pp,args.max_ac_line_bias_pp,args.max_ac_voltage_bias_pu)
    if (any(not np.isfinite(z) or z<=0 for z in checks) or
        args.min_actual_hour23_gain_pp<0 or args.min_actual_absolute_gain_pph<0 or
        not 1<=args.max_ac_cuts<=64 or not 0<=args.backtracks<=3 or
        not 1<=args.max_iterations<=3 or not 0<args.mip_gap<1 or
        args.voltage_budget_puh>args.historical_voltage_budget_puh):
        p.error('Parametros invalidos para V2.9.1')
    try:
        ctx=prepare(args)
        precheck(args,ctx)
        if args.task=='run':run(args,ctx)
        return 0
    except Exception as exc:
        print(f'[V2.9.1] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2

if __name__=='__main__':
    raise SystemExit(main())
