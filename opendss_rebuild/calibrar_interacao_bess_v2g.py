#!/usr/bin/env python3
r"""Auditoria de SUPERPOSIÇÃO elétrica BESS+V2G no OpenDSS IEEE8500.

Executar na raiz do repositório, após instalar patches v0.4.1 e v0.5:
 python .\opendss_rebuild\calibrar_interacao_bess_v2g.py \
   --results .\results\s0_s1_opendss_24h \
   --bess-dir .\results\calibracao_bess_odss_ponta \
   --v2g-dir .\results\calibracao_v2g_odss_ponta \
   --hours 17 18 19 --bess-kw 200 --v2g-kw 200 \
   --output .\results\interacao_bess_v2g_200_200

Esta é APENAS uma perturbação conjunta de injeção líquida, com taps e capacitores
congelados no S1. Não constitui despacho de Kenia/Cesar, não verifica SOC e
não implica disponibilidade física de 200 kW da frota em cada horário.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
from rebuild.contracts import load_context,read_yaml
from rebuild.engine import ODSSEngine,error_checked
from calibrar_bess_opendss import (
    verify_against_archive,select_critical_ids,perturbation_rows,
    power_components,merge_delta,
)


def resolve(root:Path,path:Path)->Path:
    return (path if path.is_absolute() else root/path).resolve()


def audit_manifests(folder_b:Path,folder_v:Path,results:Path,hours:list[int],b_kw:float,v_kw:float)->dict:
    mb=json.loads((folder_b/'manifest.json').read_text(encoding='utf-8'))
    mv=json.loads((folder_v/'manifest.json').read_text(encoding='utf-8'))
    shab=mb.get('s1_profile_sha256');shav=mv.get('s1_profile_sha256')
    sha_file=hashlib.sha256((results/'S1'/'injections_replayed.csv').read_bytes()).hexdigest()
    if not shab or shab!=shav or shab!=sha_file:
        raise ValueError('Calibrações não correspondem ao mesmo perfil S1 da pasta --results')
    source_b=mb.get('source_master_sha256');source_v=mv.get('master_sha256')
    if not source_b or source_b!=source_v:
        raise ValueError('Calibrações não correspondem ao mesmo Master.dss')
    if abs(float(mb['delta_kw'])-b_kw)>1e-6 or abs(float(mv['delta_kw'])-v_kw)>1e-6:
        raise ValueError('Escolha --bess-kw e --v2g-kw iguais aos delta_kw das respectivas calibrações. '
                         'A V0.6 não extrapola as sensibilidades.')
    if not set(hours).issubset(set(mb['hours'])) or not set(hours).issubset(set(mv['hours'])):
        raise ValueError('Horários solicitados ausentes da calibração individual')
    return {'sha_s1':sha_file,'sha_master':source_b}


def superposition_table(baseline:list[dict],combined:list[dict],bcal:pd.DataFrame,vcal:pd.DataFrame,
                        hour:int,keys:list[str],measured:str,slope:str,bkw:float,vkw:float)->pd.DataFrame:
    """Constrói predição linear de duas perturbações independentes e compara com fluxo AC conjunto."""
    before=pd.DataFrame(baseline)
    after=pd.DataFrame(combined)
    if before.duplicated(keys).any() or after.duplicated(keys).any():
        raise ValueError('Nomes duplicados no resultado elétrico conjunto')
    r=before[keys+[measured]].merge(after[keys+[measured]],on=keys,
                              how='outer',suffixes=('_s1','_joint'),indicator=True,validate='one_to_one')
    if len(r)!=len(before) or not r['_merge'].eq('both').all():
        raise ValueError('Mudança na topologia física entre S1 e o teste conjunto')
    r=r.drop(columns='_merge')
    cb=bcal.loc[bcal.hour==hour, keys[1:]+[f'{measured}_before',slope]].copy()
    cv=vcal.loc[vcal.hour==hour, keys[1:]+[f'{measured}_before',slope]].copy()
    if cb.duplicated(keys[1:]).any() or cv.duplicated(keys[1:]).any():
        raise ValueError('Calibração individual contém pares duplicados')
    cb=cb.rename(columns={f'{measured}_before':'baseline_b',slope:'slope_b'})
    cv=cv.rename(columns={f'{measured}_before':'baseline_v',slope:'slope_v'})
    r=r.merge(cb,on=keys[1:],how='left',validate='one_to_one')
    r=r.merge(cv,on=keys[1:],how='left',validate='one_to_one')
    if r[['baseline_b','baseline_v','slope_b','slope_v']].isna().any().any():
        raise ValueError('Faltam sensibilidades para alguma linha/fase ou barra/fase')
    observed_base=r[f'{measured}_s1']
    if max((observed_base-r.baseline_b).abs().max(),(observed_base-r.baseline_v).abs().max())>1e-5:
        raise ValueError('Bases de tensão/corrente diferentes entre as calibrações e a reprodução S1')
    r['pred_joint']=observed_base+r.slope_b*bkw+r.slope_v*vkw
    r['residual_joint']=r[f'{measured}_joint']-r.pred_joint
    r['abs_residual_joint']=r.residual_joint.abs()
    r['observed_joint_delta']=r[f'{measured}_joint']-observed_base
    r['pred_joint_delta']=r.slope_b*bkw+r.slope_v*vkw
    return r


def summary_errors(df:pd.DataFrame)->dict:
    err=df.abs_residual_joint.to_numpy(dtype=float)
    return {'mae':float(np.mean(err)), 'p95':float(np.percentile(err,95)), 'max':float(np.max(err))}


def main_run(args):
    repo=ROOT if args.repo is None else Path(args.repo).resolve()
    results=resolve(repo,args.results)
    folder_b=resolve(repo,args.bess_dir)
    folder_v=resolve(repo,args.v2g_dir)
    out=resolve(repo,args.output)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('Pasta de saída já contém arquivos: '+str(out))
    hours=sorted(set(args.hours))
    if not hours or any(x<0 or x>=24 for x in hours):
        raise ValueError('As horas devem estar entre 0 e 23')
    ctx=load_context(repo,HERE)
    if ctx['settings']['simulation']['controls']!='auto' or ctx['settings']['simulation']['source_mode']!='original':
        raise ValueError('Este diagnóstico requer original/auto em settings.yaml')
    team_b=read_yaml(repo/'configs'/'bess.yaml')
    team_v=read_yaml(repo/'configs'/'v2g.yaml')
    bus_b=str(team_b['connection']['bus']).lower()
    bus_v=str(team_v['standalone']['station']['bus']).lower()
    if bus_b==bus_v:
        raise ValueError('Os dois recursos devem usar barras diferentes')
    for bus in (bus_b,bus_v):
        if ctx['buses'].get(bus)!=set('ABC'):
            raise ValueError('Barra trifásica não encontrada: '+bus)
    cap_b=float(team_b['battery']['power_kw'])
    fleet=team_v['fleet'];station=team_v['standalone']['station']
    cap_v=min(float(fleet['n_vehicles'])*float(fleet['power_kw']),float(station['connection_kw']))
    if not 0<args.bess_kw<=cap_b or not 0<args.v2g_kw<=cap_v:
        raise ValueError('Perturbações excedem limite nominal de potência das unidades')
    start,end=team_v['discharge_window_h']
    if any(not start<=h<end for h in hours):
        raise ValueError('Horas do V2G fora da janela de descarga')
    hashes=audit_manifests(folder_b,folder_v,results,hours,args.bess_kw,args.v2g_kw)
    master_hash=hashlib.sha256(Path(ctx['files']['master']).read_bytes()).hexdigest()
    if master_hash != hashes['sha_master']:
        raise ValueError('Master.dss mudou desde a calibração')
    b_lines=pd.read_csv(folder_b/'sensibilidade_linhas_por_fase.csv')
    v_lines=pd.read_csv(folder_v/'sensibilidade_linhas_por_fase.csv')
    b_volts=pd.read_csv(folder_b/'sensibilidade_tensoes_por_fase.csv')
    v_volts=pd.read_csv(folder_v/'sensibilidade_tensoes_por_fase.csv')
    inp=pd.read_csv(results/'S1'/'injections_replayed.csv')
    inp['bus']=inp.bus.astype(str).str.lower()
    inp['phase']=inp.phase.astype(str).str.upper()
    if inp.duplicated(['hour','bus','phase']).any() or set(inp.hour.unique())!=set(range(24)):
        raise ValueError('O perfil S1 não é completo ou contém duplicatas')
    need=['S1/source.csv','S1/voltages.csv','S1/branches.csv','S1/regulators.csv','S1/capacitors.csv','S0/branches.csv']
    if any(not (results/x).is_file() for x in need):
        raise FileNotFoundError('Faltam arquivos originais S0/S1')
    crit_ids,_=select_critical_ids(results)
    if len(crit_ids)!=41: print(f'ATENÇÃO: {len(crit_ids)} novas linhas (41 na auditoria original).',flush=True)
    out.mkdir(parents=True,exist_ok=True)
    summaries=[];checks=[];bl=[];bv=[]
    for h in hours:
        print(f'[Conjunto] h={h:02d} BESS={args.bess_kw:g} kW; V2G={args.v2g_kw:g} kW',flush=True)
        injection_b=perturbation_rows(h,bus_b,args.bess_kw)
        injection_v=perturbation_rows(h,bus_v,args.v2g_kw)
        injection=pd.concat([injection_b,injection_v],ignore_index=True)
        engine=ODSSEngine(ctx,'original',ctx['settings']['simulation']['ampacity'])
        engine.compile()
        engine.define_assets(pd.concat([inp,injection],ignore_index=True))
        base=None
        for hour in range(h+1):
            base=engine.solve_hour(hour,float(ctx['multipliers'][hour]),inp)
        chk=verify_against_archive(results,h,base)
        checks.append(chk)
        pd.DataFrame(checks).to_csv(out/'baseline_replay_check.csv',index=False)
        if not chk['pass']:
            raise RuntimeError(f'S1 não reproduzido em h{h}. Interrompido.')
        error_checked(engine.dss,'Set controlmode=off')
        frozen=engine.solve_hour(h,float(ctx['multipliers'][h]),inp)
        if abs(frozen['source'][0]['p_kw']-base['source'][0]['p_kw'])>2:
            raise RuntimeError('Potência S1 mudou ao congelar os controles')
        p_b0=power_components(engine,bus_b)
        p_v0=power_components(engine,bus_v)
        combined=engine.solve_hour(h,float(ctx['multipliers'][h]),pd.concat([inp.loc[inp.hour==h],injection],ignore_index=True))
        p_b1=power_components(engine,bus_b)
        p_v1=power_components(engine,bus_v)
        actual_b=-(p_b1['bess_terminal_kw']-p_b0['bess_terminal_kw'])
        actual_v=-(p_v1['bess_terminal_kw']-p_v0['bess_terminal_kw'])
        ds=float(combined['source'][0]['p_kw']-frozen['source'][0]['p_kw'])
        dl=float(combined['source'][0]['loss_kw']-frozen['source'][0]['loss_kw'])
        pc=p_b1['net_pc_absorbed_kw']-p_b0['net_pc_absorbed_kw']
        residue=ds-pc-dl
        if abs(actual_b-args.bess_kw)>max(1.,.01*args.bess_kw) or abs(actual_v-args.v2g_kw)>max(1.,.01*args.v2g_kw):
            raise RuntimeError('Potência medida do BESS/V2G difere do comando')
        if abs(residue)>max(4.,.02*(args.bess_kw+args.v2g_kw)):
            raise RuntimeError(f'Balanço conjunto não fechou: {residue:g} kW')
        if {(x['regulator'],x['tap']) for x in frozen['regulators']} != {(x['regulator'],x['tap']) for x in combined['regulators']}:
            raise RuntimeError('Taps mudaram durante o teste conjunto')
        if {(x['capacitor'],x['states']) for x in frozen['capacitors']} != {(x['capacitor'],x['states']) for x in combined['capacitors']}:
            raise RuntimeError('Capacitores mudaram durante o teste conjunto')
        l=superposition_table(frozen['branches'],combined['branches'],b_lines,v_lines,
              h,['hour','line','phase'],'loading_pct','d_loading_pct_per_kw',args.bess_kw,args.v2g_kw)
        l['is_new_s1_line']=l.line.isin(crit_ids)
        v=superposition_table(frozen['voltages'],combined['voltages'],b_volts,v_volts,
              h,['hour','bus','phase'],'v_pu','d_v_pu_per_kw',args.bess_kw,args.v2g_kw)
        bl.append(l);bv.append(v)
        e_l=summary_errors(l);e_v=summary_errors(v)
        crit=l.loc[l.is_new_s1_line]
        s=dict(hour=h,bess_command_kw=args.bess_kw,v2g_command_kw=args.v2g_kw,
             bess_actual_kw=actual_b,v2g_actual_kw=actual_v,
             delta_source_kw=ds,delta_loss_kw=dl,power_balance_residual_kw=residue,
             vmin_s1_pu=float(v.v_pu_s1.min()),vmin_joint_pu=float(v.v_pu_joint.min()),
             vmax_s1_pu=float(v.v_pu_s1.max()),vmax_joint_pu=float(v.v_pu_joint.max()),
             undervoltage_s1=int((v.v_pu_s1<.95).sum()),undervoltage_joint=int((v.v_pu_joint<.95).sum()),
             overvoltage_s1=int((v.v_pu_s1>1.05).sum()),overvoltage_joint=int((v.v_pu_joint>1.05).sum()),
             overload_samples_s1=int((l.loading_pct_s1>100).sum()),
             overload_samples_joint=int((l.loading_pct_joint>100).sum()),
             critical_phase_overloads_s1=int((crit.loading_pct_s1>100).sum()),
             critical_phase_overloads_joint=int((crit.loading_pct_joint>100).sum()),
             critical_lines_overloaded_joint=int(crit.loc[crit.loading_pct_joint>100,'line'].nunique()),
             line_linear_prediction_mae_pp=e_l['mae'],line_linear_prediction_p95_pp=e_l['p95'],
             line_linear_prediction_max_pp=e_l['max'],
             voltage_linear_prediction_mae_pu=e_v['mae'],voltage_linear_prediction_p95_pu=e_v['p95'],
             voltage_linear_prediction_max_pu=e_v['max'])
        summaries.append(s)
        pd.DataFrame(summaries).to_csv(out/'resumo_interacao.csv',index=False)
        print(f"  sobrecargas {s['overload_samples_s1']}->{s['overload_samples_joint']}; "
              f"Vmax {s['vmax_joint_pu']:.5f}; MAE superposição "
              f"{e_l['mae']:.4g} pp / {e_v['mae']:.4g} pu; "
              f"resíduo balanço {residue:.3f} kW",flush=True)
    pd.concat(bl,ignore_index=True).to_csv(out/'superposicao_linhas.csv',index=False)
    pd.concat(bv,ignore_index=True).to_csv(out/'superposicao_tensoes.csv',index=False)
    pd.DataFrame(summaries).to_csv(out/'resumo_interacao.csv',index=False)
    pd.DataFrame(checks).to_csv(out/'baseline_replay_check.csv',index=False)
    metadata=dict(status='JOINT_PERTURBATION_TEST_NOT_OPTIMIZATION',created_utc=datetime.now(timezone.utc).isoformat(),
                  hours=hours,bess_bus=bus_b,v2g_bus=bus_v,bess_kw=args.bess_kw,v2g_kw=args.v2g_kw,
                  **hashes,network='IEEE8500 full OpenDSS, S1 chronology; local taps/caps frozen',
                  scientific_caveat='Sem validação de SOC, mobilidade V2G, potência hora a hora, controles automáticos ou despacho conjunto. Válido apenas como teste de superposição local.')
    (out/'manifest.json').write_text(json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Resultados:',out)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path)
    ap.add_argument('--results',type=Path,default=Path('results/s0_s1_opendss_24h'))
    ap.add_argument('--bess-dir',type=Path,default=Path('results/calibracao_bess_odss_ponta'))
    ap.add_argument('--v2g-dir',type=Path,default=Path('results/calibracao_v2g_odss_ponta'))
    ap.add_argument('--hours',nargs='+',type=int,default=[17,18,19])
    ap.add_argument('--bess-kw',type=float,default=200.)
    ap.add_argument('--v2g-kw',type=float,default=200.)
    ap.add_argument('--output',type=Path,default=Path('results/interacao_bess_v2g_200_200'))
    args=ap.parse_args()
    main_run(args)

if __name__=='__main__':main()
