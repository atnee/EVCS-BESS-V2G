#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Teste S0 pareado: pandapower vs OpenDSS no IEEE 8500 do EVCS-BESS-V2G.

Versao 6: teste controlado das matrizes 3-fasicas de linha.
Mantem as cargas em potencia constante e os demais parametros da V5.
Substitui SOMENTE os 614 trechos trifasicos por aproximacoes de sequencia
equivalentes as usadas no pandapower, preservando os demais elementos. Sem EVCS/BESS/V2G.

Compara:
  (1) pandapower: aproximacao MT usada no artigo;
  (2) OpenDSS completo: rede secundaria original, fonte ajustada;
  (3) OpenDSS MT equivalente: desliga apenas as cargas secundarias; substitui
      por cargas primarias agregadas do JSON do pandapower, preservando
      transformadores e ramais secundarios sem consumo para evitar ilhas.
      Isso eh uma equivalencia aproximada, nao eliminacao exata dos secundarios.

Nao altera nenhum arquivo do repositorio nem sobrescreve simulacoes anteriores.
NÃO reproduz o resultado S0 com reguladores automáticos da Tabela 6.

Uso:
  python -m pip install -r requirements-tested.txt
  python -m pip install opendssdirect.py
  python validar_s0_pareado_v6.py --horas 18 --capacitores ligados --modelo-linhas sequencia_3f
  python validar_s0_pareado_v6.py --horas 18 --capacitores ligados --modelo-linhas originais --saida results/s0_pareado_v6_controle
  # Compare os dois arquivos resumo_pareado.csv com a V5 (mesmo carregamento, controles, capacitores).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

PHASE_NUMBER = {"A": 1, "B": 2, "C": 3}
NUMBER_PHASE = {v: k for k, v in PHASE_NUMBER.items()}
BASE_KV_LL = 12.47
TAP_BANK_PREFIXES = ("feeder_reg", "vreg2_", "vreg3_", "vreg4_")


def save_csv(filepath: Path, rows: list[dict], cols: list[str]):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def save_json(filepath: Path, obj):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def physical_keys(data: dict) -> set[tuple[str, str]]:
    return {(str(b["id"]).lower(), ph) for b in data["buses"] for ph in b["phases"]}


def pq_metrics(voltages: dict, lines: dict):
    v_values = list(voltages.values())
    currents = list(lines.values())
    return {
        "fases_de_tensao": len(v_values),
        "vmin_pu": min(v_values) if v_values else None,
        "vmax_pu": max(v_values) if v_values else None,
        "fases_v_abaixo_095": sum(x < 0.95 for x in v_values),
        "fases_v_acima_105": sum(x > 1.05 for x in v_values),
        "fases_de_linha": len(currents),
        "carregamento_max_pct": max((x["loading_pct"] for x in currents), default=None),
        "linhas_acima_100": len({name for (name, ph), r in lines.items() if r["loading_pct"] > 100}),
    }


def taps_de_referencia(data: dict) -> dict[str, float]:
    """Tap armazenado por banco no arquivo normalizado do projeto."""
    return {str(r["id"]).lower(): float(sum(r["taps"]) / len(r["taps"]))
            for r in data["regulators"]}


def run_pandapower(repo: Path, data: dict, load_mult: float, taps_mode: str = "referencia", capacitores: bool = False):
    sys.path.insert(0, str(repo / "src"))
    try:
        import pandapower as pp
        from network.feeder_loader import load_feeder, create_pandapower_network
        from network.pandapower_solver import solve_snapshot, POWER_COLUMNS
    except ImportError as exc:
        raise RuntimeError("Nao foi possivel importar o projeto/pandapower. "
                           "Execute no ambiente Python do projeto e instale requirements-tested.txt") from exc

    network = load_feeder("ieee8500")
    net = create_pandapower_network(network)
    # Desliga controlador automatico; taps sao fixos e definidos no mesmo
    # arquivo de referencia que fornece as cargas primarias.
    net["regulator_control"] = []
    bank_taps = taps_de_referencia(data) if taps_mode == "referencia" else {}
    for idx, row in net.trafo.iterrows():
        name = str(row["name"]).lower()
        if name in {"feeder_reg", "vreg2", "vreg3", "vreg4"}:
            net.trafo.at[idx, "tap_pos"] = bank_taps.get(name, 0.0)

    # As cargas nativas precedem os bancos capacitores no carregador do repositorio.
    nominal = net.asymmetric_load[POWER_COLUMNS].copy()
    nloads = len(data["loads"])
    load_indices = nominal.index[:nloads]
    if len(load_indices) != nloads:
        raise RuntimeError("Numero de cargas nativas inesperado")
    nominal.loc[load_indices] *= load_mult
    cap_indices = nominal.index[nloads:]
    if not capacitores:
        nominal.loc[cap_indices, POWER_COLUMNS] = 0.0
    iterations = solve_snapshot(net, nominal, warm=False)
    if not bool(net.converged):
        raise RuntimeError("pandapower nao convergiu")

    expected = physical_keys(data)
    volts = {}
    for _, row in net.bus.iterrows():
        bus = str(row["name"]).lower()
        idx = row.name
        for ph in "ABC":
            if (bus, ph) in expected:
                val = float(net.res_bus_3ph.at[idx, f"vm_{ph.lower()}_pu"])
                if math.isfinite(val):
                    volts[(bus, ph)] = val

    rating = {str(l["id"]).lower(): float(data["linecodes"][l["linecode"]]["ampacity_a"])
              for l in data["lines"]}
    physical = {str(l["id"]).lower(): set(l["phases"]) for l in data["lines"]}
    currents = {}
    for idx, row in net.line.iterrows():
        name = str(row["name"]).lower()
        if name not in rating:
            continue
        for ph in physical[name]:
            val = float(net.res_line_3ph.at[idx, f"i_{ph.lower()}_ka"]) * 1000.0
            if math.isfinite(val):
                currents[(name, ph)] = {
                    "current_a": val, "ampacity_a": rating[name],
                    "loading_pct": val * 100 / rating[name],
                }

    cap_power = {}
    for idx in cap_indices:
        r = net.asymmetric_load.loc[idx]
        cap_power[str(r["name"]).lower()] = -1000.0 * sum(float(r[f"q_{ph}_mvar"]) for ph in "abc")
    ext = net.res_ext_grid_3ph
    p_kw = float(sum(ext[f"p_{p}_mw"].sum() for p in "abc")) * 1000.0
    q_kvar = float(sum(ext[f"q_{p}_mvar"].sum() for p in "abc")) * 1000.0
    # A soma das perdas de elementos e diagnostico; secundario ausente no pandapower.
    losses_kw = 0.0
    for table in ("res_line_3ph", "res_trafo_3ph"):
        result = net.get(table, None)
        if result is not None:
            cols = [f"pl_{ph}_mw" for ph in "abc" if f"pl_{ph}_mw" in result]
            if cols:
                losses_kw += float(result[cols].sum().sum()) * 1000
    return {
        "name": "pandapower_mt", "voltages": volts, "lines": currents,
        "metrics": {**pq_metrics(volts, currents), "p_subestacao_kw": p_kw,
                    "perdas_kw": losses_kw, "q_subestacao_kvar": q_kvar,
                    "capacit_kvar_por_elemento": cap_power,
                    "capacit_kvar_total": sum(cap_power.values()),
                    "capacitores_ligados": capacitores,
                    "iteracoes_zip": iterations,
                    "tap_status": f"taps {taps_mode} fixos: {bank_taps}", "engine": f"pandapower {pp.__version__}"},
    }


def change_dss_to_mv_equivalent(dss, data: dict, vmin_carga: float, vmax_carga: float):
    """Equivalencia de cargas MT SEM abrir ilhas na rede secundaria.

    No script v1, desabilitar 1177 transformadores de servico e trechos triplex
    poderia criar redes secundarias eletricamente isoladas, prejudicando a
    convergencia da matriz de admitancias do OpenDSS.

    Aqui sao desabilitadas apenas as cargas originais e injetadas as mesmas
    cargas agregadas do JSON na barra/fase MT. Os transformadores e ramais
    secundarios permanecem energizados, porem sem cargas nesses ramos.
    Isto NAO e a remocao matematica completa dos secundários: perdas em vazio
    ou outras contribuicoes residuais podem permanecer. E um teste diagnostico.
    """
    original_loads = list(dss.Loads.AllNames())
    if not original_loads or original_loads == ["NONE"]:
        raise RuntimeError("Nenhuma carga original encontrada antes da substituicao")
    dss.Text.Command("BatchEdit Load..* enabled=no")

    kv_ln = BASE_KV_LL / math.sqrt(3.0)
    for i, l in enumerate(data["loads"]):
        phases = str(l["phases"])
        if len(phases) != 1:
            raise RuntimeError(f"Carga {l['id']} nao monofasica: {phases}")
        phase = PHASE_NUMBER[phases]
        # No teste de equivalencia numerica, usar limites amplos (0.50,1.50)
        # reduz a comutacao do OpenDSS do modelo 1 (P constante) para Z constante.
        # Nao usar limites amplos como proposta de operacao fisica; somente teste.
        cmd = (f"New Load.PP_EQ_{i:04d} phases=1 conn=wye "
               f"bus1={l['bus']}.{phase} kv={kv_ln:.9g} "
               f"kw={float(l['p_kw']):.10g} kvar={float(l['q_kvar']):.10g} "
               f"model={int(l['model'])} vminpu={vmin_carga:.6g} vmaxpu={vmax_carga:.6g}")
        dss.Text.Command(cmd)

    return {"original_secondary_loads_disabled": len(original_loads),
            "primary_equivalent_loads_created": len(data["loads"]),
            "secondary_transformers_kept_connected": True,
            "secondary_lines_kept_connected": True,
            "secondary_equivalent_caveat":
                "Ramal e transformador secundario presentes sem carga: reducao aproximada."}


def solve_dss_with_diagnostics(dss, load_mult: float, mode: str):
    """Tenta fluxo normal e, se necessario, inicializacao gradual + Newton.

    Nao altera dados da rede, limites de tensao ou potencias. Registra metodo.
    Em falha, devolve diagnostico de iteracoes sem inventar resultados.
    """
    details = []

    def attempt(tag, mult):
        dss.Text.Command(f"Set LoadMult={mult:.9g}")
        dss.Solution.Solve()
        ok = bool(dss.Solution.Converged())
        try:
            iters = int(dss.Solution.Iterations())
        except Exception:
            iters = None
        details.append({"etapa": tag, "carga": mult, "convergiu": ok, "iteracoes": iters})
        return ok

    if attempt("normal", load_mult):
        return {"metodo_convergencia": "normal", "historico_solver": details}

    if mode == "mt_equivalente":
        # Inicializar com carregamento pequeno evita comecar com tensoes muito
        # baixas, sem mudar as cargas finais. Newton e apenas metodo numerico.
        dss.Text.Command("Set algorithm=Newton")
        dss.Text.Command("Set maxiterations=200")
        ramp = sorted(set([0.15, 0.30, 0.50, 0.75, load_mult]))
        ramp = [v for v in ramp if v <= load_mult + 1e-9]
        ramp[-1] = load_mult
        for m in ramp:
            if not attempt("newton_rampa", m):
                break
        else:
            return {"metodo_convergencia": "newton_rampa", "historico_solver": details}

        # Um novo teste Newton direto, para diferenciar problema de inicializacao.
        if attempt("newton_direto", load_mult):
            return {"metodo_convergencia": "newton_direto", "historico_solver": details}

    msg = (f"OpenDSS nao convergiu em {mode}, LoadMult={load_mult}. "
           f"Historico: {json.dumps(details, ensure_ascii=False)}")
    raise RuntimeError(msg)


def aproximar_linhas_trifasicas_no_dss(dss, data: dict) -> dict:
    """Diagnostico de *um* fator: replicar R1/X1/R0/X0/C1/C0 do pandapower.

    SÓ linhas MT trifasicas; nao modifica ramais monofasicos, taps, subestacao,
    potencia ou localizacao das cargas. Cria LineCodes novos sem sobrescrever
    os codigos originais. Em caso de falha o script nao deve usar o resultado.
    """
    import numpy as np

    lines = [l for l in data["lines"] if l["phases"] == "ABC"]
    codes = sorted({l["linecode"] for l in lines})
    by_name = {}
    for pos, code_name in enumerate(codes):
        code = data["linecodes"][code_name]
        values = {}
        for label, prop in (("r", "rmatrix"), ("x", "xmatrix"), ("c", "cmatrix")):
            mat = np.asarray(code[prop], dtype=float)
            if mat.shape != (3, 3) or not np.isfinite(mat).all():
                raise ValueError(f"Matriz invalida: {code_name} / {prop}")
            self_z = float(np.trace(mat)/3)
            mutual = float((mat.sum()-np.trace(mat))/6)
            values[label+"1"] = self_z - mutual
            values[label+"0"] = self_z + 2*mutual
        # As matrizes no JSON estao por km; estas sao as mesmas formulas
        # utilizadas em src/network/feeder_loader.py.
        new_name = f"V6SEQ_{pos:03d}"
        cmd = (f"New LineCode.{new_name} nphases=3 units=km "
               f"r1={values['r1']:.12g} x1={values['x1']:.12g} "
               f"r0={values['r0']:.12g} x0={values['x0']:.12g} "
               f"c1={values['c1']:.12g} c0={values['c0']:.12g}")
        dss.Text.Command(cmd)
        if dss.Error.Number():
            raise RuntimeError(f"OpenDSS erro na criacao de {new_name}: {dss.Error.Description()}")
        by_name[code_name] = new_name

    count = 0
    for line in lines:
        name = line["id"]
        cmd = f"Edit Line.{name} linecode={by_name[line['linecode']]}"
        dss.Text.Command(cmd)
        if dss.Error.Number():
            raise RuntimeError(f"OpenDSS erro em linha {name}: {dss.Error.Description()}")
        count += 1
    if count != len(lines):
        raise RuntimeError("Numero de linhas substituidas incorreto")
    return {"alteracao": "linha trifasica: matriz completa -> sequencias medias (pandapower)",
            "linhas_trifasicas_alteradas": count, "linecodes_novos": len(codes),
            "linhas_mono_bifasicas_alteradas": 0,
            "nota": "Teste isolado. Não corresponde ao OpenDSS original nem valida o modelo."}


def run_opendss(repo: Path, data: dict, load_mult: float, mode: str, taps_mode: str = "referencia", capacitores: bool = False, vmin_carga: float = 0.50, vmax_carga: float = 1.50, modelo_linhas: str = "originais"):
    try:
        from opendssdirect import dss
    except ImportError as exc:
        raise RuntimeError("Instale o motor com: python -m pip install opendssdirect.py") from exc
    master = repo / "data" / "ieee8500" / "source" / "Master.dss"
    if not master.is_file():
        raise FileNotFoundError(master)

    # Compilacao do zero para cada modo: evita herdar estados de tap e controle.
    dss.Text.Command(f'Compile "{master.as_posix()}"')
    dss.Text.Command("Set mode=snapshot")
    dss.Text.Command("Set controlmode=off")
    dss.Text.Command("Set maxiterations=100")
    dss.Text.Command("Edit Vsource.Source pu=1.0")
    # Controle automatico desligado; posicoes de tap fixas em ambos os motores.
    # A correspondencia do sinal/sentido dos taps precisa ser confirmada
    # empiricamente no exame das tensoes a montante/jusante.
    dss.Text.Command("BatchEdit RegControl..* enabled=no")
    dss.Text.Command("BatchEdit CapControl..* enabled=no")
    if not capacitores:
        dss.Text.Command("BatchEdit Capacitor..* enabled=no")
    bank_taps = taps_de_referencia(data) if taps_mode == "referencia" else {}
    for name in list(dss.Transformers.AllNames()):
        lname = name.lower()
        bank = "feeder_reg" if lname.startswith("feeder_reg") else next(
            (k for k in ("vreg2", "vreg3", "vreg4") if lname.startswith(k + "_")), None)
        if bank is not None:
            steps = bank_taps.get(bank, 0.0)
            ratio = 1.0 + 0.00625 * steps
            dss.Text.Command(f"Edit Transformer.{name} wdg=2 tap={ratio:.8f}")
    # Assegura que o comando de desligamento foi realmente aplicado.
    cap_states = {}
    for n in list(dss.Capacitors.AllNames()):
        if str(n).upper() == "NONE":
            continue
        if dss.Circuit.SetActiveElement("Capacitor." + str(n)) <= 0:
            raise RuntimeError("Capacitor nao encontrado: " + str(n))
        cap_states[str(n).lower()] = bool(dss.CktElement.Enabled())
    if any(state != capacitores for state in cap_states.values()):
        raise RuntimeError("Estado inesperado dos capacitores OpenDSS: " + str(cap_states))
    extra = {"capacitores_estados_dss":cap_states}
    if mode == "mt_equivalente":
        extra = change_dss_to_mv_equivalent(dss, data, vmin_carga, vmax_carga)
    # Alteracao controlada da matriz de linhas, mantendo todos os outros fatores.
    if modelo_linhas == "sequencia_3f":
        alteracao_linhas = aproximar_linhas_trifasicas_no_dss(dss, data)
    elif modelo_linhas == "originais":
        alteracao_linhas = {"alteracao": "nenhuma", "linhas_trifasicas_alteradas": 0}
    else:
        raise ValueError(f"Modelo de linha invalido: {modelo_linhas}")
    # Mantem as caracteristicas eletricas; registra modo de convergencia.
    solve_meta = solve_dss_with_diagnostics(dss, load_mult, mode)

    expected = physical_keys(data)
    voltages = {}
    anomalous = []
    names = list(dss.Circuit.AllNodeNames())
    pu = list(dss.Circuit.AllBusMagPu())
    if len(names) != len(pu):
        raise RuntimeError("OpenDSS: nomes de nos e tensoes com dimensoes diferentes")
    for node, voltage in zip(names, pu):
        bus, _, ph_txt = str(node).lower().rpartition(".")
        if ph_txt in ("1", "2", "3"):
            key = (bus, NUMBER_PHASE[int(ph_txt)])
            if key in expected and math.isfinite(voltage):
                voltages[key] = float(voltage)
            elif bus in {b for b, p in expected}:
                anomalous.append({"node": node, "voltage_pu": voltage})

    expected_lines = {str(l["id"]).lower(): l for l in data["lines"]}
    currents = {}
    for name in list(dss.Lines.AllNames()):
        line = expected_lines.get(name.lower())
        if line is None:
            continue
        if dss.Circuit.SetActiveElement("Line." + name) <= 0:
            continue
        # OpenDSS identifica a fase no primeiro terminal por NodeOrder; nao
        # confundir condutor 1 de ramal monofasico com fase A.
        ncond = int(dss.CktElement.NumConductors())
        order = list(dss.CktElement.NodeOrder())[:ncond]
        mags = list(dss.CktElement.CurrentsMagAng())
        rating = float(data["linecodes"][line["linecode"]]["ampacity_a"])
        for i, phase in enumerate(order):
            label = NUMBER_PHASE.get(int(phase))
            if label not in line["phases"]:
                continue
            if 2*i >= len(mags):
                continue
            amps = float(mags[2*i])
            if not math.isfinite(amps):
                continue
            currents[(name.lower(), label)] = {
                "current_a": amps, "ampacity_a": rating,
                "loading_pct": amps * 100 / rating,
            }
    # Potencia reativa nos capacitores: potencia terminal negativa indica injecao.
    cap_power = {}
    for cap_name in list(dss.Capacitors.AllNames()):
        if str(cap_name).upper() == 'NONE':
            continue
        if dss.Circuit.SetActiveElement('Capacitor.' + str(cap_name)) <= 0:
            raise RuntimeError('Capacitor OpenDSS nao localizado: ' + str(cap_name))
        # Os capacitores desligados devem registrar aproximadamente zero.
        terminal_powers = list(dss.CktElement.Powers())
        cap_power[str(cap_name).lower()] = -sum(float(q) for q in terminal_powers[1::2])
    losses_w, _ = dss.Circuit.Losses()
    p_source_kw, q_source_kvar = dss.Circuit.TotalPower()
    nominal_load_kw = sum(float(l["p_kw"]) for l in data["loads"]) * load_mult
    effective_load_kw = -float(p_source_kw) - float(losses_w)/1000.0
    # Indicador de balanco. O modelo OpenDSS contem transformadores secundarios
    # mantidos energizados, portanto desvios residuais devem ser investigados.
    load_balance = dict(potencia_carga_nominal_kw=nominal_load_kw,
                        potencia_efetiva_por_balanco_kw=effective_load_kw,
                        diferenca_efetiva_menos_nominal_kw=effective_load_kw-nominal_load_kw,
                        diferenca_pct_nominal=100*(effective_load_kw-nominal_load_kw)/nominal_load_kw if nominal_load_kw else None)

    return {
        "name": "opendss_" + mode, "voltages": voltages, "lines": currents,
        "metrics": {**pq_metrics(voltages, currents), "p_subestacao_kw": -float(p_source_kw),
                    "perdas_kw": float(losses_w) / 1000,
                    "q_subestacao_kvar": -float(q_source_kvar),
                    "capacit_kvar_por_elemento": cap_power,
                    "capacit_kvar_total": sum(cap_power.values()),
                    "capacitores_ligados": capacitores,
                    "vminpu_cargas_equivalentes": vmin_carga if mode=="mt_equivalente" else None,
                    "vmaxpu_cargas_equivalentes": vmax_carga if mode=="mt_equivalente" else None,
                    **load_balance,
                    "tap_status": f"taps {taps_mode} fixos: {bank_taps}",
                    "engine": str(dss.Basic.Version()).splitlines()[0],
                    "fases_anomalas": len(anomalous), "modelo_linhas": modelo_linhas, **alteracao_linhas, **extra, **solve_meta},
    }


def numeric_stats(values: list[float]):
    if not values:
        return {"n": 0, "mae": None, "rmse": None, "bias": None,
                "p95_erro_absoluto": None, "max_erro_absoluto": None}
    abs_errors = sorted(abs(x) for x in values)
    n = len(values)
    return {"n": n,
            "mae": statistics.fmean(abs_errors),
            "rmse": math.sqrt(statistics.fmean(x*x for x in values)),
            "bias": statistics.fmean(values),
            "p95_erro_absoluto": abs_errors[min(n-1, math.ceil(0.95*n)-1)],
            "max_erro_absoluto": abs_errors[-1]}


def compare(pp_result: dict, dss_result: dict, output: Path, prefix: str):
    v1, v2 = pp_result["voltages"], dss_result["voltages"]
    c1, c2 = pp_result["lines"], dss_result["lines"]
    keys_v = sorted(set(v1) & set(v2))
    rows_v = [{"bus": bus, "phase": ph, "pandapower_pu": v1[(bus,ph)],
               "opendss_pu": v2[(bus,ph)], "delta_pu": v2[(bus,ph)]-v1[(bus,ph)],
               "abs_delta_pu": abs(v2[(bus,ph)]-v1[(bus,ph)])}
              for bus,ph in keys_v]
    rows_v.sort(key=lambda x: x["abs_delta_pu"], reverse=True)
    keys_c = sorted(set(c1) & set(c2))
    rows_c = []
    for line, ph in keys_c:
        p, q = c1[(line,ph)], c2[(line,ph)]
        rows_c.append({"line": line, "phase": ph,
                       "ampacity_a": p["ampacity_a"],
                       "pandapower_current_a": p["current_a"],
                       "opendss_current_a": q["current_a"],
                       "delta_current_a": q["current_a"]-p["current_a"],
                       "abs_delta_current_a": abs(q["current_a"]-p["current_a"]),
                       "pandapower_loading_pct": p["loading_pct"],
                       "opendss_loading_pct": q["loading_pct"]})
    rows_c.sort(key=lambda x: x["abs_delta_current_a"], reverse=True)
    save_csv(output / f"{prefix}_comparacao_tensoes.csv", rows_v,
             ["bus","phase","pandapower_pu","opendss_pu","delta_pu","abs_delta_pu"])
    save_csv(output / f"{prefix}_comparacao_correntes.csv", rows_c,
             ["line","phase","ampacity_a","pandapower_current_a","opendss_current_a",
              "delta_current_a","abs_delta_current_a","pandapower_loading_pct","opendss_loading_pct"])
    summary = {"comparador": dss_result["name"],
               "fases_fisicas_pandapower": len(v1), "fases_fisicas_opendss": len(v2),
               "fases_comparadas": len(keys_v), "fases_ausentes_dss": len(set(v1)-set(v2)),
               "correntes_pandapower": len(c1), "correntes_opendss": len(c2),
               "correntes_comparadas": len(keys_c),
               "tensao_erro_pu": numeric_stats([x["delta_pu"] for x in rows_v]),
               "corrente_erro_a": numeric_stats([x["delta_current_a"] for x in rows_c]),
               "pior_diferenca_tensao": rows_v[:10],
               "pior_diferenca_corrente": rows_c[:10],
               "observacao": f"Taps fixos ({dss_result['metrics']['tap_status']}); "
                             "o OpenDSS pode aplicar transicao de modelo de carga fora de [vminpu,vmaxpu], "
                             "enquanto pandapower pode manter P constante; divergencias residuais "
                             "incluem acoplamento trifasico, transformador de subestacao e "
                             "equipamentos secundarios sem carga no modo equivalente. "
                             "Nao e a validacao do S0 com regulacao automatica."}
    save_json(output / f"{prefix}_resumo_comparacao.json", summary)
    return summary


def save_model_results(result: dict, out: Path, prefix: str):
    vrows = [{"bus":b, "phase":p, "v_pu":v} for (b,p),v in sorted(result["voltages"].items())]
    irows = [{"line":n,"phase":p,**v} for (n,p),v in sorted(result["lines"].items())]
    save_csv(out / f"{prefix}_tensoes.csv", vrows, ["bus", "phase", "v_pu"])
    save_csv(out / f"{prefix}_correntes.csv", irows,
             ["line", "phase", "current_a", "ampacity_a", "loading_pct"])
    save_json(out / f"{prefix}_metricas.json", result["metrics"])


def comparar_baseline_v3(repo: Path, saida: Path, hour: int, mode: str, arquivo_v3: Path):
    """Compare o experimento sem capacitores ao mesmo caso da V3 com capacitores.

    Resumos da V3 sao lidos somente como dados; nao sao alterados.
    """
    base = arquivo_v3 if arquivo_v3.is_absolute() else repo / arquivo_v3
    name = f"s0_h{hour:02d}_{mode}"
    old = base / f"{name}_comparacao_tensoes.csv"
    new = saida / f"{name}_comparacao_tensoes.csv"
    if not old.is_file():
        print(f"  Comparacao com V3 nao realizada: nao encontrado {old}", flush=True)
        return None
    def load(path):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return {(r["bus"].lower(), r["phase"]):float(r["delta_pu"])
                    for r in csv.DictReader(stream)}
    v3, v4 = load(old), load(new)
    shared = sorted(set(v3) & set(v4))
    if not shared:
        print("  AVISO: nenhum par de fases compartilhado com a V3", flush=True)
        return None
    def phase_stats(keys):
        e3 = [v3[k] for k in keys]; e4 = [v4[k] for k in keys]
        return {"fases":len(keys), "mae_v3":statistics.fmean(abs(x) for x in e3),
                "mae_v4":statistics.fmean(abs(x) for x in e4),
                "bias_v3":statistics.fmean(e3), "bias_v4":statistics.fmean(e4)}
    rows = []
    for ph in ("A", "B", "C", "TODAS"):
        keys = [k for k in shared if k[1] == ph or ph == "TODAS"]
        if not keys:continue
        row={"fase":ph,**phase_stats(keys)}
        row["diferenca_mae_v4_menos_v3"] = row["mae_v4"]-row["mae_v3"]
        rows.append(row)
    save_csv(saida/f"{name}_efeito_dos_capacitores.csv",rows,
             ["fase","fases","mae_v3","mae_v4","bias_v3","bias_v4","diferenca_mae_v4_menos_v3"])
    summary={"hora":hour,"modo":mode,"baseline_v3":str(old),"comparacao_v4":str(new),
             "estatisticas":rows,
             "observacao":"Desligar capacitores modifica o proprio sistema; melhora de MAE e um diagnostico, nao prova causal unica. "
                          "As outras diferencas permanecem (matrizes, modelos de cargas, transformadores)."}
    save_json(saida/f"{name}_diagnostico_v3_v4.json",summary)
    print("  MAE V3 (com capacitores) -> V4:",
          round(rows[-1]["mae_v3"],6),"->",round(rows[-1]["mae_v4"],6), flush=True)
    return summary


def diagnostico_linhas(repo:Path,data:dict,saida:Path,hour:int,mode:str):
    """Agrupa erros de corrente e saltos de erro de tensao por trecho e fase fisica."""
    name=f"s0_h{hour:02d}_{mode}"
    path=saida/f"{name}_comparacao_tensoes.csv"
    if not path.exists():return
    with path.open(encoding="utf-8-sig",newline="") as f:
        volts={(r["bus"].lower(),r["phase"]):float(r["delta_pu"])
               for r in csv.DictReader(f)}
    edges=[]
    for line in data["lines"]:
        b1,b2=line["from_bus"].lower(),line["to_bus"].lower()
        for ph in line["phases"]:
            d1=volts.get((b1,ph));d2=volts.get((b2,ph))
            if d1 is None or d2 is None:continue
            edges.append({"linha":line["id"],"fase":ph,
                          "fases_fisicas":len(line["phases"]),
                          "delta_na_origem_pu":d1,"delta_no_destino_pu":d2,
                          "salto_delta_pu":d2-d1,
                          "abs_salto_delta_pu":abs(d2-d1),
                          "comprimento_km":line["length_km"]})
    edges.sort(key=lambda x:x["abs_salto_delta_pu"],reverse=True)
    save_csv(saida/f"{name}_saltos_erro_tensao.csv",edges,
             ["linha","fase","fases_fisicas","delta_na_origem_pu","delta_no_destino_pu",
              "salto_delta_pu","abs_salto_delta_pu","comprimento_km"])
    path_i=saida/f"{name}_comparacao_correntes.csv"
    results={"total_linhas_mt":len(data["lines"]),
             "linhas_monofasicas":sum(len(l["phases"])==1 for l in data["lines"]),
             "linhas_bifasicas":sum(len(l["phases"])==2 for l in data["lines"]),
             "linhas_trifasicas":sum(len(l["phases"])==3 for l in data["lines"]),
             "saltos_tensao_maiores":edges[:20],
             "nota":"Saltos entre extremidades nao provam erro na linha: podem refletir diferencas propagadas de redes a montante."}
    if path_i.is_file():
        phases={l["id"].lower():len(l["phases"]) for l in data["lines"]}
        groups={1:[],2:[],3:[]}
        with path_i.open(encoding="utf-8-sig",newline="") as stream:
            for r in csv.DictReader(stream):
                n=phases.get(r["line"].lower())
                if n in groups:groups[n].append(float(r["abs_delta_current_a"]))
        results["corrente_mae_por_tipo_linha"]={str(n):{"n_fases_linha":n,"observacoes":len(values),
            "mae_corrente_a":statistics.fmean(values) if values else None}
            for n,values in groups.items()}
    save_json(saida/f"{name}_diagnostico_linhas.json",results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="Raiz do repositorio EVCS-BESS-V2G")
    parser.add_argument("--horas", type=int, nargs="+", default=[18],
                        help="Horas do dia sintetico a comparar, ex.: 3 18")
    parser.add_argument("--taps", choices=["referencia", "neutros"],
                        default="referencia",
                        help="Taps fixos do JSON (padrao) ou neutros para diagnostico")
    parser.add_argument("--capacitores", choices=["ligados", "desligados"],default="ligados",
                        help="Liga ou desliga os quatro bancos de capacitores nos dois motores")
    parser.add_argument("--vmin-carga", type=float, default=0.50,
                        help="Vminpu das cargas MT equivalentes OpenDSS; 0.50 para diagnostico de P constante")
    parser.add_argument("--vmax-carga", type=float, default=1.50,
                        help="Vmaxpu das cargas MT equivalentes OpenDSS; 1.50 para diagnostico")
    parser.add_argument("--modos",choices=["completo", "mt_equivalente"],nargs="+",default=["mt_equivalente"],
                        help="OpenDSS a comparar, padrao apenas MT equivalente")
    parser.add_argument("--modelo-linhas", choices=["originais", "sequencia_3f"], default="sequencia_3f",
                        help="Tratar 614 linhas trifasicas com sequencias equivalentes a pandapower")
    parser.add_argument("--baseline-v3",type=Path,default=Path("results/s0_pareado_v3"),
                        help="Pasta da V3 com capacitores ligados, para comparar MAE")
    parser.add_argument("--saida", type=Path, default=Path("results/s0_pareado_v6_linhas_3f"),
                        help="Pasta de saida (sempre separada dos resultados do artigo)")
    args = parser.parse_args()
    if any(h < 0 or h > 23 for h in args.horas):
        parser.error("Horas validas: 0 a 23")
    if not (0 < args.vmin_carga < args.vmax_carga and args.vmax_carga <= 3.0):
        parser.error("Limites de tensao invalidos: 0 < --vmin-carga < --vmax-carga <= 3")
    repo = args.repo.expanduser().resolve()
    json_file = repo / "src/network/data/ieee8500.json"
    config_file = repo / "configs/ieee8500.yaml"
    if not json_file.is_file() or not config_file.is_file():
        parser.error("Execute na raiz EVCS-BESS-V2G (src/network/data/ieee8500.json e configs/ieee8500.yaml)")
    try:
        import yaml
    except ImportError as exc:
        parser.error("PyYAML nao instalado: python -m pip install pyyaml")
    data = json.loads(json_file.read_text(encoding="utf-8"))
    conf = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    multipliers = conf["load_multipliers"]
    if len(multipliers) != 24:
        parser.error("Esperados 24 multiplicadores de demanda no arquivo de configuracao")
    out = args.saida if args.saida.is_absolute() else repo / args.saida
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "protocolo.json", {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo), "hours": args.horas, "multipliers": multipliers,
        "source_pu": 1.0,
        "taps_mode": args.taps, "taps_pandapower": taps_de_referencia(data) if args.taps == "referencia" else {},
        "capacitores_ligados":args.capacitores == "ligados",
        "modo_dss":args.modos, "modelo_linhas": args.modelo_linhas,
        "taps_opendss": "tap_wdg2 = 1 + 0.00625 * tap_pos; conferir sentido de tensao",
        "limites_tensao_cargas_dss": {"vminpu": args.vmin_carga, "vmaxpu": args.vmax_carga},
        "objetivo": "Diagnostico de equivalencia de P constante para cargas model=1, NUNCA conformidade regulatoria",
        "regulator_and_capacitor_controls": "off; capacitors " + args.capacitores,
        "scope": "S0: sem VE, BESS ou V2G. Nao corresponde ao S0 regulado da Tabela 6.",
        "comparison": "Pandapower MT vs OpenDSS nos modos solicitados; MT equivalente com secundario conectado sem carga",
    })
    caps_on=args.capacitores == "ligados"
    summaries = []
    for hour in dict.fromkeys(args.horas):
        m = float(multipliers[hour])
        name = f"s0_h{hour:02d}"
        print(f"\n[{name}] carregamento {m:.3f}; taps fixos {args.taps}; capacitores {args.capacitores}", flush=True)
        pp_result = run_pandapower(repo,data,m,args.taps,caps_on)
        save_model_results(pp_result,out, name+"_pandapower")
        print("  pandapower: Vmin", round(pp_result["metrics"]["vmin_pu"],6), flush=True)
        for mode in args.modos:
            print(f"  OpenDSS {mode}: resolvendo...", flush=True)
            try:
                ds_result = run_opendss(repo,data,m,mode,args.taps,caps_on,args.vmin_carga,args.vmax_carga,args.modelo_linhas)
            except RuntimeError as exc:
                # Preserva resultados de outros casos e relata falha do motor.
                save_json(out / f"{name}_{mode}_falha.json", {
                    "hora": hour, "multiplicador": m, "taps": args.taps,
                    "modelo": mode, "modelo_linhas": args.modelo_linhas, "erro": str(exc),
                    "atencao": "Sem convergencia nao ha resultado fisico validado."
                })
                print(f"   AVISO: {mode} nao convergiu; detalhes em JSON", flush=True)
                continue
            save_model_results(ds_result,out, name+"_opendss_"+mode)
            comp = compare(pp_result,ds_result,out,name+"_"+mode)
            diagnostico_linhas(repo,data,out,hour,mode)
            # Referencia apropriada: V5, com as mesmas cargas P constante.
            # A unica mudanca opcional e a representacao das linhas trifasicas.
            print("   Vmin:", round(ds_result["metrics"]["vmin_pu"],6),
                  "V MAE:", round(comp["tensao_erro_pu"]["mae"],7),
                  "pares de fases:", comp["fases_comparadas"],
                  "pares de correntes:", comp["correntes_comparadas"],flush=True)
            row = {"hora":hour,"multiplicador":m,"modelo":mode,
                   "vminpu_carga":args.vmin_carga,"vmaxpu_carga":args.vmax_carga,
                   "carga_efetiva_opendss_kw":ds_result["metrics"].get("potencia_efetiva_por_balanco_kw"),
                   "desvio_carga_nominal_opendss_pct":ds_result["metrics"].get("diferenca_pct_nominal"),
                   "capacitores":args.capacitores, "modelo_linhas":args.modelo_linhas,
                   "q_cap_pandapower_kvar":pp_result["metrics"]["capacit_kvar_total"],
                   "q_cap_opendss_kvar":ds_result["metrics"]["capacit_kvar_total"],
                   "panda_vmin":pp_result["metrics"]["vmin_pu"],
                   "dss_vmin":ds_result["metrics"]["vmin_pu"],
                   "panda_sobrecargas":pp_result["metrics"]["linhas_acima_100"],
                   "dss_sobrecargas":ds_result["metrics"]["linhas_acima_100"],
                   "panda_subestacao_kw":pp_result["metrics"]["p_subestacao_kw"],
                   "dss_subestacao_kw":ds_result["metrics"]["p_subestacao_kw"],
                   "panda_perdas_kw":pp_result["metrics"]["perdas_kw"],
                   "dss_perdas_kw":ds_result["metrics"]["perdas_kw"],
                   "mae_v_pu":comp["tensao_erro_pu"]["mae"],
                   "max_abs_delta_v_pu":comp["tensao_erro_pu"]["max_erro_absoluto"],
                   "mae_i_a":comp["corrente_erro_a"]["mae"],
                   "fases_comparadas":comp["fases_comparadas"],
                   "correntes_comparadas":comp["correntes_comparadas"]}
            summaries.append(row)
            if mode=="mt_equivalente" and abs(row["desvio_carga_nominal_opendss_pct"] or 0)>1:
                print("   AVISO: demanda OpenDSS difere da nominal em mais de 1%; ainda nao ha comparabilidade de carga",flush=True)
            save_csv(out / "resumo_pareado.csv",summaries,
                     ["hora","multiplicador","modelo","vminpu_carga","vmaxpu_carga","modelo_linhas","carga_efetiva_opendss_kw","desvio_carga_nominal_opendss_pct","capacitores",
                      "q_cap_pandapower_kvar","q_cap_opendss_kvar","panda_vmin","dss_vmin",
                      "panda_sobrecargas","dss_sobrecargas",
                      "panda_subestacao_kw","dss_subestacao_kw","panda_perdas_kw","dss_perdas_kw",
                      "mae_v_pu","max_abs_delta_v_pu",
                      "mae_i_a","fases_comparadas","correntes_comparadas"])
    print("\nOK. Pasta:", out)
    print("Envie resumo_pareado.csv, *_diagnostico_linhas.json e *_metricas.json.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print("\nERRO:", type(err).__name__, str(err),file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
