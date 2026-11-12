#!/usr/bin/env python3
"""V3.0.1: busca AC com caminhos absolutos, filtragem de recursos e trilha persistente.

Nao substitui a V3.0 nem altera qualquer entrada ou gate AC. O modo audit
apenas inventaria artefatos existentes e NUNCA promove candidatos.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
import uuid

import numpy as np
import pandas as pd
from distributed_stochastic import continuacao_ac_v30 as old

VERSION = 'V3.0.1-AC-TRACEABLE'
PRECHECK = 'precheck_v301.json'
CSV = 'todos_candidatos_v301.csv'
FINAL = 'final_report_v301.json'
JOURNAL = 'execution_v301.jsonl'
OUTPUTS = (PRECHECK, CSV, FINAL, JOURNAL, 'trials', 'best')
FILE_COLUMNS = ['name','source','alpha','status','reason','absolute_pph',
                'thermal_policy_pph','voltage_policy_puh','voltage_samples',
                'thermal_samples','new_lines','folder']


def normalize_paths(args):
    """CONGELA os caminhos antes de qualquer chamada OpenDSS que possa mudar cwd."""
    attrs = ('repo','baseline','v22_dir','v23_dir','v24_dir','v25_dir','v26_dir',
             'v27_dir','v28_dir','v281_dir','v281c_dir','v29_dir','v291_dir',
             'v2931_dir','output')
    for attr in attrs:
        if hasattr(args,attr):
            p = getattr(args,attr)
            if p is not None:
                setattr(args,attr,str(Path(p).resolve(strict=False)))
    return args


def write_json_checked(path, payload):
    path=Path(path).resolve()
    old.json_write_atomic(path,payload)
    canonical=json.loads(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    if not path.is_file() or json.loads(path.read_text(encoding='utf-8'))!=canonical:
        raise IOError(f'Falha na persistencia verificavel: {path}')
    return path


def write_csv_checked(path, rows):
    path=Path(path).resolve()
    old.csv_write_atomic(path,rows)
    if not path.is_file() or len(pd.read_csv(path))!=len(rows):
        raise IOError(f'Falha na persistencia CSV: {path}')
    return path


def journal(out, event):
    out=Path(out).resolve()
    obj={'version':VERSION,**event}
    data=json.dumps(obj,ensure_ascii=False,allow_nan=False,sort_keys=True)+'\n'
    with (out/JOURNAL).open('a',encoding='utf-8') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return obj


def prepare(args):
    normalize_paths(args)
    out=Path(args.output)
    repo=Path(args.repo)
    if not out.is_relative_to(repo/'results'):
        raise ValueError('Saida deve estar dentro de --repo/results')
    protected=[Path(getattr(args,k)) for k in ('baseline','v22_dir','v23_dir',
        'v24_dir','v25_dir','v26_dir','v27_dir','v28_dir','v281_dir','v281c_dir',
        'v29_dir','v291_dir','v2931_dir')]
    old.validate_output(out,protected)
    if out.exists():
        extras=set(os.listdir(out))-{PRECHECK}
        if extras:
            raise FileExistsError('Pasta V3.0.1 em uso: '+str(out)+'; nao reutilize output')
    ctx=old.v29.prepare(args)
    old.validate_ac_frame(ctx['incumbent']['ac'])
    sources=old.source_entries(args,ctx)
    if not sources:
        raise ValueError('Nenhuma fonte de despacho auditada')
    return ctx,sources


def manifest(args,ctx,sources):
    base=old.check_manifest(args,ctx,sources)
    base['version']=VERSION
    base['module_sha256']=old.digest(Path(__file__).resolve())
    base['parameters']['output_resolved']=str(Path(args.output).resolve())
    base['parameters']['selection']='filter_resources_before_limit'
    return base


def selected_specifications(checks, specs, max_trials):
    byname={r['name']:r for r in checks}
    return [s for s in specs if byname[s['name']]['resources']=='OK'][:max_trials]


def precheck(args,ctx,sources):
    out=Path(args.output).resolve()
    payload=manifest(args,ctx,sources)
    specs=old.combinations(old.parse_names(args.sources,old.SOURCES),
                           old.parse_alphas(args.alphas),args.include_mix)
    checks=[]
    state=ctx['state']
    for spec in specs:
        try:
            b,c,repair=old.build_blend(ctx['incumbent'],sources,spec,
                                       state['cap'],args.max_bess_repair_kw)
            ctx['v24'].validate_resources(state['team'],b,c,state['places'],state['cap'])
            checks.append({'name':spec['name'],'resources':'OK',
                 'max_repair_kw':max((float(x['max_adjustment_kw'])
                                    for x in repair.values()),default=0.)})
        except Exception as exc:
            checks.append({'name':spec['name'],'resources':'FAILED',
                           'reason':f'{type(exc).__name__}: {exc}'})
    chosen=selected_specifications(checks,specs,args.max_ac_trials)
    if not chosen:
        raise RuntimeError('Nenhum candidato viavel em recursos. Nada sera simulado.')
    payload['resource_precheck']=checks
    payload['selected']=[s['name'] for s in chosen]
    payload['excluded']=[r for r in checks if r['resources']=='FAILED']
    payload['reference_metrics']=ctx['incumbent']['metrics']
    payload['output_absolute']=str(out)
    out.mkdir(parents=True,exist_ok=True)
    write_json_checked(out/PRECHECK,payload)
    print(f'[V3.0.1] Precheck OK: {len(chosen)} candidatos selecionados; '
          f'{len(payload["excluded"])} invalidos removidos ANTES de AC; output={out}',flush=True)
    return payload


def verify_ac_files(folder):
    """Nao aceitar resultado AC so porque a funcao retornou DataFrames."""
    for name in old.CHAMPION[:5]:
        file=Path(folder)/name
        if not file.is_file() or file.stat().st_size==0:
            raise FileNotFoundError('Saida AC nao persistida: '+str(file))


def run_trial(args,ctx,sources,spec,index,total,out):
    folder=out/'trials'/f'{index:02d}_{spec["name"]}'
    rec={'name':spec['name'],'source':spec['source'],'alpha':spec['alpha'],
         'status':'PENDING','reason':'','folder':str(folder)}
    journal(out,{'event':'TRIAL_START','name':spec['name'],'folder':str(folder)})
    print(f'[V3.0.1] {index}/{total} {spec["name"]}',flush=True)
    entered=Path.cwd().resolve()
    changed_cwd=False
    try:
        inc=ctx['incumbent']; state=ctx['state']
        b,c,repair=old.build_blend(inc,sources,spec,state['cap'],args.max_bess_repair_kw)
        ctx['v24'].validate_resources(state['team'],b,c,state['places'],state['cap'])
        prof=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                        b,c,state['places'])
        folder.mkdir(parents=True,exist_ok=False)
        ctx['v22'].write_dispatch(folder,b,c,state['team']['vehicles'],state['places'],state['cap'])
        prof.to_csv(folder/'injections_reoptimized.csv',index=False)
        write_json_checked(folder/'repair_v301.json',repair)
        try:
            ac=ctx['v24'].evaluate(ctx['repo'],prof,state['sites'],ctx['inv'],ctx['policy'],folder/'AC')
        finally:
            changed_cwd=Path.cwd().resolve()!=entered
            if changed_cwd:
                os.chdir(entered)
        old.validate_ac_frame(ac)
        verify_ac_files(folder)
        m=old.v28.qmetrics(ctx,ac)
        yes,reasons,audit=old.v291.gate_v291(ctx,inc,ac,m,args)
        rec.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if yes else 'AC_REJECTED',
            reason=';'.join(reasons),absolute_pph=float(m['orig']['strict_thermal_pp_h']),
            thermal_policy_pph=float(m['policy']['thermal_aggravation_pp_h']),
            voltage_policy_puh=float(m['policy']['voltage_aggravation_pu_h']),
            voltage_samples=int(m['orig']['strict_voltage_samples']),
            thermal_samples=int(m['orig']['strict_overload_samples']),
            new_lines=int(m['orig']['new_overloaded_lines_vs_s0']))
        write_json_checked(folder/'report_v301.json',{
            'state':rec['status'],'reasons':reasons,'metrics':m,'acceptance_audit':audit,
            'cwd_changed_by_AC':changed_cwd,'absolute_grid_certification':False,
            'provenance':spec})
    except Exception as exc:
        rec.update(status='ERROR_NOT_EVALUATED',reason=f'{type(exc).__name__}: {exc}')
        folder.mkdir(parents=True,exist_ok=True)
        write_json_checked(folder/'error_v301.json',{'error':str(exc),
           'traceback':traceback.format_exc(),'cwd_changed_by_AC':changed_cwd})
    finally:
        if Path.cwd().resolve()!=entered:
            os.chdir(entered)
    rec['cwd_changed_by_AC']=changed_cwd
    write_json_checked(folder/'record_v301.json',rec)
    journal(out,{'event':'TRIAL_FINISHED','record':rec})
    return rec


def publish_best(ctx,out,winner,ref_hash):
    """Copia para diretorio temporario e publica somente apos verificar conteudo."""
    path=out/'best'
    if path.exists():
        raise FileExistsError('best/ ja existe: '+str(path))
    tmp=out/('best.tmp-'+uuid.uuid4().hex)
    source=Path(winner['folder']) if winner else Path(ctx['v281cdir'])/'best'
    try:
        shutil.copytree(source,tmp)
        if winner:
            verify_ac_files(tmp)
            old.v27.write_json(tmp/'control_policy_v25.json',ctx['policy'])
            metrics=json.loads((tmp/'report_v301.json').read_text(encoding='utf-8'))['metrics']
            old.v27.write_json(tmp/'ac_score_vs_original_s0.json',metrics['orig'])
            old.v27.write_json(tmp/'ac_score_vs_policy_s0.json',metrics['policy'])
        elif old.baseline_digest(tmp)!=ref_hash:
            raise RuntimeError('Copia do incumbente nao coincide com SHA-256 de referencia')
        write_json_checked(tmp/'provenance_v301.json',{
            'champion': winner['name'] if winner else 'V281_CORRIGIDA_BT0',
            'absolute_grid_certification':False,
            'selected_from_validated_AC':winner is not None})
        if old.baseline_digest(Path(ctx['v281cdir'])/'best')!=ref_hash:
            raise RuntimeError('Incumbente original alterado: publicacao bloqueada')
        tmp.rename(path)
    finally:
        if tmp.exists():shutil.rmtree(tmp)


def run(args,ctx,sources):
    out=Path(args.output).resolve()
    if not (out/PRECHECK).is_file():
        raise FileNotFoundError('Execute precheck V3.0.1 na mesma pasta antes de run')
    saved=json.loads((out/PRECHECK).read_text(encoding='utf-8'))
    old.validate_manifest(saved,manifest(args,ctx,sources))
    if saved.get('output_absolute')!=str(out):
        raise RuntimeError('Output nao coincide com precheck absoluto')
    if any((out/name).exists() for name in OUTPUTS if name!=PRECHECK):
        raise FileExistsError('Pasta ja possui execucao; nao sobrescrever: '+str(out))
    planned=old.combinations(old.parse_names(args.sources,old.SOURCES),
        old.parse_alphas(args.alphas),args.include_mix)
    accepted_names=saved.get('selected',[])
    specs=[s for s in planned if s['name'] in accepted_names]
    if len(specs)!=len(accepted_names) or len(set(accepted_names))!=len(accepted_names):
        raise RuntimeError('Selecao invalida/inconsistente com precheck')
    inc=ctx['incumbent']
    ref_hash=old.baseline_digest(Path(ctx['v281cdir'])/'best')
    (out/'trials').mkdir(exist_ok=False)
    journal(out,{'event':'RUN_STARTED','output':str(out),
                  'cwd':str(Path.cwd().resolve()),'selected':accepted_names})
    rows=[]
    for i,spec in enumerate(specs,1):
        record=run_trial(args,ctx,sources,spec,i,len(specs),out)
        rows.append(record)
        write_csv_checked(out/CSV,rows)
        print(f'[V3.0.1] {record["status"]}: {record["reason"][:160]}',flush=True)
    winner=old.choose_best(rows,inc['metrics'])
    publish_best(ctx,out,winner,ref_hash)
    if old.baseline_digest(Path(ctx['v281cdir'])/'best')!=ref_hash:
        raise RuntimeError('Incumbente alterado apos publicacao')
    state='AC_ELIGIBLE_NOT_CERTIFIED' if winner else 'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V301'
    champion=winner['name'] if winner else 'V281_CORRIGIDA_BT0'
    report={'version':VERSION,'state':state,'champion':champion,
      'absolute_grid_certification':False,'output_absolute':str(out),
      'candidates_planned':len(planned),'candidates_filtered':len(planned)-len(specs),
      'candidates_selected':len(specs),
      'candidates_simulated':sum(r['status'] in ('AC_REJECTED','AC_ELIGIBLE_NOT_CERTIFIED') for r in rows),
      'candidates_accepted':sum(r['status']=='AC_ELIGIBLE_NOT_CERTIFIED' for r in rows),
      'candidates_errors':sum(r['status']=='ERROR_NOT_EVALUATED' for r in rows),
      'reference':inc['metrics'],'chosen':winner,'source_sha256':saved['sources'],
      'acceptance_parameters':{k:getattr(args,k) for k in (
          'historical_voltage_budget_puh','voltage_budget_puh',
          'strict_voltage_budget_puh','max_line_growth_pp',
          'min_actual_absolute_gain_pph','min_actual_hour23_gain_pp',
          'max_added_overload_samples') if hasattr(args,k)},
      'reference_preserved':True,'cwd_changed_trials':[r['name'] for r in rows if r['cwd_changed_by_AC']],
      'method':'feasible-resource-filtered dispatch blending, 24h independent OpenDSS AC replay',
      'limitations':['Nao e MILP global nem certificacao de operabilidade absoluta.',
          'Passos pequenos podem nao atingir o foco 23h.',
          'Nao faz reparo de mobilidade V2G; descarta candidatos inviaveis.',
          'Candidatos sem AC completo sao erro, nunca rejeicao eletrica.']}
    write_json_checked(out/FINAL,report)
    journal(out,{'event':'RUN_FINALIZED','state':state,'champion':champion})
    print(f'[V3.0.1] Final: {state}; aceitos={report["candidates_accepted"]}; '
          f'output={out}',flush=True)
    return report


def audit_only(args):
    """Inventaria resultados parciais sem mudar best/ nem declarar aceites novos."""
    out=Path(args.output).resolve()
    pre=out/PRECHECK
    if not pre.is_file():raise FileNotFoundError(pre)
    data=json.loads(pre.read_text(encoding='utf-8'))
    if data.get('version')!=VERSION:raise RuntimeError('Nao e um precheck V3.0.1')
    folders=sorted((out/'trials').glob('*')) if (out/'trials').is_dir() else []
    records=[]
    for folder in folders:
        file=folder/'record_v301.json'
        if file.is_file():
            rec=json.loads(file.read_text(encoding='utf-8'))
            if rec.get('status') in ('AC_REJECTED','AC_ELIGIBLE_NOT_CERTIFIED'):
                if not (folder/'report_v301.json').is_file():
                    rec['status']='INCOMPLETE_UNVERIFIED'
                else:
                    try:verify_ac_files(folder)
                    except Exception:rec['status']='INCOMPLETE_UNVERIFIED'
            records.append(rec)
        else: records.append({'folder':str(folder),'status':'INCOMPLETE_UNVERIFIED'})
    final=out/FINAL
    result={'version':VERSION,'output':str(out),'has_final_report':final.is_file(),
            'has_best':(out/'best').is_dir(),'trial_records':records,
            'warning':'Auditoria de arquivos, nao reexecuta OpenDSS nem promove candidatos.'}
    write_json_checked(out/'audit_v301.json',result)
    print(f'[V3.0.1] Audit: {len(records)} pastas, final={result["has_final_report"]}, '
          f'best={result["has_best"]}',flush=True)
    return result


def parser():
    p=old.parser()
    p.description=__doc__
    p.set_defaults(output='./results/v301_continuacao_ac_segura',task='precheck')
    p._option_string_actions['--task'].choices=('precheck','run','audit')
    return p


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    try:
        if not 1<=args.max_ac_trials<=20:raise ValueError('--max-ac-trials precisa estar entre 1 e 20')
        if not np.isfinite(args.max_bess_repair_kw) or not 0<=args.max_bess_repair_kw<=60:
            raise ValueError('max-bess-repair-kw invalido')
        if not 0<=args.voltage_budget_puh<=args.historical_voltage_budget_puh:
            raise ValueError('Orcamentos de tensao inconsistentes')
        normalize_paths(args)
        if args.task=='audit': audit_only(args)
        else:
            ctx,sources=prepare(args)
            if args.task=='precheck':precheck(args,ctx,sources)
            else:run(args,ctx,sources)
        return 0
    except Exception as exc:
        print(f'[V3.0.1] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__=='__main__':raise SystemExit(main())
