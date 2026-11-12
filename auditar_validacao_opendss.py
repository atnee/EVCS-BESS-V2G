"""Auditoria dos resultados OpenDSS frente à topologia física do IEEE 8500 reduzido.

Executar na raiz do repositório EVCS-BESS-V2G:
    python auditar_validacao_opendss.py

O programa NÃO recalcula o fluxo elétrico, não altera os CSV originais e NÃO
constitui, sozinho, uma validação quantitativa contra o pandapower.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

PHASE_NUMBER = {'A': 1, 'B': 2, 'C': 3}


def read_csv(path: Path) -> list[dict]:
    with path.open('r', encoding='utf-8-sig', newline='') as fp:
        return list(csv.DictReader(fp))


def write_csv(path: Path, rows: list[dict], columns: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as fp:
        w = csv.DictWriter(fp, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def audit_case(case: str, folder: Path, physical_buses: dict, expected_lines: set, output: Path) -> dict:
    v = read_csv(folder / 'bus_phase_voltages.csv')
    i = read_csv(folder / 'line_currents.csv')
    seen_physical, valid, anomalous = set(), [], []
    for row in v:
        bus = row['bus'].lower()
        try:
            ph = int(row['phase'])
        except (ValueError, TypeError):
            continue
        if bus not in physical_buses or ph not in (1, 2, 3):
            continue
        r = {'node': row['node'], 'bus': bus, 'phase': ph,
             'voltage_pu': float(row['voltage_pu'])}
        if ph in physical_buses[bus]:
            seen_physical.add((bus, ph))
            valid.append(r)
        else:
            r['expected_phases'] = ','.join(str(x) for x in sorted(physical_buses[bus]))
            anomalous.append(r)
    expected_nodes = {(bus, ph) for bus, phases in physical_buses.items() for ph in phases}
    missing = sorted(expected_nodes - seen_physical)
    valid.sort(key=lambda r: r['voltage_pu'])

    # Restringe os dados de correntes às 2.483 linhas de MT do modelo reduzido.
    lines = defaultdict(list)
    for row in i:
        name = row['line'].lower()
        if name in expected_lines:
            lines[name].append(row)
    linemax = []
    for name, rows in lines.items():
        rated = {float(r['ampacity_a']) for r in rows}
        worst = max(rows, key=lambda r: float(r['loading_pct']))
        linemax.append({'line': name,
                        'maximum_current_a': float(worst['current_a']),
                        'ampacity_a': float(worst['ampacity_a']),
                        'maximum_loading_pct': float(worst['loading_pct']),
                        'conductor_order': int(worst['conductor_order']),
                        'different_ampacities_per_phase': len(rated) != 1})
    linemax.sort(key=lambda r: r['maximum_loading_pct'], reverse=True)

    write_csv(output / f'{case}_tensoes_fases_fisicas.csv', valid,
              ['node', 'bus', 'phase', 'voltage_pu'])
    write_csv(output / f'{case}_fases_incompativeis.csv', anomalous,
              ['node', 'bus', 'phase', 'voltage_pu', 'expected_phases'])
    write_csv(output / f'{case}_carregamento_linhas_mt.csv', linemax,
              ['line', 'maximum_current_a', 'ampacity_a', 'maximum_loading_pct',
               'conductor_order', 'different_ampacities_per_phase'])

    out = {
        'case': case,
        'physical_phase_nodes_expected_from_json': len(expected_nodes),
        'physical_phase_nodes_found': len(valid),
        'unexpected_phase_nodes': len(anomalous),
        'missing_expected_phase_nodes': [{'bus': b, 'phase': ph} for b, ph in missing],
        'minimum_voltage_pu_physical': valid[0]['voltage_pu'] if valid else None,
        'minimum_voltage_bus': valid[0]['node'] if valid else None,
        'maximum_voltage_pu_physical': max((r['voltage_pu'] for r in valid), default=None),
        'physical_phase_nodes_below_0_95': sum(r['voltage_pu'] < 0.95 for r in valid),
        'physical_phase_nodes_above_1_05': sum(r['voltage_pu'] > 1.05 for r in valid),
        'medium_voltage_lines_expected': len(expected_lines),
        'medium_voltage_lines_found': len(lines),
        'medium_voltage_lines_over_100_percent': sum(r['maximum_loading_pct'] > 100 for r in linemax),
        'worst_medium_voltage_lines': linemax[:12],
        'anomalous_voltage_nodes': anomalous[:30],
        'methodology_note': 'Filtragem por fases do JSON e linhas MT; snapshot, nao simulacao S0-S4.',
    }
    (output / f'{case}_auditoria.json').write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--resultados', type=Path, default=Path('results/opendss_validation'))
    parser.add_argument('--saida', type=Path, default=None)
    args = parser.parse_args()
    root = args.repo.resolve()
    base = args.resultados if args.resultados.is_absolute() else root / args.resultados
    output = args.saida or (base / 'auditoria')
    output.mkdir(parents=True, exist_ok=True)
    data = json.loads((root / 'src/network/data/ieee8500.json').read_text(encoding='utf-8'))
    phases = {str(b['id']).lower(): {PHASE_NUMBER[p] for p in b['phases']}
              for b in data['buses']}
    lines = {str(line['id']).lower() for line in data['lines']}
    reports = {}
    for case in ('original', 'ajustado'):
        report = audit_case(case, base / case, phases, lines, output)
        reports[case] = report
        print(f"{case}: Vmin física={report['minimum_voltage_pu_physical']:.6f} pu; "
              f"Vmax física={report['maximum_voltage_pu_physical']:.6f} pu; "
              f"linhas MT >100%={report['medium_voltage_lines_over_100_percent']}; "
              f"fases incompatíveis={report['unexpected_phase_nodes']}")
        if report['missing_expected_phase_nodes']:
            print('  Nós físicos ausentes:', report['missing_expected_phase_nodes'][:10])
    print('Relatório salvo em:', output)


if __name__ == '__main__':
    main()
