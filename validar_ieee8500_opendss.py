"""Execução inicial do IEEE 8500 no OpenDSSDirect.py (DSS-Extensions).

Objetivo: gerar um retrato elétrico independente do feeder para a auditoria
pandapower vs OpenDSS. NÃO reproduz sozinho a simulação temporal S0-S4.

Uso, a partir da raiz do repositório EVCS-BESS-V2G:
    python -m pip install opendssdirect.py
    python validar_ieee8500_opendss.py --caso original
    python validar_ieee8500_opendss.py --caso ajustado

Pode também passar --repo C:/caminho/EVCS-BESS-V2G e --saida C:/caminho/saida.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


def save_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def apply_study_adjustments(dss, dataset: dict) -> dict:
    """Ajusta somente fonte, setpoints e ampacidades, sem alterar a rede secundária.

    ATENÇÃO: o circuito OpenDSS continua diferente da redução em pandapower.
    """
    dss.Text.Command("Edit Vsource.Source pu=1.0")
    # OpenDSS usa vreg em volts, com base PT nominal de 120 V.
    # 1.03 pu -> 123.6 V; a banda fica em 2 V como no modelo principal.
    dss.Text.Command("BatchEdit RegControl..* vreg=123.6 band=2")
    assigned, not_found = 0, []
    for line in dataset["lines"]:
        code = dataset["linecodes"][line["linecode"]]
        ampacity = float(code["ampacity_a"])
        name = line["id"]
        # Algumas linhas podem ter sido reduzidas na representação pandapower;
        # só editar as que existem no circuito OpenDSS.
        if dss.Circuit.SetActiveElement("Line." + name) <= 0:
            not_found.append(name)
            continue
        dss.Text.Command(f"Edit Line.{name} normamps={ampacity:.9g} emergamps={ampacity:.9g}")
        assigned += 1
    if not_found:
        print(f"AVISO: {len(not_found)} linhas da redução não encontradas no OpenDSS; exemplos: {not_found[:6]}")
    return {"source_pu": 1.0, "regcontrol_vreg_v": 123.6,
            "regcontrol_band_v": 2.0, "lines_with_assumed_ampacity": assigned,
            "line_ids_not_found": len(not_found)}


def voltage_rows(dss, mv_buses: set[str]) -> list[dict]:
    names = dss.Circuit.AllNodeNames()
    voltages = dss.Circuit.AllBusMagPu()
    if len(names) != len(voltages):
        raise RuntimeError(f"Nomes e tensões por nó incompatíveis: {len(names)} vs {len(voltages)}")
    rows = []
    for name, voltage in zip(names, voltages):
        # Os identificadores de nós terminam com '.1', '.2', '.3' etc.
        bus, _, node = name.rpartition(".")
        if not bus:
            bus, node = name, ""
        rows.append({"node": name, "bus": bus, "phase": node,
                     "voltage_pu": float(voltage),
                     "in_mv_reduction": bus.lower() in mv_buses})
    return rows


def line_rows(dss) -> list[dict]:
    rows = []
    if dss.Lines.First() == 0:
        return rows
    while True:
        name = dss.Lines.Name()
        amps_limit = float(dss.Lines.NormAmps())
        dss.Circuit.SetActiveElement("Line." + name)
        num_phases = int(dss.CktElement.NumPhases())
        num_conductors = int(dss.CktElement.NumConductors())
        mag_angle = list(dss.CktElement.CurrentsMagAng())
        # Apenas terminal 1; cada condutor contribui (magnitude, angle).
        # Retém as fases físicas, ignorando condutores auxiliares/neutro.
        magnitudes = [float(mag_angle[2 * k]) for k in range(min(num_phases, num_conductors))]
        for k, amps in enumerate(magnitudes, start=1):
            rows.append({"line": name, "conductor_order": k, "current_a": amps,
                         "ampacity_a": amps_limit,
                         "loading_pct": 100 * amps / amps_limit if amps_limit > 0 else None})
        if dss.Lines.Next() <= 0:
            break
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=Path.cwd(),
                   help="Diretório raiz do repositório EVCS-BESS-V2G")
    p.add_argument("--saida", type=Path, default=Path("results/opendss_validation"))
    p.add_argument("--caso", choices=["original", "ajustado"], default="original")
    args = p.parse_args()
    repo = args.repo.expanduser().resolve()
    out = (args.saida if args.saida.is_absolute() else repo / args.saida).resolve() / args.caso
    master = repo / "data/ieee8500/source/Master.dss"
    normalized = repo / "src/network/data/ieee8500.json"
    if not master.is_file() or not normalized.is_file():
        print("ERRO: Master.dss e/ou ieee8500.json não encontrados.", file=sys.stderr)
        print("Execute na raiz do repositório ou informe --repo <caminho>.", file=sys.stderr)
        return 2

    try:
        from opendssdirect import dss
    except ImportError:
        print("ERRO: instale a dependência com: python -m pip install opendssdirect.py", file=sys.stderr)
        return 2

    dataset = json.loads(normalized.read_text(encoding="utf-8"))
    mv_buses = {str(b["id"]).lower() for b in dataset["buses"]}
    print("Motor OpenDSS:", dss.Basic.Version().splitlines()[0])
    print("Compilando:", master)
    dss.Text.Command(f'Compile "{master.as_posix()}"')
    mods = {}
    if args.caso == "ajustado":
        mods = apply_study_adjustments(dss, dataset)
    # Solve no modo snapshot / controles estáticos. Não equivale aos taps ganged
    # sem delay do artigo nem ao fluxo QSTS ao longo das 24 horas.
    dss.Text.Command("Set mode=snapshot")
    dss.Text.Command("Set controlmode=static")
    dss.Text.Command("Set maxiterations=100")
    dss.Solution.Solve()
    if not dss.Solution.Converged():
        print("ERRO: a solução OpenDSS não convergiu.", file=sys.stderr)
        return 1

    volts = voltage_rows(dss, mv_buses)
    lines = line_rows(dss)
    mv = [r for r in volts if r["in_mv_reduction"] and r["phase"] in ("1", "2", "3")]
    line_overload = {r["line"].lower() for r in lines
                     if r["loading_pct"] is not None and r["loading_pct"] > 100}
    losses_w, losses_var = dss.Circuit.Losses()
    supply_kw, supply_kvar = dss.Circuit.TotalPower()
    info = {
        "case": args.caso,
        "engine": dss.Basic.Version().splitlines()[0],
        "converged": bool(dss.Solution.Converged()),
        "buses_full_dss": int(dss.Circuit.NumBuses()),
        "nodes_total": len(volts),
        "nodes_matching_mv_reduction": len(mv),
        "minimum_voltage_pu_mv": min((r["voltage_pu"] for r in mv), default=None),
        "maximum_voltage_pu_mv": max((r["voltage_pu"] for r in mv), default=None),
        "minimum_voltage_pu_all": min((r["voltage_pu"] for r in volts), default=None),
        "maximum_voltage_pu_all": max((r["voltage_pu"] for r in volts), default=None),
        "lines_above_100_percent": len(line_overload),
        "active_losses_kw": losses_w / 1000,
        "reactive_losses_kvar": losses_var / 1000,
        "source_total_power_kw_signed_opendss": float(supply_kw),
        "source_total_power_kvar_signed_opendss": float(supply_kvar),
        "adjustments": mods,
        "notes": [
            "É um snapshot estático da rede OpenDSS, não o experimento temporal S0-S4.",
            "A versão 'ajustado' iguala fonte, setpoints e ratings assumidos, mas mantém secundários, controladores por fase e demais diferenças estruturais.",
            "Sobrecargas calculadas com a ampacidade efetiva de cada elemento OpenDSS; compatibilidade das definições deve ser verificada antes da comparação direta.",
        ],
    }
    out.mkdir(parents=True, exist_ok=True)
    save_csv(out / "bus_phase_voltages.csv", volts, ["node", "bus", "phase", "voltage_pu", "in_mv_reduction"])
    save_csv(out / "line_currents.csv", lines, ["line", "conductor_order", "current_a", "ampacity_a", "loading_pct"])
    (out / "summary.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Convergiu: SIM")
    print("Barras OpenDSS:", info["buses_full_dss"])
    print("Tensão mínima MT: ", info["minimum_voltage_pu_mv"])
    print("Tensão máxima MT: ", info["maximum_voltage_pu_mv"])
    print("Linhas >100%:     ", info["lines_above_100_percent"])
    print("Perdas ativas kW:", round(info["active_losses_kw"], 4))
    print("Arquivos salvos em:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
