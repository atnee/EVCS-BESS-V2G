#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Teste NAO DESTRUTIVO de uma falha localizada na representacao de fases.

Rodar na raiz do projeto EVCS-BESS-V2G:
    python testar_correcao_cap_trifasico_v8.py

Compara S0 as 18h, carga nominal, taps fixos de referencia e capacitores ligados:
  A) backend atual (CAP_1A/B/C, CAP_2A/B/C, CAP_3A/B/C);
  B) backend com cada tripla CAP_* representada por UM elemento trifasico.

Isto e um DIAGNOSTICO de conexoes paralelas. NAO corrige as demais linhas
monofasicas, nao substitui o OpenDSS e nao altera arquivos do repositorio.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path


def validate_parallel_triplets(net):
    triplets = {}
    for bank in (1, 2, 3):
        ids = {}
        for p in 'abc':
            name = f'cap_{bank}{p}'
            found = net.line.index[net.line.name.astype(str).str.lower() == name].tolist()
            if len(found) != 1:
                raise ValueError(f'Esperado um elemento {name}; encontrados {len(found)}')
            ids[p] = int(found[0])
        rows = [net.line.loc[v] for v in ids.values()]
        b = {tuple(sorted((int(r.from_bus), int(r.to_bus)))) for r in rows}
        if len(b) != 1:
            raise ValueError(f'CAP_{bank}: barras terminais nao coincidem')
        for key in ('length_km', 'r_ohm_per_km', 'x_ohm_per_km',
                    'r0_ohm_per_km', 'x0_ohm_per_km', 'c_nf_per_km', 'c0_nf_per_km'):
            vals = [float(r[key]) for r in rows]
            if not all(math.isclose(vals[0], x, rel_tol=1e-6, abs_tol=1e-12) for x in vals[1:]):
                raise ValueError(f'CAP_{bank}: {key} nao equivalente {vals}; interrompido')
        for p, idx in ids.items():
            tag = str(net.line.at[idx, 'phases']).upper()
            if tag != p.upper():
                raise ValueError(f'CAP_{bank}{p}: fase fisica inesperada {tag}')
        triplets[bank] = ids
    return triplets


def merge_cap_triplets(net):
    groups = validate_parallel_triplets(net)
    for bank, ids in groups.items():
        keep = ids['a']
        net.line.at[keep, 'name'] = f'cap_{bank}_abc_diagnostico'
        net.line.at[keep, 'phases'] = 'ABC'
        net.line.drop([ids['b'], ids['c']], inplace=True)
    return groups


def run_case(reduced: bool, loader, solver):
    load_feeder, create_pandapower_network = loader
    POWER_COLUMNS, solve_snapshot = solver
    data = load_feeder('ieee8500')
    net = create_pandapower_network(data)
    if reduced:
        merge_cap_triplets(net)
    else:
        validate_parallel_triplets(net)
    net['regulator_control'] = []  # taps armazenados no JSON da pesquisa
    nominal = net.asymmetric_load[POWER_COLUMNS].copy()
    iterations = solve_snapshot(net, nominal, warm=False)
    if not net.converged:
        raise RuntimeError('Falha de convergencia')
    # Excluir fases nao fisicas ao sumarizar a tensao (inclusive no baseline).
    phase_by_bus = {b['id'].lower(): set(b['phases'].upper()) for b in data.equipment['feeder_data'][0]['buses']}
    voltages = {}
    for idx, bus in net.bus.iterrows():
        name = str(bus['name']).lower()
        physical = phase_by_bus.get(name)
        if physical is None:
            continue
        for phase in physical:
            voltages[(name, phase)] = float(net.res_bus_3ph.at[idx, f'vm_{phase.lower()}_pu'])
    # Correntes fisicas por nome de linha.
    lines = {}
    overloaded = []
    for idx, line in net.line.iterrows():
        name = str(line['name']).lower()
        phys = set(str(line['phases']).upper())
        currents = {p: float(net.res_line_3ph.at[idx, f'i_{p.lower()}_ka']) * 1000
                    for p in phys}
        loading = {p:float(net.res_line_3ph.at[idx, f'loading_{p.lower()}_percent'])
                   for p in phys}
        lines[name] = {'currents_a':currents, 'loading_pct':loading}
        if max(loading.values()) > 100:
            overloaded.append(name)
    ext = net.res_ext_grid_3ph
    source_kw = float(ext[[f'p_{p}_mw' for p in 'abc']].sum().sum()*1000)
    losses_kw = float(net.res_line_3ph[[f'pl_{p}_mw' for p in 'abc']].sum().sum()*1000)
    return {
        'summary': {'iterations':int(iterations), 'physical_vmin_pu':min(voltages.values()),
                    'physical_vmax_pu':max(voltages.values()),
                    'source_p_kw':source_kw, 'line_losses_kw':losses_kw,
                    'overloaded_line_count':len(overloaded), 'overloaded_lines':overloaded,
                    'number_of_line_elements':len(net.line)},
        'voltages':voltages, 'lines':lines,
    }


def cap_current_rows(case, tag):
    rows = []
    for bank in (1,2,3):
        for phase in 'ABC':
            if tag == 'original':
                line_name = f'cap_{bank}{phase.lower()}'
            else:
                line_name = f'cap_{bank}_abc_diagnostico'
            item = case['lines'][line_name]
            rows.append({'caso':tag, 'bank':bank, 'phase':phase, 'line':line_name,
                         'current_a':item['currents_a'][phase],
                         'loading_pct':item['loading_pct'][phase]})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--saida', type=Path, default=Path('results/diagnostico_v8_cap_tripletas'))
    args = parser.parse_args()
    repo = args.repo.resolve()
    if not (repo/'src'/'network'/'feeder_loader.py').is_file():
        raise SystemExit('Coloque este script na raiz do EVCS-BESS-V2G ou indique --repo.')
    sys.path.insert(0, str(repo/'src'))
    try:
        from network.feeder_loader import load_feeder, create_pandapower_network
        from network.pandapower_solver import POWER_COLUMNS, solve_snapshot
    except ImportError as exc:
        raise SystemExit(f'Falha de importacao: {exc}')
    loader = (load_feeder,create_pandapower_network)
    solver = (POWER_COLUMNS,solve_snapshot)
    print('S0 18h, taps fixos de referencia, capacitores ligados')
    print('Executando backend original...')
    a=run_case(False,loader,solver)
    print('Executando teste de tripletas CAP_* agrupadas...')
    b=run_case(True,loader,solver)
    vcommon=sorted(set(a['voltages']).intersection(b['voltages']))
    absd=[abs(a['voltages'][k]-b['voltages'][k]) for k in vcommon]
    vmax_idx=max(range(len(vcommon)),key=lambda i:absd[i])
    rows=[]
    for c,tag in ((a,'original'),(b,'cap_agrupadas')):
        rows.extend(cap_current_rows(c,tag))
    import csv
    out=args.saida if args.saida.is_absolute() else repo/args.saida
    out.mkdir(exist_ok=True,parents=True)
    with (out/'correntes_cap_comparacao.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['caso','bank','phase','line','current_a','loading_pct'])
        w.writeheader();w.writerows(rows)
    deviations = [dict(bus=k[0],phase=k[1],original=a['voltages'][k],cap_agrupadas=b['voltages'][k],
                       diff_pu=b['voltages'][k]-a['voltages'][k]) for k in vcommon]
    deviations.sort(key=lambda x:abs(x['diff_pu']),reverse=True)
    with (out/'maiores_diferencas_tensao.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=['bus','phase','original','cap_agrupadas','diff_pu'])
        w.writeheader();w.writerows(deviations[:500])
    report={'gerado_utc':datetime.now(timezone.utc).isoformat(),
            'experimento':'diagnostico limitado: agrega somente as tres tripletas CAP_* em elementos trifasicos; nao corrige outras linhas monofasicas',
            'original':a['summary'], 'cap_agrupadas':b['summary'],
            'comparacao':{'paired_physical_voltages':len(vcommon),
                          'voltage_change_mae_pu':sum(absd)/len(absd),
                          'voltage_change_max_pu':max(absd),
                          'voltage_change_max_bus':vcommon[vmax_idx][0],
                          'voltage_change_max_phase':vcommon[vmax_idx][1],
                          'delta_source_kw':b['summary']['source_p_kw']-a['summary']['source_p_kw'],
                          'delta_line_losses_kw':b['summary']['line_losses_kw']-a['summary']['line_losses_kw']}}
    (out/'resumo.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Original Vmin=',round(a['summary']['physical_vmin_pu'],6),'CAP agrupadas Vmin=',round(b['summary']['physical_vmin_pu'],6))
    print('Mudanca MAE em tensao=',round(report['comparacao']['voltage_change_mae_pu'],8),'p.u.')
    print('Sobrecargas:',a['summary']['overloaded_line_count'],'->',b['summary']['overloaded_line_count'])
    print('Relatorio:',out)
    print('ATENCAO: este teste nao valida fisicamente o IEEE 8500 e nao deve atualizar os resultados do paper.')

if __name__=='__main__':
    main()
