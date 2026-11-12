#!/usr/bin/env python3
"""V3.0 — busca AC conservadora entre despachos reais, sem MILP repetido.

A pesquisa exploratoria e feita apenas sobre direcoes de despacho preexistentes
VALIDADAS quanto a recursos. A otimizacao de baixo numero de dimensoes e uma
busca em passos discretos com validacao AC 24 h para CADA proposta. Nenhuma
inviabilidade matematica ou folga surrogate autoriza aprovacao. Nao certifica
operabilidade absoluta. Nunca altera pastas de entrada.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
import uuid
import numpy as np
import pandas as pd
from distributed_stochastic import redespachar_milp_v29 as v29
from distributed_stochastic import redespachar_milp_v291 as v291
from distributed_stochastic import validar_passo_v27 as v27
from distributed_stochastic import otimizar_conjunto_v28 as v28

CHAMPION = ('AC/branches.csv','AC/voltages.csv','AC/source.csv',
 'AC/regulators.csv','AC/capacitors.csv','bess_dispatch.csv',
 'vehicle_dispatch.csv','injections_reoptimized.csv','control_policy_v25.json')
SOURCES = {
 'v291_bt0': ('v291_dir','iter_01/trial_bt0','report_v291.json'),
 'v2931_bt1': ('v2931_dir','iter_01/trial_01_no_seed_half_trust','report_v2931.json'),
 'v2931_bt2': ('v2931_dir','iter_01/trial_02_recalibrated_no_cuts_half_trust','report_v2931.json'),
}


def digest(path:Path)->str:
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
 return h.hexdigest()


def json_write_atomic(path, data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
 try:
  with tmp.open('w',encoding='utf-8') as f:
   json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
   f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if tmp.exists():tmp.unlink()


def csv_write_atomic(path, records):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
 columns=['name','source','alpha','status','reason','absolute_pph','thermal_policy_pph',
          'voltage_policy_puh','voltage_samples','thermal_samples','new_lines','folder']
 try:
  pd.DataFrame(records,columns=columns).to_csv(tmp,index=False)
  os.replace(tmp,path)
 finally:
  if tmp.exists():tmp.unlink()


def parse_names(text,choices):
 names=[n.strip() for n in text.split(',')]
 if not names or any(not n or n not in choices for n in names) or len(set(names))!=len(names):
  raise ValueError('Fontes invalidas/duplicadas: '+text)
 return names


def parse_alphas(text):
 x=v27.parse_alphas(text)
 if len(x)>12:raise ValueError('No maximo 12 fatores alpha')
 return sorted(x)


def validate_output(output, paths):
 """Evita colisao e sobrescrita, incluindo relacao de ancestralidade."""
 output=Path(output).resolve()
 for p in paths:
  p=Path(p).resolve()
  if output==p or output in p.parents or p in output.parents:
   raise ValueError('Saida sobrepoe entrada protegida: '+str(p))
 if output.is_symlink():raise ValueError('Saida nao pode ser link simbolico')
 return output


def validate_ac_frame(ac):
 """Garante cobertura completa, unicidade e amostras finitas antes de calcular score."""
 for name, keys, column in (
   ('branches',['hour','line','phase'],'loading_pct'),
   ('voltages',['hour','bus','phase'],'v_pu'),
   ('source',['hour'],'p_kw')):
  df=ac[name]
  if df.empty or not set(keys+[column]).issubset(df.columns):
   raise ValueError(f'AC sem dados {name}')
  if df.duplicated(keys).any() or set(df['hour'])!=set(range(24)):
   raise ValueError(f'AC com horas/chaves incompletas {name}')
  if not np.isfinite(df[column].to_numpy(dtype=float)).all():
   raise ValueError(f'AC com valores nao finitos {name}')
  counts=df.groupby('hour').size()
  if counts.nunique()!=1:raise ValueError(f'AC com contagem de elementos variavel por hora: {name}')
 for name,keys in (('regulators',['hour','regulator']),('capacitors',['hour','capacitor'])):
  df=ac[name]
  if df.empty or not set(keys).issubset(df.columns) or df.duplicated(keys).any() or set(df.hour)!=set(range(24)):
   raise ValueError(f'Controles AC invalidos: {name}')
  if df.groupby('hour').size().nunique()!=1:
   raise ValueError(f'Controles variam em quantidade: {name}')


def source_entries(args,ctx):
 out={}
 for name in parse_names(args.sources,SOURCES):
  attr,sub,reportname=SOURCES[name]
  path=Path(getattr(args,attr)).resolve()/sub
  for x in ('vehicle_dispatch.csv','bess_dispatch.csv','injections_reoptimized.csv',reportname):
   if not (path/x).is_file():raise FileNotFoundError(path/x)
  for x in ('branches','voltages','source','regulators','capacitors'):
   if not (path/'AC'/f'{x}.csv').is_file():raise FileNotFoundError(path/'AC'/f'{x}.csv')
  rep=v27.read_json(path/reportname)
  if rep.get('status')!='AC_REJECTED':
   raise ValueError(f'Fonte deve ser candidata AC rejeitada e auditada: {path}')
  v27.assert_dispatch_identity(path,ctx['state'])
  b=ctx['v24'].read_bess(path,ctx['state'])
  cars=ctx['v24'].read_cars(path,ctx['state'])
  ctx['v24'].validate_resources(ctx['state']['team'],b,cars,
                                ctx['state']['places'],ctx['state']['cap'])
  v29.verify_injections(ctx,path,b,cars)
  ac=ctx['v22'].read_ac_folder(path/'AC')
  validate_ac_frame(ac)
  actual=v28.qmetrics(ctx,ac)
  saved=rep.get('ac')
  if not isinstance(saved,dict) or not {'orig','policy','focus','ops'}.issubset(saved):
   raise ValueError(f'Faltam metricas AC arquivadas: {path}')
  v29.assert_scores(actual,saved,name)
  fingerprints={s:digest(path/s) for s in
    ('vehicle_dispatch.csv','bess_dispatch.csv','injections_reoptimized.csv',reportname,
    'AC/branches.csv','AC/voltages.csv','AC/source.csv','AC/regulators.csv','AC/capacitors.csv')}
  out[name]={'path':path,'bess':b,'cars':cars,'metrics':actual,'sha256':fingerprints}
 return out


def combinations(names,alphas, include_mix):
 """Ordem intercalada: nenhuma fonte consome toda a cota antes das outras."""
 specs=[]
 for alpha in alphas:
  for name in names:
   specs.append({'name':f'{name}_a{str(alpha).replace(".","p")}',
     'source':name,'alpha':alpha,'weights':{name:1.}})
  if include_mix and len(names)>=2:
   a,b=names[:2]
   specs.append({'name':f'mix_{a}_{b}_a{str(alpha).replace(".","p")}',
     'source':a+'+'+b,'alpha':alpha,'weights':{a:.5,b:.5}})
 return specs


def build_blend(inc,sources,spec,cap,repair_kw):
 alpha=spec['alpha']; weights=spec['weights']
 if not (0<alpha<=1 and weights and all(0<=float(w)<=1 for w in weights.values()) and abs(sum(weights.values())-1)<1e-12):
  raise ValueError('Pesos invalidos')
 base_c=inc['cars']; base_b=inc['bess']
 cars=(1.-alpha)*base_c.copy()
 bess={s:(1-alpha)*np.asarray(base_b[s],float).copy() for s in base_b}
 for name,w in weights.items():
  src=sources[name]
  cars+=alpha*w*src['cars']
  for s in bess:bess[s]+=alpha*w*np.asarray(src['bess'][s],float)
 bess,repair=v27.repair_bess_soc(bess,cap,repair_kw)
 return bess,cars,repair


def baseline_digest(best):
 return {name:digest(best/name) for name in CHAMPION}


def check_manifest(args,ctx,sources):
 inbest=Path(ctx['v281cdir'])/'best'
 record={'version':'V3.0-AC-CONTINUATION',
  'incumbent':str(inbest.resolve()),'incumbent_sha256':baseline_digest(inbest),
  'sources':{k:{'path':str(v['path']),'sha256':v['sha256']} for k,v in sources.items()},
  'parameters':{'sources':parse_names(args.sources,SOURCES),'alphas':parse_alphas(args.alphas),
                'include_mix':args.include_mix,'repair_kw':args.max_bess_repair_kw,
                'max_ac_trials':args.max_ac_trials},
  'module_sha256':digest(Path(__file__).resolve())}
 return record


def prepare(args):
 protected=[Path(getattr(args,k)) for k in ('repo','baseline','v22_dir','v23_dir',
  'v24_dir','v25_dir','v26_dir','v27_dir','v28_dir','v281_dir','v281c_dir','v291_dir','v2931_dir')]
 # Output deve residir DENTRO do repositorio results/, mas nao pode sobrepor
 # uma pasta historica. A unica excecao ancestral e o repositorio raiz.
 out=Path(args.output).resolve();repo=Path(args.repo).resolve()
 if not out.is_relative_to(repo/'results'):
  raise ValueError('Output precisa estar dentro de --repo/results')
 validate_output(out,[p for p in protected if p.resolve()!=repo])
 if out.exists() and any(x not in ('precheck_v30.json',) for x in os.listdir(out)):
  raise FileExistsError('Pasta V3.0 ja usada. Escolha outra --output')
 ctx=v29.prepare(args)  # valida fontes S0, politica C, contratos e V2.8.1
 inc=ctx['incumbent'];validate_ac_frame(inc['ac'])
 sources=source_entries(args,ctx)
 if not sources:raise ValueError('Nenhuma fonte valida')
 return ctx,sources


def precheck(args,ctx,sources):
 manifest=check_manifest(args,ctx,sources)
 specs=combinations(manifest['parameters']['sources'],manifest['parameters']['alphas'],args.include_mix)
 checks=[]; state=ctx['state']
 for spec in specs:
  try:
   bess,cars,repair=build_blend(ctx['incumbent'],sources,spec,state['cap'],args.max_bess_repair_kw)
   ctx['v24'].validate_resources(state['team'],bess,cars,state['places'],state['cap'])
   checks.append({'name':spec['name'],'resources':'OK',
     'max_repair_kw':max(x['max_adjustment_kw'] for x in repair.values())})
  except Exception as ex:
   checks.append({'name':spec['name'],'resources':'FAILED','reason':str(ex)})
 if not any(x['resources']=='OK' for x in checks):
  raise RuntimeError('Nenhum candidato fisicamente admissivel para AC')
 manifest['resource_precheck']=checks
 manifest['reference_metrics']=ctx['incumbent']['metrics']
 out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
 json_write_atomic(out/'precheck_v30.json',manifest)
 print(f'[V3.0] Precheck OK: 3 BESS, 150 V2G; fontes={len(sources)}; '
       f'passos validos={sum(x["resources"]=="OK" for x in checks)}/{len(checks)}',flush=True)
 return manifest


def validate_manifest(saved,expected):
 for k in ('version','incumbent','incumbent_sha256','sources','parameters','module_sha256'):
  if saved.get(k)!=expected.get(k):raise RuntimeError('Dados/configuracao mudaram desde o precheck: '+k)


def choose_best(rows,inc_metrics):
 accepted=[r for r in rows if r.get('status')=='AC_ELIGIBLE_NOT_CERTIFIED']
 if not accepted:return None
 # Primary physical metric; secondary thermal relative / voltage.
 return min(accepted,key=lambda r:(r['absolute_pph'],r['thermal_policy_pph'],r['voltage_policy_puh']))


def run(args,ctx,sources):
 out=Path(args.output)
 saved=v27.read_json(out/'precheck_v30.json')
 expected=check_manifest(args,ctx,sources)
 validate_manifest(saved,expected)
 if (out/'trials').exists() or (out/'best').exists() or (out/'final_report_v30.json').exists():
  raise FileExistsError('Resultados V3.0 ja presentes; use outra pasta')
 inc=ctx['incumbent']; state=ctx['state']
 ref_hash=baseline_digest(Path(ctx['v281cdir'])/'best')
 rows=[];trials=out/'trials';trials.mkdir(exist_ok=False)
 specs=combinations(parse_names(args.sources,SOURCES),parse_alphas(args.alphas),args.include_mix)
 run_specs=specs[:args.max_ac_trials]
 for i,spec in enumerate(run_specs,1):
  folder=trials/f'{i:02d}_{spec["name"]}'
  rec={'name':spec['name'],'source':spec['source'],'alpha':spec['alpha'],
       'status':'PENDING','reason':'','folder':str(folder)}
  print(f'[V3.0] {i}/{len(run_specs)} {spec["name"]}',flush=True)
  try:
   b,c,repair=build_blend(inc,sources,spec,state['cap'],args.max_bess_repair_kw)
   ctx['v24'].validate_resources(state['team'],b,c,state['places'],state['cap'])
   prof=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                                   b,c,state['places'])
   folder.mkdir(exist_ok=False)
   ctx['v22'].write_dispatch(folder,b,c,state['team']['vehicles'],state['places'],state['cap'])
   prof.to_csv(folder/'injections_reoptimized.csv',index=False)
   json_write_atomic(folder/'repair_v30.json',repair)
   ac=ctx['v24'].evaluate(ctx['repo'],prof,state['sites'],ctx['inv'],ctx['policy'],folder/'AC')
   validate_ac_frame(ac)
   m=v28.qmetrics(ctx,ac)
   yes,reasons,audit=v291.gate_v291(ctx,inc,ac,m,args)
   rec.update(status='AC_ELIGIBLE_NOT_CERTIFIED' if yes else 'AC_REJECTED',
     reason=';'.join(reasons),absolute_pph=float(m['orig']['strict_thermal_pp_h']),
     thermal_policy_pph=float(m['policy']['thermal_aggravation_pp_h']),
     voltage_policy_puh=float(m['policy']['voltage_aggravation_pu_h']),
     voltage_samples=int(m['orig']['strict_voltage_samples']),
     thermal_samples=int(m['orig']['strict_overload_samples']),
     new_lines=int(m['orig']['new_overloaded_lines_vs_s0']))
   json_write_atomic(folder/'report_v30.json',{
     'state':rec['status'],'reasons':reasons,'metrics':m,'acceptance_audit':audit,
     'absolute_grid_certification':False,'provenance':spec})
  except Exception as exc:
   rec.update(status='ERROR_NOT_EVALUATED',reason=f'{type(exc).__name__}: {exc}')
   folder.mkdir(parents=True,exist_ok=True)
   json_write_atomic(folder/'error_v30.json',{'error':str(exc),'traceback':traceback.format_exc()})
  rows.append(rec)
  csv_write_atomic(out/'todos_candidatos_v30.csv',rows)
  print(f'[V3.0] {rec["status"]}: {rec["reason"][:130]}',flush=True)
 winner=choose_best(rows,inc['metrics'])
 best=out/'best'
 if winner is None:
  shutil.copytree(Path(ctx['v281cdir'])/'best',best)
  state_name='V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V30';champion='V281_CORRIGIDA_BT0'
 else:
  shutil.copytree(Path(winner['folder']),best)
  v27.write_json(best/'control_policy_v25.json',ctx['policy'])
  v27.write_json(best/'ac_score_vs_original_s0.json',v27.read_json(best/'report_v30.json')['metrics']['orig'])
  v27.write_json(best/'ac_score_vs_policy_s0.json',v27.read_json(best/'report_v30.json')['metrics']['policy'])
  state_name='AC_ELIGIBLE_NOT_CERTIFIED';champion=winner['name']
 json_write_atomic(best/'provenance_v30.json',{'champion':champion,
    'absolute_grid_certification':False,'selected_from_validated_AC':winner is not None})
 if baseline_digest(Path(ctx['v281cdir'])/'best')!=ref_hash:
  raise RuntimeError('Arquivos protegidos da V2.8.1 foram alterados durante a execucao')
 if winner is None and baseline_digest(best)!=ref_hash:
  raise RuntimeError('Falha: best nao preserva os nove arquivos do incumbente')
 report={'state':state_name,'champion':champion,'absolute_grid_certification':False,
   'candidates_planned':len(specs),'candidates_simulated':sum(r['status'] in ('AC_REJECTED','AC_ELIGIBLE_NOT_CERTIFIED') for r in rows),
   'candidates_accepted':sum(r['status']=='AC_ELIGIBLE_NOT_CERTIFIED' for r in rows),
   'candidates_errors':sum(r['status']=='ERROR_NOT_EVALUATED' for r in rows),
   'reference':inc['metrics'],'chosen':winner,'source_sha256':expected['sources'],
   'reference_preserved':True,
   'method':'low-dimensional directional search on archived dispatches; individual 24h OpenDSS AC replay',
   'limitations':['Nao e MILP global ou prova de otimo.','Nao atesta transformadores, N-1, protecao, harmonicos ou robustez estocastica.',
                  'Candidatos com erro nao sao classificados como eletricamente rejeitados.']}
 json_write_atomic(out/'final_report_v30.json',report)
 print(f'[V3.0] Final: {state_name}, aceitos={report["candidates_accepted"]}, campeao={champion}',flush=True)
 return report


def parser():
 p=v291.parser()
 p.description=__doc__
 p.add_argument('--v291-dir',required=True)
 p.add_argument('--v2931-dir',required=True)
 p.add_argument('--task',choices=('precheck','run'),default='precheck') if '--task' not in p._option_string_actions else None
 p.add_argument('--sources',default='v291_bt0,v2931_bt2')
 p.add_argument('--alphas',default='0.025,0.05,0.1,0.2')
 p.add_argument('--include-mix',action='store_true')
 p.add_argument('--max-ac-trials',type=int,default=6)
 p.add_argument('--max-bess-repair-kw',type=float,default=15.)
 p.set_defaults(output='./results/v30_continuacao_ac',task='precheck',
  # inherited V2.9.1 accepts *only* candidates that improve 23h.
  min_actual_absolute_gain_pph=.04,min_actual_hour23_gain_pp=.005)
 return p


def main(argv=None):
 p=parser();args=p.parse_args(argv)
 try:
  if not 1<=args.max_ac_trials<=20:raise ValueError('--max-ac-trials precisa estar entre 1 e 20')
  if not np.isfinite(args.max_bess_repair_kw) or not 0<=args.max_bess_repair_kw<=60:
   raise ValueError('max-bess-repair-kw invalido')
  if not 0<=args.voltage_budget_puh<=args.historical_voltage_budget_puh:
   raise ValueError('Orcamento historico/incremental divergente')
  ctx,sources=prepare(args)
  if args.task=='precheck':precheck(args,ctx,sources)
  else:run(args,ctx,sources)
  return 0
 except Exception as exc:
  print(f'[V3.0] ERRO: {type(exc).__name__}: {exc}',file=sys.stderr)
  traceback.print_exc()
  return 2


if __name__=='__main__':raise SystemExit(main())
