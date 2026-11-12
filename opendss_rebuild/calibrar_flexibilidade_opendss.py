#!/usr/bin/env python3
r"""Sensibilidades locais BESS/V2G no IEEE8500 OpenDSS sem mudar S0-S1.

Exemplo (na raiz EVCS-BESS-V2G):
python .\opendss_rebuild\calibrar_flexibilidade_opendss.py --resource v2g \
  --results .\results\s0_s1_opendss_24h --hours 17 18 19 --delta-kw 200 \
  --output .\results\calibracao_v2g_odss_ponta

A perturbação do V2G é uma INJEÇÃO LÍQUIDA TESTE na barra anfitriã.
Ela NÃO representa despacho de frota validado nem contabiliza SOC, viagens,
veículos plugados, recarga e limites de portas hora a hora.
Os taps e os capacitores ficam congelados no estado S1 de cada hora.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
from rebuild.contracts import load_context, read_yaml
from rebuild.engine import ODSSEngine, error_checked
from calibrar_bess_opendss import (
    select_critical_ids, verify_against_archive, merge_delta,
    power_components, power_delta_audit, perturbation_rows
)


def resource_definition(root: Path, ctx: dict, resource: str, hours: list[int]):
    if resource == 'bess':
        config = read_yaml(root/'configs'/'bess.yaml')
        bus = str(config['connection']['bus']).lower()
        phases = str(config['connection']['phases'])
        limit_kw = float(config['battery']['power_kw'])
        assumptions = {'capacity_kwh':float(config['battery']['capacity_kwh']),
                       'soc_initial':float(config['battery']['soc_initial'])}
    elif resource == 'v2g':
        config = read_yaml(root/'configs'/'v2g.yaml')
        st = config['standalone']['station']
        bus = str(st['bus']).lower()
        phases = str(st['phases'])
        fleet = config['fleet']
        limit_kw = min(float(st['connection_kw']),
                       float(fleet['n_vehicles'])*float(fleet['power_kw']))
        window = config['discharge_window_h']
        invalid = [h for h in hours if not (float(window[0]) <= h < float(window[1]))]
        if invalid:
            raise ValueError(f'Horas {invalid} fora da janela V2G {window}. '
                             'O teste V2G não admite descarga fora dessa janela.')
        assumptions = {'fleet_vehicles':int(fleet['n_vehicles']),
                       'per_vehicle_power_kw':float(fleet['power_kw']),
                       'station_connection_kw':float(st['connection_kw']),
                       'discharge_window_h':window,
                       'note':'Teto teórico; potência disponível depende da presença e do SOC dos veículos.'}
    else:
        raise ValueError('resource deve ser bess ou v2g')
    if phases != 'ABC' or ctx['buses'].get(bus) != set('ABC'):
        raise ValueError(f'Barra/fases do recurso não existem no alimentador: {bus} {phases}')
    return bus,limit_kw,assumptions


def run(args):
    root = ROOT if args.repo is None else Path(args.repo).resolve()
    results = Path(args.results)
    if not results.is_absolute(): results = root/results
    results = results.resolve()
    output = Path(args.output)
    if not output.is_absolute(): output = root/output
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Pasta de saída não vazia: '+str(output))
    hours = sorted(set(args.hours))
    if not hours or any(h<0 or h>23 for h in hours):
        raise ValueError('Horas devem estar entre 0 e 23')
    ctx = load_context(root,HERE)
    if ctx['settings']['simulation']['controls'] != 'auto' or ctx['settings']['simulation']['source_mode'] != 'original':
        raise ValueError('Requer settings controls:auto e source_mode:original')
    bus, theoretical_limit, assumptions = resource_definition(root,ctx,args.resource,hours)
    if not 0.0 < args.delta_kw <= theoretical_limit:
        raise ValueError(f'Perturbação deve estar entre 0 e {theoretical_limit:g} kW')
    needed = ['S1/injections_replayed.csv','S1/source.csv','S1/voltages.csv',
              'S1/branches.csv','S1/regulators.csv','S1/capacitors.csv','S0/branches.csv']
    missing = [str(f) for f in needed if not (results/f).is_file()]
    if missing: raise FileNotFoundError('Resultados faltantes: '+str(missing))
    inp_path = results/'S1'/'injections_replayed.csv'
    inp = pd.read_csv(inp_path)
    required = {'hour','bus','phase','p_kw','q_kvar'}
    if not required <= set(inp.columns):
        raise ValueError('Campos faltantes no perfil de S1')
    inp['bus'] = inp.bus.astype(str).str.lower()
    inp['phase'] = inp.phase.astype(str).str.upper()
    if set(inp.hour.unique()) != set(range(24)) or inp.duplicated(['hour','bus','phase']).any():
        raise ValueError('Perfil S1 não possui horas 0..23 completas ou contém duplicatas')
    if not np.isfinite(inp[['p_kw','q_kvar']].to_numpy(float)).all():
        raise ValueError('Potências não finitas no perfil S1')
    new_ids, all_s1_ids = select_critical_ids(results)
    print(f'[OpenDSS] Recurso={args.resource.upper()}, barra={bus}, delta={args.delta_kw:.3f} kW, '
          f'41 linhas críticas esperadas; identificadas={len(new_ids)}.',flush=True)
    output.mkdir(parents=True,exist_ok=True)
    replay_checks=[];power_audits=[];lines_all=[];volts_all=[];summaries=[]
    for hour in hours:
        print(f'[{args.resource}] h={hour:02d}: reproduzindo S1 de 0 a {hour}',flush=True)
        engine = ODSSEngine(ctx,'original',ctx['settings']['simulation']['ampacity'])
        engine.compile()
        # Inclui o ponto da perturbação na definição antecipada dos ativos.
        injected = perturbation_rows(hour,bus,args.delta_kw)
        engine.define_assets(pd.concat([inp,injected],ignore_index=True))
        baseline = None
        for j in range(hour+1):
            baseline = engine.solve_hour(j,float(ctx['multipliers'][j]),inp)
        check = verify_against_archive(results,hour,baseline)
        replay_checks.append(check)
        pd.DataFrame(replay_checks).to_csv(output/'baseline_replay_check.csv',index=False)
        if not check['pass']:
            raise RuntimeError(f'Falha na reprodução do S1 em h{hour}; consultar baseline_replay_check.csv')
        error_checked(engine.dss,'Set controlmode=off')
        frozen_base = engine.solve_hour(hour,float(ctx['multipliers'][hour]),inp)
        if abs(frozen_base['source'][0]['p_kw']-baseline['source'][0]['p_kw'])>2.0:
            raise RuntimeError('S1 mudou após congelar controles')
        before_power = power_components(engine,bus)
        after = engine.solve_hour(hour,float(ctx['multipliers'][hour]),
                                  pd.concat([inp.loc[inp.hour==hour],injected],ignore_index=True))
        after_power = power_components(engine,bus)
        ds = float(after['source'][0]['p_kw']-frozen_base['source'][0]['p_kw'])
        dl = float(after['source'][0]['loss_kw']-frozen_base['source'][0]['loss_kw'])
        balance = power_delta_audit(before_power,after_power,ds,dl,args.delta_kw)
        balance.update(hour=hour,resource=args.resource,resource_bus=bus)
        pd.DataFrame([balance]).to_csv(output/f'balanco_potencia_h{hour:02d}.csv',index=False)
        if abs(balance['actual_bess_injection_kw']-args.delta_kw)>max(1.,.01*args.delta_kw):
            raise RuntimeError('Injeção líquida real difere da consigna no ponto de teste')
        if abs(balance['power_balance_residual_kw'])>max(3.,.02*args.delta_kw):
            raise RuntimeError('Balanço de potência não fechou')
        regs = {(r['regulator'],r['tap']) for r in frozen_base['regulators']}
        if regs != {(r['regulator'],r['tap']) for r in after['regulators']}:
            raise RuntimeError('Taps alterados após congelamento')
        caps = {(r['capacitor'],r['states']) for r in frozen_base['capacitors']}
        if caps != {(r['capacitor'],r['states']) for r in after['capacitors']}:
            raise RuntimeError('Capacitores alterados após congelamento')
        power_audits.append(balance)
        line_df = merge_delta(frozen_base['branches'],after['branches'],['hour','line','phase'],
                              ('current_a','loading_pct'),args.delta_kw)
        line_df['is_new_s1_line'] = line_df.line.isin(new_ids)
        line_df['is_any_s1_overloaded_line'] = line_df.line.isin(all_s1_ids)
        line_df['loading_reduction_pp']=-line_df.delta_loading_pct
        line_df['benefit_pp_per_kw']=-line_df.d_loading_pct_per_kw
        line_df['resource_bus']=bus
        line_df['resource']=args.resource
        volt_df = merge_delta(frozen_base['voltages'],after['voltages'],['hour','bus','phase'],
                              ('v_pu',),args.delta_kw)
        volt_df['resource_bus']=bus
        volt_df['resource']=args.resource
        lines_all.append(line_df)
        volts_all.append(volt_df)
        critical = line_df.loc[line_df.is_new_s1_line]
        summaries.append(dict(
            hour=hour,resource=args.resource,resource_bus=bus,delta_kw=args.delta_kw,
            actual_net_injection_kw=balance['actual_bess_injection_kw'],
            source_before_kw=float(frozen_base['source'][0]['p_kw']),
            source_after_kw=float(after['source'][0]['p_kw']),
            source_delta_kw=ds,loss_delta_kw=dl,
            balance_residual_kw=balance['power_balance_residual_kw'],
            vmin_before_pu=float(volt_df.v_pu_before.min()),
            vmin_after_pu=float(volt_df.v_pu_after.min()),
            vmax_before_pu=float(volt_df.v_pu_before.max()),
            vmax_after_pu=float(volt_df.v_pu_after.max()),
            undervoltage_before=int((volt_df.v_pu_before<.95).sum()),
            undervoltage_after=int((volt_df.v_pu_after<.95).sum()),
            overvoltage_before=int((volt_df.v_pu_before>1.05).sum()),
            overvoltage_after=int((volt_df.v_pu_after>1.05).sum()),
            overload_samples_before=int((line_df.loading_pct_before>100).sum()),
            overload_samples_after=int((line_df.loading_pct_after>100).sum()),
            new_s1_overloaded_unique_lines_before=int(critical.loc[critical.loading_pct_before>100,'line'].nunique()),
            new_s1_overloaded_unique_lines_after=int(critical.loc[critical.loading_pct_after>100,'line'].nunique()),
            critical_phase_overloads_before=int((critical.loading_pct_before>100).sum()),
            critical_phase_overloads_after=int((critical.loading_pct_after>100).sum()),
            critical_median_benefit_pp_per_kw=float(critical.benefit_pp_per_kw.median())
        ))
        print(f'  fonte ΔP={ds:.2f} kW; perdas ΔP={dl:.2f} kW; '
              f'sobrecargas {summaries[-1]["overload_samples_before"]}→{summaries[-1]["overload_samples_after"]}; '
              f'vmax {summaries[-1]["vmax_before_pu"]:.5f}→{summaries[-1]["vmax_after_pu"]:.5f}',flush=True)
    line_out=pd.concat(lines_all,ignore_index=True)
    volt_out=pd.concat(volts_all,ignore_index=True)
    pd.DataFrame(replay_checks).to_csv(output/'baseline_replay_check.csv',index=False)
    pd.DataFrame(power_audits).to_csv(output/'balanco_potencia_todas_horas.csv',index=False)
    pd.DataFrame(summaries).to_csv(output/'resumo_sensibilidades.csv',index=False)
    line_out.to_csv(output/'sensibilidade_linhas_por_fase.csv',index=False)
    volt_out.to_csv(output/'sensibilidade_tensoes_por_fase.csv',index=False)
    line_out[line_out.is_new_s1_line].to_csv(output/'corredor_41_linhas.csv',index=False)
    manifest = {
        'status':'LOCAL_NET_INJECTION_DIAGNOSTIC_NOT_DISPATCH_VALIDATION',
        'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'resource':args.resource,'resource_bus':bus,
        'delta_kw':float(args.delta_kw),'hours':hours,
        'theoretical_power_cap_kw':float(theoretical_limit),
        'resource_assumptions':assumptions,
        'network':'original OpenDSS IEEE8500, controlled S1 state and frozen local taps/caps',
        's1_profile_sha256':hashlib.sha256(inp_path.read_bytes()).hexdigest(),
        'master_sha256':hashlib.sha256(Path(ctx['files']['master']).read_bytes()).hexdigest(),
        'scientific_caveat':('Perturbação positiva é uma variação líquida de injeção no ponto. '
            'No V2G não separa redução de carregamento do veículo de descarga bidirecional, '
            'não garante disponibilidade individual/SOC/mobilidade. '
            'Só mede derivadas locais com controles congelados. Necessário posterior despacho conjunto '
            'e verificação da rede com controles automáticos.')
    }
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Conclusão do diagnóstico:',output)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--resource',choices=('bess','v2g'),required=True)
    ap.add_argument('--repo',type=Path,default=None)
    ap.add_argument('--results',type=Path,default=Path('results/s0_s1_opendss_24h'))
    ap.add_argument('--hours',nargs='+',type=int,default=[17,18,19])
    ap.add_argument('--delta-kw',type=float,default=200.0)
    ap.add_argument('--output',type=Path,default=Path('results/calibracao_recurso_v05'))
    args=ap.parse_args()
    run(args)

if __name__ == '__main__':main()
