#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auditoria não destrutiva da representação de fases físicas no IEEE 8500.

Executar na raiz de EVCS-BESS-V2G:
    python auditar_fases_fantasmas_ieee8500.py

Objetivo: verificar se linhas etiquetadas como mono/bifásicas recebem correntes
calculadas em fases fisicamente ausentes no backend pandapower.runpp_3ph.
O teste reproduz o S0 às 18h (multiplicador de carga 1.0), com taps de referência
fixos e capacitores ligados; NÃO substitui uma validação OpenDSS completa.
Nenhum arquivo do projeto é alterado; somente cria resultados de auditoria.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--saida", type=Path, default=Path("results/auditoria_fases_fantasmas"))
    parser.add_argument("--limiar-a", type=float, default=0.1, help="Corrente mínima para sinalizar fase ausente (A)")
    args = parser.parse_args()
    repo = args.repo.resolve()
    src = repo / "src"
    if not src.is_dir():
        raise SystemExit(f"Pasta src não encontrada: {src}. Coloque o script na raiz do repositório.")
    if args.limiar_a <= 0:
        raise SystemExit("--limiar-a deve ser positivo")

    sys.path.insert(0, str(src))
    try:
        from network.feeder_loader import load_feeder, create_pandapower_network
        from network.pandapower_solver import POWER_COLUMNS, solve_snapshot
    except ImportError as exc:
        raise SystemExit("Falha ao importar o projeto. Instale requirements-tested.txt antes de executar. "+str(exc))

    network = load_feeder("ieee8500")
    net = create_pandapower_network(network)
    net["regulator_control"] = []  # taps já armazenados no JSON, sem resposta automática
    ref_taps = {
        str(r["name"]).lower():float(net.trafo.at[idx,"tap_pos"])
        for idx,r in net.trafo.iterrows() if str(r["name"]).lower() in ("feeder_reg","vreg2","vreg3","vreg4")
    }

    print("Auditoria S0, 18h: carga nativa 1.000, capacitores ligados, taps fixos")
    print("Taps de referência:",ref_taps)
    nominal = net.asymmetric_load[POWER_COLUMNS].copy()
    iters = solve_snapshot(net,nominal,warm=False)
    if not net.converged:
        raise RuntimeError("Fluxo de potência não convergiu; nenhum indicador será publicado.")
    print("Fluxo convergido em", iters, "iterações externas")

    rows = []
    counts = Counter()
    phantom_by_line = defaultdict(list)
    group_pairs = defaultdict(list)
    line_names = {}
    for idx, l in net.line.iterrows():
        name = str(l["name"]).lower()
        physical = set(str(l["phases"]).upper())
        if not physical or not physical <= set("ABC"):
            raise ValueError(f"Fases inválidas em {name}: {physical}")
        counts[len(physical)] += 1
        a,b = int(l["from_bus"]), int(l["to_bus"])
        group_pairs[tuple(sorted((a,b)))].append({"name":name,"physical":"".join(sorted(physical))})
        line_names[name]=(a,b)
        res = net.res_line_3ph.loc[idx]
        for phase in "ABC":
            i_ka = float(res[f"i_{phase.lower()}_ka"])
            if not math.isfinite(i_ka):
                raise RuntimeError(f"Corrente não finita em {name}.{phase}")
            current = abs(i_ka)*1000.0
            is_physical = phase in physical
            flag = not is_physical and current >= args.limiar_a
            row = {
                "line":name,
                "physical_phases":"".join(sorted(physical)),
                "computed_phase":phase,
                "physical":is_physical,
                "current_a":round(current,6),
                "phantom_current_flag":flag,
                "from_bus":str(net.bus.at[a,"name"]),
                "to_bus":str(net.bus.at[b,"name"]),
            }
            rows.append(row)
            if flag:
                phantom_by_line[name].append(row)

    affected_lines = sorted(phantom_by_line,
        key=lambda n: max(v["current_a"] for v in phantom_by_line[n]),reverse=True)
    count_ghost_phase = sum(len(vals) for vals in phantom_by_line.values())
    ghosts = sorted([r for r in rows if r["phantom_current_flag"]],
                    key=lambda r:r["current_a"],reverse=True)
    repeated_pairs = []
    for buspair, items in group_pairs.items():
        if len(items)>=2 and any(len(x["physical"])<3 for x in items):
            repeated_pairs.append({
                "bus1":str(net.bus.at[buspair[0],"name"]),
                "bus2":str(net.bus.at[buspair[1],"name"]),
                "number_of_line_elements":len(items),
                "elements":"; ".join(x["name"]+"["+x["physical"]+"]" for x in items),
            })
    repeated_pairs.sort(key=lambda z:-z["number_of_line_elements"])

    pp_cap_example = []
    for nm in ("cap_1a","cap_1b","cap_1c","cap_2a","cap_2b","cap_2c","cap_3a","cap_3b","cap_3c"):
        pp_cap_example.extend(r for r in rows if r["line"]==nm)
    pp_cap_example.sort(key=lambda r:(r["line"],r["computed_phase"]))

    out = args.saida if args.saida.is_absolute() else repo/args.saida
    out.mkdir(parents=True,exist_ok=True)
    fields = ["line","physical_phases","computed_phase","physical","current_a","phantom_current_flag","from_bus","to_bus"]
    write_csv(out/"todas_correntes_por_fase.csv", rows, fields)
    write_csv(out/"correntes_fantasmas.csv", ghosts, fields)
    write_csv(out/"exemplo_cap_1_2_3.csv",pp_cap_example,fields)
    write_csv(out/"linhas_paralelas_mesmas_barras.csv",repeated_pairs,
              ["bus1","bus2","number_of_line_elements","elements"])

    summary = {
        "gerado_em_utc":datetime.now(timezone.utc).isoformat(),
        "experimento":"S0 18h, carga 1.0, taps de referência fixos, capacitores ligados",
        "regulator_taps":ref_taps,
        "iterations":int(iters),
        "total_line_elements":len(net.line),
        "single_phase_lines":int(counts[1]),
        "two_phase_lines":int(counts[2]),
        "three_phase_lines":int(counts[3]),
        "phantom_current_threshold_a":args.limiar_a,
        "single_or_two_phase_lines_with_nonphysical_current_above_threshold":sum(
            len(set(x['physical_phases']))<3 for name in affected_lines
            for x in phantom_by_line[name][:1]
        ),
        "number_of_nonphysical_phase_current_records_above_threshold":count_ghost_phase,
        "max_nonphysical_current_a":max((r["current_a"] for r in ghosts),default=0.0),
        "top10_nonphysical_phase_currents":ghosts[:10],
        "parallel_line_pairs_with_phase_specific_elements":repeated_pairs[:20],
        "conclusion_if_flagged":"Currentes em fases eletricamente ausentes indicam que a coluna phases nao abre condutores no calculo. Confirmar em net.res_line_3ph e corrigir backend antes de validar o paper.",
        "note":"Correntes em fases ausentes sao prova de um problema de representacao das linhas, nao uma medicao de campo. Não alterar resultados do artigo sem recalculo consistente.",
    }
    (out/"resumo.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print("Linhas mono/bi/tri:",counts[1],counts[2],counts[3])
    print("Linhas com corrente em fase fisicamente ausente:",summary["single_or_two_phase_lines_with_nonphysical_current_above_threshold"])
    print("Registros de fases ausentes com corrente:",count_ghost_phase)
    print("Maior corrente em fase fisicamente ausente:",f'{summary["max_nonphysical_current_a"]:.3f}',"A")
    print("Grupos de linhas fase-especificas entre as mesmas barras:",len(repeated_pairs))
    print("Auditoria salva em:",out)
    if count_ghost_phase:
        print("ATENÇÃO: o solver calculou correntes em fases que o dataset declara ausentes.")

if __name__=="__main__":
    main()
