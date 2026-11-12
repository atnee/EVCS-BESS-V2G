#!/usr/bin/env python3
r"""Mede sensibilidades de carregamento/tensão ao BESS no OpenDSS físico.

Primeiro experimento de calibração, NÃO reotimiza Kenia nem valida S2/S4.
Execute a partir da raiz do EVCS-BESS-V2G:
 python .\opendss_rebuild\calibrar_bess_opendss.py --results .\results\s0_s1_opendss_24h --hours 17 18 19 --delta-kw 200 --output .\results\calibracao_bess_odss_v04

Para cada hora solicitada, reproduz S1 desde a hora 0 (mesma sequência
cronológica de controles), congela os taps e estados de capacitores obtidos,
resolva a base sem descarga e introduz +delta-kw em l3029502, fases ABC.
O sinal positivo representa DESCARGA/injeção; as derivadas são locais e
somente válidas próximo do estado operacional verificado.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
from rebuild.contracts import load_context, read_yaml
from rebuild.engine import ODSSEngine, error_checked


def digest(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_df(items:list[dict],keys:list[str],value:str)->pd.DataFrame:
    df=pd.DataFrame(items)
    if not set(keys+[value]) <= set(df.columns):
        raise ValueError('Colunas ausentes no resultado OpenDSS')
    if df.duplicated(keys).any():
        raise ValueError('Linhas duplicadas no resultado: '+str(keys))
    return df


def merge_delta(before:list[dict],after:list[dict],keys:list[str],fields:tuple[str,...],total_kw:float)->pd.DataFrame:
    base=pd.DataFrame(before)
    pert=pd.DataFrame(after)
    if base.duplicated(keys).any() or pert.duplicated(keys).any():
        raise ValueError(f'IDs de corrente ou tensão duplicados: {keys}')
    out=base.merge(pert,on=keys,how='outer',validate='one_to_one',suffixes=('_before','_after'),indicator=True)
    if not out._merge.eq('both').all():
        raise ValueError('Fases/linhas diferentes entre base e perturbação')
    out=out.drop(columns=['_merge'])
    for field in fields:
        out[f'delta_{field}']=out[f'{field}_after']-out[f'{field}_before']
        out[f'd_{field}_per_kw']=out[f'delta_{field}']/total_kw
    return out


def select_critical_ids(root:Path)->tuple[set[str],set[str]]:
    base=pd.read_csv(root/'S0'/'branches.csv',usecols=['line','loading_pct'])
    ev=pd.read_csv(root/'S1'/'branches.csv',usecols=['line','loading_pct'])
    b=set(base.loc[base.loading_pct>100,'line'].str.lower())
    t=set(ev.loc[ev.loading_pct>100,'line'].str.lower())
    return t-b,t


def verify_against_archive(folder:Path,hour:int,baseline:dict)->dict:
    """Reproducibility gate: exact chronological S1 should match prior S1 export."""
    p=folder/'S1'
    src=pd.read_csv(p/'source.csv');src=src.loc[src.hour==hour]
    if len(src)!=1:raise ValueError(f'S1/source.csv sem hora {hour}')
    a=pd.DataFrame(baseline['voltages']);v=pd.read_csv(p/'voltages.csv');v=v.loc[v.hour==hour]
    av=a.merge(v,on=['hour','bus','phase'],suffixes=('_replayed','_saved'),how='outer',indicator=True)
    if len(av)!=len(a) or not av._merge.eq('both').all():
        raise RuntimeError('Fases S1 não coincidem com o arquivo original')
    dv=(av.v_pu_replayed-av.v_pu_saved).abs().max()
    b=pd.DataFrame(baseline['branches']);old=pd.read_csv(p/'branches.csv');old=old.loc[old.hour==hour]
    ab=b.merge(old,on=['hour','line','phase'],suffixes=('_replayed','_saved'),how='outer',indicator=True)
    if len(ab)!=len(b) or not ab._merge.eq('both').all():
        raise RuntimeError('Ramos S1 não coincidem com o arquivo original')
    dl=(ab.loading_pct_replayed-ab.loading_pct_saved).abs().max()
    dp=abs(float(src.iloc[0].p_kw)-baseline['source'][0]['p_kw'])
    rr=pd.DataFrame(baseline['regulators']);oldr=pd.read_csv(p/'regulators.csv');oldr=oldr.loc[oldr.hour==hour]
    ar=rr.merge(oldr,on=['hour','regulator'],suffixes=('_replayed','_saved'),how='outer',indicator=True)
    if not ar._merge.eq('both').all(): raise RuntimeError('Reguladores S1 não coincidem')
    taps_mismatch=int((ar.tap_replayed!=ar.tap_saved).sum())
    cc=pd.DataFrame(baseline['capacitors']);oldc=pd.read_csv(p/'capacitors.csv');oldc=oldc.loc[oldc.hour==hour]
    ac=cc.merge(oldc,on=['hour','capacitor'],suffixes=('_replayed','_saved'),how='outer',indicator=True)
    if not ac._merge.eq('both').all(): raise RuntimeError('Capacitores S1 não coincidem')
    caps_mismatch=int((ac.states_replayed!=ac.states_saved).sum())
    return {'hour':hour,'max_voltage_error_pu':float(dv),'max_loading_error_pp':float(dl),
            'source_power_error_kw':float(dp),'tap_state_mismatch':taps_mismatch,'cap_state_mismatch':caps_mismatch,
            'pass':bool(dv<0.002 and dl<0.5 and dp<5 and taps_mismatch==0 and caps_mismatch==0)}


def element_terminal_p_kw(dss, cls: str, name: str) -> float:
    """Potencia ativa real no terminal 1 (positiva=absorvida; gerador<0).

    Lê o fluxo solucionado, não a consigna kW do componente. Inclui todos os
    condutores do primeiro terminal, pois cargas secundárias podem ter dois.
    """
    if dss.Circuit.SetActiveElement(f'{cls}.{name}') <= 0:
        raise RuntimeError(f'Elemento OpenDSS inexistente: {cls}.{name}')
    if not dss.CktElement.Enabled():
        return 0.0
    n=int(dss.CktElement.NumConductors())
    powers=dss.CktElement.Powers()
    if len(powers)<2*n:
        raise RuntimeError(f'Powers incompleto para {cls}.{name}: {len(powers)} entradas, n={n}')
    out=sum(float(powers[2*i]) for i in range(n))
    if not np.isfinite(out):
        raise RuntimeError(f'Potencia ativa nao finita em {cls}.{name}')
    return out


def power_components(engine: ODSSEngine, bess_bus: str) -> dict:
    """Balanço independente dos consumos/injeções reais no ponto de operacao."""
    dss=engine.dss
    loads=0.0; original_loads=0.0; generators=0.0
    for name in dss.Loads.AllNames():
        if str(name).lower()=='none': continue
        val=element_terminal_p_kw(dss,'Load',str(name))
        loads+=val
        if not str(name).lower().startswith('rebuild_load_'):
            original_loads+=val
    for name in dss.Generators.AllNames():
        if str(name).lower()=='none': continue
        generators+=element_terminal_p_kw(dss,'Generator',str(name))
    bess_terminal=0.0
    for ph in 'ABC':
        name=engine.assets[(bess_bus,ph)]
        bess_terminal+=element_terminal_p_kw(dss,'Load',name['load'])
        bess_terminal+=element_terminal_p_kw(dss,'Generator',name['gen'])
    return {'loads_kw':loads,'original_loads_kw':original_loads,
            'generators_terminal_kw':generators,'bess_terminal_kw':bess_terminal,
            'net_pc_absorbed_kw':loads+generators}


def power_delta_audit(before:dict, after:dict, source_delta_kw:float,
                      loss_delta_kw:float, command_kw:float)->dict:
    """Fechamento: deltaP_fonte = deltaP_PCelements + deltaP_perdas.

    Uma descarga pode reduzir perdas, logo deltaP_fonte NÃO tem de ser -command_kw.
    """
    measured_injection=-(after['bess_terminal_kw']-before['bess_terminal_kw'])
    native_delta=after['original_loads_kw']-before['original_loads_kw']
    delta_pc=after['net_pc_absorbed_kw']-before['net_pc_absorbed_kw']
    expected_source_delta=delta_pc+loss_delta_kw
    residual=source_delta_kw-expected_source_delta
    return {'command_kw':float(command_kw),
            'actual_bess_injection_kw':float(measured_injection),
            'source_delta_kw':float(source_delta_kw),
            'source_delta_plus_bess_kw':float(source_delta_kw+measured_injection),
            'loss_delta_kw':float(loss_delta_kw),
            'original_load_delta_kw':float(native_delta),
            'all_pc_terminal_power_delta_kw':float(delta_pc),
            'source_delta_expected_by_balance_kw':float(expected_source_delta),
            'power_balance_residual_kw':float(residual),
            'loss_reduction_kw':float(-loss_delta_kw)}


def perturbation_rows(hour:int,bus:str,delta_kw:float)->pd.DataFrame:
    return pd.DataFrame([{'hour':hour,'bus':bus,'phase':p,'p_kw':delta_kw/3.0,'q_kvar':0.0} for p in 'ABC'])


def run(args):
    root=ROOT if args.repo is None else Path(args.repo).resolve()
    result_dir=Path(args.results)
    if not result_dir.is_absolute():result_dir=root/result_dir
    result_dir=result_dir.resolve()
    output=Path(args.output)
    if not output.is_absolute():output=root/output
    output=output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Pasta de saída não vazia. Escolha outra --output: '+str(output))
    output.mkdir(parents=True,exist_ok=True)

    ctx=load_context(root, HERE)
    if ctx['settings']['simulation']['controls']!='auto' or ctx['settings']['simulation']['source_mode']!='original':
        raise ValueError('Este teste requer controls:auto e source_mode:original (S1)')
    bess=read_yaml(root/'configs'/'bess.yaml')
    conn=bess['connection']
    bus=str(conn['bus']).lower()
    if str(conn['phases']) != 'ABC' or ctx['buses'].get(bus)!=set('ABC'):
        raise ValueError('BESS não está em barra trifásica: '+bus)
    if args.delta_kw<=0 or args.delta_kw>float(bess['battery']['power_kw']):
        raise ValueError('delta_kw deve estar no intervalo (0, 2200] kW')
    hours=sorted(set(args.hours))
    if not hours or any(h<0 or h>23 for h in hours):raise ValueError('Horas devem estar entre 0 e 23')
    needed=['S1/injections_replayed.csv','S1/source.csv','S1/voltages.csv',
            'S1/branches.csv','S1/regulators.csv','S1/capacitors.csv', 'S0/branches.csv']
    missing=[x for x in needed if not (result_dir/x).is_file()]
    if missing:raise FileNotFoundError('Arquivos de S0/S1 necessários ausentes: '+str(missing))
    input_path=result_dir/'S1'/'injections_replayed.csv'
    inp=pd.read_csv(input_path)
    for col in ('hour','bus','phase','p_kw','q_kvar'):
        if col not in inp.columns:raise ValueError('Coluna de entrada ausente: '+col)
    inp['bus']=inp['bus'].astype(str).str.lower()
    inp['phase']=inp['phase'].astype(str).str.upper()
    if set(inp.hour.unique())!=set(range(24)):
        raise ValueError('Perfil S1 deve conter 24 horas')
    if inp.duplicated(['hour','bus','phase']).any():
        raise ValueError('Perfil S1 tem duplicatas: hora, barra, fase')
    if not np.isfinite(inp[['p_kw','q_kvar']].to_numpy(float)).all():
        raise ValueError('Perfil com valores não finitos')
    new_ids,all_s1_ids=select_critical_ids(result_dir)
    print(f'BESS {bus}: +{args.delta_kw:g} kW descarga trifásica. Linhas novas S1: {len(new_ids)}.')
    if len(new_ids)!=41:
        print(f'AVISO: {len(new_ids)} linhas novas em S1 (41 no estudo anterior). Confira hashes/configurações.')
    checks=[];line_frames=[];voltage_frames=[];summaries=[];power_audits=[]
    for hour in hours:
        print(f'Calibrando h={hour:02d} (reprodução cronológica 0..{hour})',flush=True)
        engine=ODSSEngine(ctx,'original',ctx['settings']['simulation']['ampacity'])
        engine.compile()
        # Ensure all BESS phases have configured OpenDSS generator elements.
        full=pd.concat([inp,perturbation_rows(hour,bus,args.delta_kw)],ignore_index=True)
        engine.define_assets(full)
        base=None
        for j in range(hour+1):
            base=engine.solve_hour(j,float(ctx['multipliers'][j]),inp)
        assert base is not None
        check=verify_against_archive(result_dir,hour,base)
        checks.append(check)
        pd.DataFrame(checks).to_csv(output/'baseline_replay_check.csv',index=False)
        print(f"  Reprodução S1: dVmax={check['max_voltage_error_pu']:.6g} pu; "
              f"dLmax={check['max_loading_error_pp']:.6g} pp; P={check['source_power_error_kw']:.4g} kW",flush=True)
        if not check['pass']:
            pd.DataFrame(checks).to_csv(output/'baseline_replay_check.csv',index=False)
            raise RuntimeError('S1 reproduzido não coincide com os CSVs originais; '
                               'nenhuma sensibilidade será considerada confiável. Examine baseline_replay_check.csv')
        # Freeze the *actual* settled controls; preserve existing tap/cap positions.
        error_checked(engine.dss,'Set controlmode=off')
        frozen_base=engine.solve_hour(hour,float(ctx['multipliers'][hour]),inp)
        if abs(frozen_base['source'][0]['p_kw']-base['source'][0]['p_kw'])>2.0:
            raise RuntimeError('Mudança inesperada do estado de base ao congelar controles')
        comp_before=power_components(engine,bus)
        injection=perturbation_rows(hour,bus,args.delta_kw)
        stimulated=pd.concat([inp.loc[inp.hour==hour],injection],ignore_index=True)
        after=engine.solve_hour(hour,float(ctx['multipliers'][hour]),stimulated)
        comp_after=power_components(engine,bus)
        dsource=after['source'][0]['p_kw']-frozen_base['source'][0]['p_kw']
        dloss=after['source'][0]['loss_kw']-frozen_base['source'][0]['loss_kw']
        audit=power_delta_audit(comp_before,comp_after,dsource,dloss,args.delta_kw)
        audit['hour']=hour
        # Salvar diagnóstico mesmo se alguma verificação física falhar.
        pd.DataFrame([audit]).to_csv(output/f'balanco_potencia_h{hour:02d}.csv',index=False)
        print(f"  Potência: comando={args.delta_kw:.2f} kW; injeção medida="
              f"{audit['actual_bess_injection_kw']:.2f} kW; ΔP_fonte={dsource:.2f} kW; "
              f"ΔP_perdas={dloss:.2f} kW; ΔP_cargas originais="
              f"{audit['original_load_delta_kw']:.2f} kW; "
              f"resíduo={audit['power_balance_residual_kw']:.3f} kW",flush=True)
        # Não exigir -delta_kw na fonte: perdas e cargas dependentes da tensão
        # podem mudar. Exigir potência REAL do elemento BESS e balanço de rede.
        if abs(audit['actual_bess_injection_kw']-args.delta_kw)>max(1.0,0.01*args.delta_kw):
            raise RuntimeError('Potência BESS entregue difere da consigna; '
                               f'veja balanco_potencia_h{hour:02d}.csv')
        if abs(audit['power_balance_residual_kw'])>max(3.,0.02*args.delta_kw):
            raise RuntimeError('Balanço incremental da rede não fechou; '
                               f'veja balanco_potencia_h{hour:02d}.csv')
        reg_a={(r['regulator'],r['tap']) for r in frozen_base['regulators']}
        reg_b={(r['regulator'],r['tap']) for r in after['regulators']}
        cap_a={(r['capacitor'],r['states']) for r in frozen_base['capacitors']}
        cap_b={(r['capacitor'],r['states']) for r in after['capacitors']}
        if reg_a!=reg_b or cap_a!=cap_b:
            raise RuntimeError('Estados de reguladores/capacitores mudaram apesar de controles congelados')
        power_audits.append(audit)
        lines=merge_delta(frozen_base['branches'],after['branches'],['hour','line','phase'],
                          ('current_a','loading_pct'),args.delta_kw)
        lines['is_new_s1_line']=lines.line.isin(new_ids)
        lines['is_any_s1_overloaded_line']=lines.line.isin(all_s1_ids)
        lines['loading_reduction_pp']= -lines.delta_loading_pct
        lines['benefit_pp_per_kw']=-lines.d_loading_pct_per_kw
        lines['bess_bus']=bus
        line_frames.append(lines)
        volt=merge_delta(frozen_base['voltages'],after['voltages'],['hour','bus','phase'],('v_pu',),args.delta_kw)
        volt['bess_bus']=bus
        voltage_frames.append(volt)
        critical=lines[lines.is_new_s1_line]
        summaries.append(dict(hour=hour,delta_bess_kw=args.delta_kw,
                              source_before_kw=frozen_base['source'][0]['p_kw'],
                              source_after_kw=after['source'][0]['p_kw'],
                              source_delta_kw=dsource,
                              bess_actual_injection_kw=audit['actual_bess_injection_kw'],
                              loss_delta_kw=dloss,
                              native_load_delta_kw=audit['original_load_delta_kw'],
                              power_balance_residual_kw=audit['power_balance_residual_kw'],
                              loss_before_kw=frozen_base['source'][0]['loss_kw'],
                              loss_after_kw=after['source'][0]['loss_kw'],
                              min_v_before_pu=min(x['v_pu'] for x in frozen_base['voltages']),
                              min_v_after_pu=min(x['v_pu'] for x in after['voltages']),
                              critical_lines=critical.line.nunique(),
                              critical_line_phases=int(len(critical)),
                              critical_overloaded_before=int((critical.loading_pct_before>100).sum()),
                              critical_overloaded_after=int((critical.loading_pct_after>100).sum()),
                              critical_median_benefit_pp_per_kw=float(critical.benefit_pp_per_kw.median()),
                              critical_min_benefit_pp_per_kw=float(critical.benefit_pp_per_kw.min()),
                              overload_samples_before=int((lines.loading_pct_before>100).sum()),
                              overload_samples_after=int((lines.loading_pct_after>100).sum())))
        print(f'  ΔP_fonte={dsource:.2f} kW; Vmin {summaries[-1]["min_v_before_pu"]:.5f}→{summaries[-1]["min_v_after_pu"]:.5f} pu; '
              f'sobrecargas {summaries[-1]["overload_samples_before"]}→{summaries[-1]["overload_samples_after"]}',flush=True)
    line_out=pd.concat(line_frames,ignore_index=True)
    voltage_out=pd.concat(voltage_frames,ignore_index=True)
    pd.DataFrame(checks).to_csv(output/'baseline_replay_check.csv',index=False)
    pd.DataFrame(power_audits).to_csv(output/'balanco_potencia_todas_horas.csv',index=False)
    pd.DataFrame(summaries).to_csv(output/'resumo_sensibilidades.csv',index=False)
    line_out.to_csv(output/'sensibilidade_linhas_por_fase.csv',index=False)
    voltage_out.to_csv(output/'sensibilidade_tensoes_por_fase.csv',index=False)
    line_out[line_out.is_new_s1_line].to_csv(output/'corredor_41_linhas.csv',index=False)
    manifest={'status':'DIAGNOSTIC_ONLY_NOT_OPTIMIZED',
              'run_utc':datetime.now(timezone.utc).isoformat(),
              'bess_bus':bus,'bess_rating_kw':float(bess['battery']['power_kw']),
              'bess_capacity_kwh':float(bess['battery']['capacity_kwh']),
              'delta_kw':args.delta_kw,'hours':hours,'controls':'S1 auto -> fixed for local derivative',
              'source':'OpenDSS original IEEE8500','ampacity':ctx['settings']['simulation']['ampacity'],
              's1_profile_sha256':digest(input_path),
              'source_master_sha256':digest(ctx['files']['master']),
              'source_balance_validation':'terminal powers + network losses, actual BESS injection',
              'note':'Finite differences are local and may change with tap/cap actions, loading, and phase. '
                     'This test does not perform a 24-h SOC-constrained optimization. '
                     'Do not substitute these numbers directly in old pandapower surrogate.'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Concluído:',output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=None)
    parser.add_argument('--results',type=Path,default=Path('results/s0_s1_opendss_24h'))
    parser.add_argument('--hours',nargs='+',type=int,default=[18])
    parser.add_argument('--delta-kw',type=float,default=200.)
    parser.add_argument('--output',type=Path,default=Path('results/calibracao_bess_odss_v04'))
    args=parser.parse_args()
    run(args)

if __name__=='__main__':main()
