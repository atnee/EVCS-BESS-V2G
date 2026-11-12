#!/usr/bin/env python3
"""Finalizacao de execucao V2.9.3.1 interrompida APOS os testes AC.

Nao executa MILP/OpenDSS. Reconstroi apenas relatorios de quatro tentativas
armazenadas, audita os dados AC, exige best identico a referencia V281C e
registra uma eventual indisponibilidade posterior da V293 (nao a mascara).
Nao promove candidatos rejeitados nem relaxa qualquer gate eletrico.
"""
from __future__ import annotations
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

CHAMPION_FILES=(
    'AC/branches.csv','AC/voltages.csv','AC/source.csv',
    'AC/regulators.csv','AC/capacitors.csv',
    'bess_dispatch.csv','vehicle_dispatch.csv',
    'injections_reoptimized.csv','control_policy_v25.json'
)
AC_FILES=('branches.csv','voltages.csv','source.csv','regulators.csv','capacitors.csv')


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify_best(folder,reference,manifest=None):
    got={}
    for f in CHAMPION_FILES:
        a,b=Path(folder)/f,Path(reference)/f
        if not a.is_file():raise FileNotFoundError(f'Arquivo best ausente: {a}')
        if not b.is_file():raise FileNotFoundError(f'Arquivo do incumbente V281C ausente: {b}')
        ha,hb=sha256(a),sha256(b)
        if ha!=hb:raise RuntimeError(f'Best diverge da V281C: {f}')
        if manifest is not None and manifest.get(f)!=hb:
            raise RuntimeError(f'Manifesto de integridade nao corresponde ao incumbente: {f}')
        got[f]=hb
    return got


def _finite(x,label):
    val=float(x)
    if not math.isfinite(val):raise ValueError('Dado nao finito: '+label)
    return val


def _ac_column(path,required,key_columns,val_column):
    """Leitura em streaming, para nao armazenar o circuito inteiro em RAM."""
    seen=set(); hours=set(); vals=[]; strict_count=0
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        rdr=csv.DictReader(f)
        if not required.issubset(rdr.fieldnames or []):
            raise ValueError(f'Colunas AC ausentes em {path}: {required}')
        for row in rdr:
            h=int(row['hour']); hours.add(h)
            k=tuple(row[k] for k in key_columns)
            if k in seen: raise ValueError(f'Chave AC duplicada em {path}: {k}')
            seen.add(k)
            value=_finite(row[val_column],str(path)+str(k))
            vals.append(value)
    if hours!=set(range(24)):
        raise ValueError(f'Horizonte AC incompleto em {path}: {sorted(hours)}')
    if not vals:raise ValueError(f'AC vazio: {path}')
    return vals, len(seen)


def audit_ac(folder,score):
    ac=Path(folder)/'AC'
    for fname in AC_FILES:
        if not (ac/fname).is_file():raise FileNotFoundError(f'AC de candidato incompleto: {ac/fname}')
    l,nl=_ac_column(ac/'branches.csv',{'hour','line','phase','loading_pct'},('hour','line','phase'),'loading_pct')
    v,nv=_ac_column(ac/'voltages.csv',{'hour','bus','phase','v_pu'},('hour','bus','phase'),'v_pu')
    s,ns=_ac_column(ac/'source.csv',{'hour','p_kw'},('hour',),'p_kw')
    # Verifica existencia e horizonte dos controles, sem inferir estados a partir de nomes.
    _ac_column(ac/'regulators.csv',{'hour','regulator','tap'},('hour','regulator'),'tap')
    # states do capacitor e string, nao necessariamente float.
    with (ac/'capacitors.csv').open(encoding='utf-8-sig',newline='') as f:
        rd=csv.DictReader(f)
        if not {'hour','capacitor','states'}.issubset(rd.fieldnames or []):
            raise ValueError('Estrutura AC de capacitores incompleta')
        hours=set(); seen=set()
        for row in rd:
            h=int(row['hour']);hours.add(h)
            k=(h,row['capacitor']);
            if k in seen:raise ValueError('Duplicidade em estados de capacitores')
            seen.add(k)
        if hours!=set(range(24)):raise ValueError('Horizonte AC de capacitores incompleto')
    values={
      'strict_overload_samples':sum(x>100.001 for x in l),
      'strict_voltage_samples':sum(x<.95-1e-6 or x>1.05+1e-6 for x in v),
      'strict_thermal_pp_h':sum(max(0.,x-100.) for x in l),
      'strict_voltage_pu_h':sum(max(0.,.95-x)+max(0.,x-1.05) for x in v),
      'max_loading_pct':max(l),'vmin_pu':min(v),'vmax_pu':max(v),
      'source_peak_kw':max(s),
    }
    for key,measured in values.items():
        reported=_finite(score[key],key)
        tol=0 if key.endswith('_samples') else 1e-5*max(1.,abs(reported))
        if abs(measured-reported)>tol:
            raise RuntimeError(f'AC e report divergentes em {key}: {measured} vs {reported}')
    return {'branch_phase_hours':nl,'voltage_phase_hours':nv,'source_hour_rows':ns,
            'metrics_recomputed':values,
            'files_sha256':{f:sha256(ac/f) for f in AC_FILES}}


def _atomic_bytes(dest,blob):
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
    fd,tempfile_name=tempfile.mkstemp(prefix='.v2931_finalize_',suffix='.tmp',dir=dest.parent)
    tmp=Path(tempfile_name)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(blob);f.flush();os.fsync(f.fileno())
        os.replace(tmp,dest)
    finally:
        if tmp.exists():tmp.unlink()


def _atomic_json(dest,payload):
    _atomic_bytes(dest,(json.dumps(payload,indent=2,ensure_ascii=False,default=str)+'\n').encode('utf-8'))


def finalize_existing_run(output,reference,v293dir=None,*,verify_only=False):
    output=Path(output).resolve();reference=Path(reference).resolve()
    if not (output/'run_manifest_v2931.json').is_file():
        raise FileNotFoundError('Nao e uma pasta de execucao V2.9.3.1: run_manifest ausente')
    run=read_json(output/'run_manifest_v2931.json')
    if run.get('task')!='run':raise RuntimeError('Manifesto nao corresponde a uma execucao oficial')
    # Confirma caminhos por nome para permitir auditoria cruzada Windows/Linux.
    def base(path):return str(path).replace('\\','/').rstrip('/').split('/')[-1]
    if base(run.get('output',''))!=output.name or base(run.get('v281c_dir',''))!=reference.name:
        raise RuntimeError('Pastas fornecidas divergem do manifesto original')
    manifest_path=output/'iter_01'/'incumbent_sha256_v2931.json'
    if not manifest_path.is_file():raise FileNotFoundError('Manifesto SHA256 do incumbente ausente')
    hashes=verify_best(output/'best',reference/'best',read_json(manifest_path))
    attempts=sorted((output/'iter_01').glob('trial_*'))
    expected=int(run['max_attempts'])
    if len(attempts)!=expected:raise RuntimeError(f'Tentativas incompletas: {len(attempts)} de {expected}')
    history=[];ac_audits={};diagnostics=0;recalibrations=0
    for i,t in enumerate(attempts):
        p=t/'report_v2931.json'
        if not p.is_file():raise FileNotFoundError(f'Relatorio de tentativa ausente: {p}')
        rec=read_json(p)
        if int(rec.get('attempt',-1))!=i or rec.get('strategy') not in t.name:
            raise RuntimeError(f'Identidade da tentativa divergente: {t.name}')
        status=rec.get('status')
        if status not in ('AC_REJECTED','MILP_INFEASIBLE'):
            raise RuntimeError(f'Status nao reconciliavel com modo seguro: {status}')
        if status=='AC_REJECTED':
            if not rec.get('gate_reasons'):raise RuntimeError(f'AC rejeitada sem motivos em {t.name}')
            if rec.get('ac',{}).get('orig',{}).get('strict_pass'):
                raise RuntimeError('Candidato rejeitado possui declaracao de aprovacao absoluta')
            if not all((t/name).is_file() for name in ('bess_dispatch.csv','vehicle_dispatch.csv',
                                                      'injections_reoptimized.csv')):
                raise FileNotFoundError(f'Arquivos de despacho ausentes: {t.name}')
            ac_audits[t.name]=audit_ac(t,rec['ac']['policy'])
        else:
            if not (t/'error_v2931.json').is_file():raise RuntimeError(f'MILP sem log de erro: {t.name}')
        if (t/'diagnostics').is_dir():diagnostics+=1
        if rec.get('recalibration',{}).get('status')=='COMPLETE':recalibrations+=1
        history.append(rec)
    original_metrics=read_json(output/'best'/'ac_score_vs_original_s0.json')
    policy_metrics=read_json(output/'best'/'ac_score_vs_policy_s0.json')
    if (original_metrics.get('strict_pass') or policy_metrics.get('strict_pass')):
        raise RuntimeError('Referência V281C marcada como certificada inesperadamente')
    # Valida o mesmo BEST contra os CSVs efetivamente recuperados.
    best_ac_audit=audit_ac(output/'best',policy_metrics)
    if v293dir is not None:
        prev=Path(v293dir).resolve()/'best'
        try:
            verify_best(prev,reference/'best')
            v293status={'status':'PRESENT_AND_MATCHING','folder':str(prev)}
        except FileNotFoundError as e:
            # O input V293 foi validado no PRECHECK original, mas a finalizacao
            # NAO pode afirmar integridade atual de arquivo ausente.
            v293status={'status':'UNAVAILABLE_AFTER_RUN','detail':str(e),
                        'previous_precheck_status':'VALIDATED_BEFORE_RUN',
                        'note':'Historico indisponivel agora; nao modificado nem restaurado'}
    else:
        v293status={'status':'NOT_SUPPLIED_FOR_POSTRUN_CHECK'}
    original_precheck=read_json(output/'precheck_v2931.json')
    if original_precheck.get('status')!='V2931_PRECHECK_OK':
        raise RuntimeError('Precheck original nao confirma integridade')
    data=[{
       'attempt':r['attempt'],'strategy':r['strategy'],'status':r['status'],
       'reason':';'.join(r.get('gate_reasons',[])) or r.get('error',''),
       'thermal_pph':r.get('ac',{}).get('policy',{}).get('thermal_aggravation_pp_h'),
       'absolute_thermal_pph':r.get('ac',{}).get('orig',{}).get('strict_thermal_pp_h'),
       'voltage_puh':r.get('ac',{}).get('policy',{}).get('voltage_aggravation_pu_h'),
       'tap_changes':r.get('changed_tap_hour_states'),
       'recalibration':r.get('recalibration',{}).get('status')
    } for r in history]
    fields=['attempt','strategy','status','reason','thermal_pph','absolute_thermal_pph',
            'voltage_puh','tap_changes','recalibration']
    import io
    sink=io.StringIO(newline='');w=csv.DictWriter(sink,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(data)
    final={
      'state':'V281_CORRIGIDA_PRESERVED_NO_ACCEPTED_V2931',
      'champion':'V281_CORRIGIDA_BT0','n_accepted':0,'attempts':len(history),
      'recalibrations':recalibrations,'diagnostic_events':diagnostics,
      'experiments':[{'name':r['strategy'],'status':r['status'],
                      'reasons':r.get('gate_reasons',[]),'error':r.get('error')} for r in history],
      'reference_metrics':{'orig':original_metrics,'policy':policy_metrics},
      'best_metrics':{'orig':original_metrics,'policy':policy_metrics},
      'best_files_verified':True,'recovered_from_existing_results':True,
      'historical_v293_postrun':v293status,
      'phase1_does_not_certify':True,'absolute_AC_certification':False,
      'limitations':['Apenas reconstrucao de relatorios; nenhuma nova simulacao MILP/AC',
                     'Resultado eletrico permanece nao certificado em termos absolutos']
    }
    audit={'state':'AUDITED_RECOVERY_V2931','output':str(output),
       'best_sha256_verified':hashes,'best_ac_audit':best_ac_audit,
       'trial_ac_audits':ac_audits,'historical_v293_postrun':v293status,
       'number_of_trials':len(attempts),'number_of_ac_rejected':len(ac_audits),
       'number_of_milp_infeasible':sum(r['status']=='MILP_INFEASIBLE' for r in history),
       'no_new_simulations':True,'no_new_acceptances':True}
    if not verify_only:
        targets=[(output/'todos_candidatos_ac_v2931.csv',sink.getvalue().encode('utf-8')),
                 (output/'final_report_v2931.json',(json.dumps(final,indent=2,ensure_ascii=False)+'\n').encode('utf-8')),
                 (output/'finalizacao_v2931_integridade.json',(json.dumps(audit,indent=2,ensure_ascii=False)+'\n').encode('utf-8'))]
        for path,content in targets:
            if path.is_file() and path.read_bytes()!=content:
                raise RuntimeError(f'Saida de finalizacao ja existente e divergente: {path}')
        for path,content in targets:
            if not path.is_file():_atomic_bytes(path,content)
    return {'status':'VERIFIED_ONLY' if verify_only else 'FINALIZED',
            'state':final['state'],'ac_checked':len(ac_audits),
            'v293_reference':v293status['status'],'best_identical_to_v281c':True,
            'report_path':str(output/'final_report_v2931.json')}


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--v281c-dir',required=True)
    p.add_argument('--v293-dir');p.add_argument('--verify-only',action='store_true')
    args=p.parse_args(argv)
    try:
        result=finalize_existing_run(args.output,args.v281c_dir,args.v293_dir,
                                     verify_only=args.verify_only)
        print('[V2.9.3.1 FINALIZER] '+json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(f'[V2.9.3.1 FINALIZER] ERRO: {type(exc).__name__}: {exc}')
        return 2

if __name__=='__main__':raise SystemExit(main())
