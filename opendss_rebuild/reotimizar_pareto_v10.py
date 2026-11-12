#!/usr/bin/env python3
r"""V1.0 | AC-verified Pareto exploration for BESS + V2G on the IEEE 8500.

New workstream intended for an Applied Energy-quality methodology. Imports and
reuses existing team contracts and OpenDSS V0.9 physics, WITHOUT modifying the
legacy paper/results. It DOES NOT certify AC feasibility or publication quality.

Multiobjective approach: use several MILP scalarizations, optionally adding
separate epsilon upper bounds on *predicted* under/overvoltage severities; then
replay each candidate AC with automatic regulators/capacitors. Preserve the
nondominated set based on verified AC thermal severity, under/overvoltage
severities and peak import (four objectives, all minimized).

Run from repo root (PowerShell):
 python .\opendss_rebuild\reotimizar_pareto_v10.py `
   --results .\results\replay_s0_s4_opendss `
   --v08-dir .\results\joint_opendss_v08 `
   --v09-dir .\results\joint_opendss_v09_teste `
   --output .\results\joint_opendss_v10 --task run
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback

import numpy as np
import pandas as pd

import reotimizar_bess_v2g as v08
import reotimizar_sequencial_v09 as v09
from rebuild.contracts import load_context, sha256
from rebuild.joint_optimization import OptSettings, solve_joint

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TABLES = v09.TABLES
OBJECTIVES = ("thermal_vs_s0_pph", "under_vs_s0_puh", "over_vs_s0_puh", "peak_import_kw")
AC_WEIGHTS = {"thermal": 40.0, "voltage": 5000.0, "peak": 0.5}  # for traceability, not Pareto ranking

# (thermal weight, under weight, over weight, peak weight, relative epsilon caps)
# Budgets are reference-dependent and constrain only the LOCAL MILP approximation.
PROFILES = {
    "balanced": (40.0, 5000.0, 5000.0, 0.5, None, None),
    "thermal": (110.0, 7000.0, 7000.0, 0.4, None, None),
    "under_guard": (55.0, 20000.0, 7500.0, 0.4, 1.03, None),
    "over_guard": (55.0, 7500.0, 20000.0, 0.4, None, 1.03),
    "voltage_guard": (35.0, 18000.0, 18000.0, 0.4, 1.03, 1.03),
}


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def non_dominated(scores: list[dict], tolerance=(1e-4, 1e-6, 1e-6, 0.02)) -> list[bool]:
    """Actual four-dimensional AC Pareto test, with fixed unit-aware tolerances."""
    data = [np.asarray([s[k] for k in OBJECTIVES], dtype=float) for s in scores]
    if any(not np.isfinite(x).all() for x in data):
        raise ValueError("Não é possível classificar Pareto com score não finito")
    eps = np.asarray(tolerance, float)
    result = []
    for i, x in enumerate(data):
        dominated = False
        for j, y in enumerate(data):
            if i != j and np.all(y <= x + eps) and np.any(y < x - eps):
                dominated = True
                break
        result.append(not dominated)
    return result


def energy_and_fleet_check(bess: np.ndarray, vehicle_power: np.ndarray, team: dict) -> None:
    """Validate adopted candidate commands against the ORIGINAL operational contracts."""
    from v2g.strategies import fleet_profile
    from bess.model import simulate

    if bess.shape != (24,) or vehicle_power.shape != (len(team["vehicles"]), 24):
        raise ValueError("A solução não contém os 24 horários ou todos os veículos")
    if not (np.isfinite(bess).all() and np.isfinite(vehicle_power).all()):
        raise ValueError("Despachos têm valores não finitos")
    battery = team["battery"]
    state = simulate(battery, bess, team["grid"].dt_h, final_soc=battery.soc_initial)
    if np.max(np.abs(state.rejected_kw)) > 0.001:
        raise ValueError("BESS: potência rejeitada por SOC/potência")
    mask = np.zeros(24, bool)
    start, end = team["discharge_window"]
    mask[start:end] = True
    fleet_profile(team["station"], team["grid"], list(team["vehicles"]), vehicle_power,
                  independent_evcs_kw=team["ind"],
                  independent_ids=("independent-demand-group",),
                  discharge_mask=mask, enable_v2g=True)


def read_saved_candidate(folder: Path, name: str, team: dict, s1: pd.DataFrame,
                         original_fleet: pd.DataFrame) -> dict:
    """Only accept complete, physically replayed AC tables and exact signed injections."""
    if not all((folder / f"{x}.csv").is_file() for x in (*TABLES, "dispatch_24h", "vehicle_dispatch", "injections_reoptimized")):
        raise FileNotFoundError(f"Candidato incompleto: {folder}")
    dfs = v09.load_tables(folder)
    for tb, key, value in (("source", ["hour"], "p_kw"),
                           ("branches", ["hour", "line", "phase"], "loading_pct"),
                           ("voltages", ["hour", "bus", "phase"], "v_pu")):
        d = dfs[tb]
        if d.duplicated(key).any() or not set(d.hour) == set(range(24)) or not np.isfinite(d[value].to_numpy(float)).all():
            raise ValueError(f"Dados AC duplicados, incompletos ou inválidos: {folder}/{tb}.csv")
    dispatch = pd.read_csv(folder / "dispatch_24h.csv").sort_values("hour")
    if dispatch.hour.tolist() != list(range(24)):
        raise ValueError(f"Despacho não cobre 24 horas: {folder}")
    bess = dispatch.bess_net_injection_kw.to_numpy(float)
    fleet = dispatch.v2g_fleet_net_injection_kw.to_numpy(float)
    vp = pd.read_csv(folder / "vehicle_dispatch.csv")
    if vp.duplicated(["hour", "vehicle_id"]).any() or set(vp.vehicle_id) != {v.id for v in team["vehicles"]}:
        raise ValueError("Despacho de veículos duplicado ou incompleto")
    ids = [v.id for v in team["vehicles"]]
    matrix = vp.pivot(index="vehicle_id", columns="hour", values="p_kw").loc[ids, list(range(24))].to_numpy(float)
    if not np.allclose(matrix.sum(axis=0), fleet, atol=0.001):
        raise ValueError("Potência da frota não corresponde aos veículos individuais")
    energy_and_fleet_check(bess, matrix, team)
    raw = pd.read_csv(folder / "injections_reoptimized.csv")
    raw["bus"] = raw.bus.astype(str).str.lower()
    raw["phase"] = raw.phase.astype(str).str.upper()
    raw = raw.groupby(["hour", "bus", "phase"], as_index=False)[["p_kw", "q_kvar"]].sum()
    reconstructed = v08.build_scenario_injections(s1, original_fleet, team,
                      {"bess_net_kw": bess, "v2g_net_kw": fleet})
    joint = raw.merge(reconstructed, on=["hour", "bus", "phase"],
                      suffixes=("_saved", "_rebuilt"), how="outer", indicator=True, validate="one_to_one")
    if ((joint._merge != "both").any() or
        not np.allclose(joint[["p_kw_saved", "q_kvar_saved"]].to_numpy(float),
                        joint[["p_kw_rebuilt", "q_kvar_rebuilt"]].to_numpy(float), atol=0.001)):
        raise RuntimeError(f"Injeções não reproduzem os contratos de Kenia/Cesar em {folder}")
    return {"name": name, "folder": folder, "dfs": dfs, "profile": raw,
            "bess": bess, "fleet": fleet, "vehicles": matrix}


def candidate_score(s0: dict, item: dict) -> dict:
    return v09.relative_score(s0, item["dfs"], AC_WEIGHTS)


def solution_hash(item: dict) -> str:
    data = np.concatenate([np.asarray(item["bess"], float), np.asarray(item["vehicles"], float).ravel()])
    return hashlib.sha256(np.round(data, decimals=5).tobytes()).hexdigest()


def solution_from_milp(solution: dict, folder: Path, name: str) -> dict:
    return {"name": name, "folder": folder,
            "bess": solution["bess_net_kw"], "fleet": solution["v2g_net_kw"],
            "vehicles": solution["vehicle_power_kw"]}


def summarize(item: dict, s0: dict, source: str = "OpenDSS AC saved") -> dict:
    score = candidate_score(s0, item)
    br = item["dfs"]["branches"]
    v = item["dfs"]["voltages"]
    source_df = item["dfs"]["source"]
    s0_ids = set(s0["branches"].loc[s0["branches"].loading_pct > 100., "line"])
    now_ids = set(br.loc[br.loading_pct > 100., "line"])
    info = dict(score)
    info.update(name=item["name"], directory=str(item["folder"]), provenance=source,
                min_voltage_pu=float(v.v_pu.min()), max_voltage_pu=float(v.v_pu.max()),
                max_loading_pct=float(br.loading_pct.max()),
                new_distinct_lines_vs_s0=len(now_ids - s0_ids),
                source_import_kwh=float(np.maximum(source_df.p_kw.to_numpy(float), 0).sum()))
    return info


def config_for(profile_name: str, refscore: dict, trust_b: float, trust_v: float,
               time_limit_s: float, mip_gap: float, team: dict) -> OptSettings:
    th, under, over, peak, under_epsilon, over_epsilon = PROFILES[profile_name]
    def cap(key, mult):
        return None if mult is None else max(0., refscore[key]) * mult + 0.01
    return OptSettings(thermal_weight=th, undervoltage_weight=under,
                       overvoltage_weight=over, peak_weight=peak,
                       bess_trust_kw=trust_b, v2g_trust_kw=trust_v,
                       max_under_slack_puh=cap("under_vs_s0_puh", under_epsilon),
                       max_over_slack_puh=cap("over_vs_s0_puh", over_epsilon),
                       bess_throughput_usd_kwh=float(team["battery"].degradation_per_kwh),
                       vehicle_degradation_usd_kwh=float(team["configs"]["v2g"]["cesar_v1"].get("degradation_usd_kwh", 0.)),
                       solver_time_limit_s=time_limit_s, mip_rel_gap=mip_gap)


def ac_feasibility(s0: dict, s1: dict, item: dict) -> dict:
    """An explicit *independent AC* feasibility flag, not a MILP claim."""
    _, check, _, _ = v08.score(s0, s1, item["dfs"])
    return check



def write_frontier(output: Path, pool: list[dict], s0: dict, legacy: dict | None,
                   attempts: list[dict], settings: dict) -> None:
    scores = [p["score"] for p in pool]
    mask = non_dominated(scores)
    summaries = []
    for item, nd in zip(pool, mask):
        z = dict(item["score"])
        z.update(name=item["name"], nondominated=bool(nd),
                 origin=item["origin"], folder=str(item["folder"]),
                 ac_no_aggravation=bool(item["check"].get("S4_completely_satisfies_no_aggravation", False)))
        summaries.append(z)
    pd.DataFrame(summaries).to_csv(output / "todos_candidatos_ac.csv", index=False)
    pd.DataFrame([x for x in summaries if x["nondominated"]]).to_csv(output / "fronteira_pareto_ac.csv", index=False)
    pd.DataFrame(attempts).to_csv(output / "historico_tentativas.csv", index=False)
    save_json(output / "resumo_final.json", {
        "status": "PARETO_AC_EVALUATED_NO_GLOBAL_OPTIMALITY_OR_FEASIBILITY_CLAIM",
        "n_ac_verified_candidates": len(pool),
        "n_nondominated_ac": int(sum(mask)),
        "n_failed_or_rejected_milp": int(sum(x.get("status") == "failed" for x in attempts)),
        "ac_no_aggravation_candidates": [x["name"] for x in summaries if x["ac_no_aggravation"]],
        "nondominated_candidates": [x["name"] for x in summaries if x["nondominated"]],
        "legacy_s4_score": legacy,
        "objectives_minimize": list(OBJECTIVES),
        "settings": settings,
        "limitations": [
            "24 snapshots horários sintéticos, não medida real de Honduras",
            "Ampacidades 'study' hipotéticas; fonte IEEE original 1.05 p.u.",
            "Derivadas localmente lineares com reguladores/capacitores congelados; replay automático no OpenDSS",
            "A fronteira contém soluções com violações remanescentes; não é prova de segurança operacional",
            "O conjunto não prova Pareto-global ou otimalidade global do problema AC",
            "Não declarar resultado validado para Applied Energy sem auditorias de 15 min, custos e robustez",
        ],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    })


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--repo", type=Path, default=ROOT)
    p.add_argument("--results", type=Path, required=True, help="Replay histórico OpenDSS contendo S0/S1/S4 e dados da frota")
    p.add_argument("--v08-dir", type=Path, required=True, help="Diretório joint_opendss_v08 (contém S4)")
    p.add_argument("--v09-dir", type=Path, default=None, help="Diretório V0.9, incluindo trial_01, trial_02 com AC")
    p.add_argument("--output", type=Path, required=True, help="NOVA pasta vazia de resultados")
    p.add_argument("--task", choices=("inspect", "run"), default="run")
    p.add_argument("--profiles", default="balanced,under_guard,thermal", help="Escolhas: " + ",".join(PROFILES))
    p.add_argument("--max-starts", type=int, default=2, help="Nº de sementes AC por rodada")
    p.add_argument("--max-iterations", type=int, default=1)
    p.add_argument("--max-candidates", type=int, default=12, help="Máximo de MILPs/replays AC")
    p.add_argument("--bess-trust-kw", type=float, default=250.)
    p.add_argument("--v2g-trust-kw", type=float, default=200.)
    p.add_argument("--delta-kw", type=float, default=100.)
    p.add_argument("--time-limit", type=float, default=240.)
    p.add_argument("--mip-gap", type=float, default=.02)
    return p.parse_args()


def main():
    args = parse_args()
    names = [x.strip() for x in args.profiles.split(",") if x.strip()]
    if not names or len(set(names)) != len(names) or any(x not in PROFILES for x in names):
        raise ValueError("Perfis inválidos. Disponíveis: " + ",".join(PROFILES))
    if min(args.max_iterations, args.max_starts, args.max_candidates) < 1:
        raise ValueError("Iterações, sementes e candidatos precisam ser positivos")
    if not (10. <= args.delta_kw <= 250. and args.bess_trust_kw > 0 and args.v2g_trust_kw > 0
            and args.time_limit > 0 and 0 < args.mip_gap < 1):
        raise ValueError("Parâmetros numéricos de otimização inválidos")
    repo, replay, v08dir, out = (x.resolve() for x in (args.repo, args.results, args.v08_dir, args.output))
    v09dir = args.v09_dir.resolve() if args.v09_dir else None
    # The output IS usually under repo/results; only prohibit overwriting immutable inputs.
    if any(out == parent or parent in out.parents for parent in (replay, v08dir, v09dir) if parent is not None):
        raise ValueError("A saída deve ser separada das entradas e benchmarks")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("Pasta de saída não está vazia; nada foi sobrescrito")
    if "max_under_slack_puh" not in OptSettings.__dataclass_fields__:
        raise RuntimeError("Instale o ZIP V1.0 completo: falta a atualização compatível de rebuild/joint_optimization.py")
    if not replay.is_dir() or not v08dir.is_dir() or (v09dir is not None and not v09dir.is_dir()):
        raise FileNotFoundError("Replay, V0.8 ou V0.9 não encontrados")
    out.mkdir(parents=True, exist_ok=True)
    try:
        ctx = load_context(repo, HERE)
        sims, s1 = v08.read_existing(replay)
        team = v08.read_team(repo, replay)
        base = v09.read_seed(v08dir, team, ctx)
        # Seed candidates are read from replayed tables; no result is re-labeled as feasible.
        original = [read_saved_candidate(v08dir / "S4", "v08", team, s1, team["old_fleet_profile"])]
        if v09dir is not None:
            initial = json.loads((v09dir / "initial_ac_score.json").read_text(encoding="utf-8"))
            test = candidate_score(sims["S0"], original[0])
            if any(abs(test[k] - initial[k]) > 1e-3 for k in OBJECTIVES):
                raise RuntimeError("V0.9 não utiliza o mesmo S0 e V0.8; não misturar configurações")
            for folder in sorted(v09dir.glob("iteration_*/trial_*")):
                if (folder / "acceptance.json").is_file() and all((folder / f"{x}.csv").is_file() for x in TABLES):
                    original.append(read_saved_candidate(folder, folder.parent.name + "_" + folder.name,
                                                         team, s1, team["old_fleet_profile"]))
        if all((replay / "S4" / f"{x}.csv").exists() for x in TABLES):
            legacy = summarize({"name": "legacy_s4", "folder": replay / "S4",
                                "dfs": v09.load_tables(replay / "S4")}, sims["S0"])
        else:
            legacy = None
        pool = []
        seen = set()
        for item in original:
            fp = solution_hash(item)
            if fp in seen:
                continue
            seen.add(fp)
            item["score"] = summarize(item, sims["S0"], source="previously AC-replayed")
            item["origin"] = "imported_ac_saved"
            item["check"] = ac_feasibility(sims["S0"], sims["S1"], item)
            pool.append(item)
        if not pool:
            raise RuntimeError("Nenhuma semente AC válida")
        settings_summary = {
            "profiles": names, "max_starts": args.max_starts,
            "max_iterations": args.max_iterations, "max_candidates": args.max_candidates,
            "trust_bess_kw": args.bess_trust_kw, "trust_v2g_kw": args.v2g_trust_kw,
            "delta_kw": args.delta_kw, "source_mode": ctx["settings"]["simulation"]["source_mode"],
            "controls": ctx["settings"]["simulation"]["controls"],
            "source_hashes": {k: sha256(p) for k, p in ctx["files"].items()},
        }
        save_json(out / "configuracao.json", settings_summary)
        attempts: list[dict] = []
        write_frontier(out, pool, sims["S0"], legacy, attempts, settings_summary)
        print(f"[V1.0] Importados {len(pool)} candidatos AC; Pareto={sum(non_dominated([x['score'] for x in pool]))}", flush=True)
        if args.task == "inspect":
            print(f"[INSPECT] Fronteira: {out / 'fronteira_pareto_ac.csv'}")
            return
        num_trials = 0
        for iteration in range(1, args.max_iterations + 1):
            # Expand verified nondominated parents, preferring both thermal and voltage anchors.
            frontier = [p for p, accepted in zip(pool, non_dominated([x["score"] for x in pool])) if accepted]
            frontier.sort(key=lambda x: (x["score"]["thermal_vs_s0_pph"] +
                                        60. * x["score"]["total_voltage_vs_s0_puh"], x["name"]))
            seeds = frontier[:args.max_starts]
            new_this_round = 0
            for parent in seeds:
                if num_trials >= args.max_candidates:
                    break
                refdir = out / f"iteration_{iteration:02d}" / f"seed_{parent['name']}"
                refdir.mkdir(parents=True, exist_ok=True)
                reference = dict(dfs=parent["dfs"], profile=parent["profile"],
                                 bess=parent["bess"], fleet=parent["fleet"],
                                 vehicles=parent["vehicles"])
                # Reference replay must match the already-exported AC files.
                if parent["name"] == "v08" and v09dir is not None:
                    cache = v09dir / "iteration_01" / "sensitivity"
                else:
                    cache = Path("__no_cache__")
                cached_ok = False
                if all((cache / f"{z}.csv").is_file() for z in ("lines", "voltages", "source")) and (cache / "check.json").is_file():
                    info = json.loads((cache / "check.json").read_text(encoding="utf-8"))
                    errors = info.get("replay_max_error", {})
                    cached_ok = (abs(float(info.get("delta_kw", -1)) - args.delta_kw) < 1e-8 and
                                 float(errors.get("voltages", 1)) < 1e-5 and
                                 float(errors.get("branches", 1)) < 0.02 and
                                 float(errors.get("source", 100)) < 2.0)
                if cached_ok:
                    dl = pd.read_csv(cache / "lines.csv")
                    dv = pd.read_csv(cache / "voltages.csv")
                    ds = pd.read_csv(cache / "source.csv")
                    save_json(refdir / "calibration_origin.json", {"reused_previous_calibration": str(cache),
                                                                      "delta_kw": args.delta_kw})
                else:
                    dl, dv, ds, replayerr = v09.finite_difference(ctx, team, reference, refdir, args.delta_kw)
                    save_json(refdir / "calibration_origin.json", {"new_ac_calibration": True,
                                                                      "replay_error": replayerr})
                for profile_name in names:
                    if num_trials >= args.max_candidates:
                        break
                    num_trials += 1
                    tag = f"i{iteration:02d}_{parent['name']}_{profile_name}"
                    trial_dir = out / "candidates" / tag
                    trial_dir.mkdir(parents=True, exist_ok=True)
                    row = {"candidate": tag, "parent": parent["name"], "profile": profile_name,
                           "status": "started", "directory": str(trial_dir)}
                    try:
                        opt = config_for(profile_name, parent["score"], args.bess_trust_kw,
                                         args.v2g_trust_kw, args.time_limit, args.mip_gap, team)
                        solution = solve_joint(
                            vehicles=team["vehicles"], grid=team["grid"], station=team["station"],
                            battery=team["battery"], bess_bus=team["bess_bus"], v2g_bus=team["v2g_bus"],
                            source_s1=parent["dfs"]["source"], lines_s1=parent["dfs"]["branches"],
                            volt_s1=parent["dfs"]["voltages"], lines_s0=sims["S0"]["branches"],
                            volt_s0=sims["S0"]["voltages"], ds_lines=dl, ds_volt=dv, ds_source=ds,
                            fleet_s1_kw=team["old_fleet_net"], fleet_s1_per_vehicle_kw=team["old_fleet"],
                            independent_evcs_kw=team["ind"], discharge_window=team["discharge_window"],
                            no_charge_window=team["no_charge_window"], tariffs=team["tariff"],
                            remuneration=team["remuneration"], settings=opt, mode="S4",
                            network_reference_bess_kw=parent["bess"],
                            network_reference_fleet_kw=parent["fleet"])
                        v09.save_candidate(solution, trial_dir, team)
                        child = solution_from_milp(solution, trial_dir, tag)
                        fp = solution_hash(child)
                        if fp in seen:
                            row.update(status="duplicate_commands", note="No new electrical replay needed")
                            continue
                        if (np.max(np.abs(child["bess"] - parent["bess"])) < 1e-3 and
                            np.max(np.abs(child["fleet"] - parent["fleet"])) < 1e-3):
                            row.update(status="duplicate_injections", note="Existing AC state reused")
                            continue
                        energy_and_fleet_check(child["bess"], child["vehicles"], team)
                        injections = v08.build_scenario_injections(s1, team["old_fleet_profile"], team, solution)
                        observed = v08.simulate_solution(ctx, injections, team, sims["S1"], trial_dir)
                        child.update(dfs=observed, profile=injections)
                        child["score"] = summarize(child, sims["S0"], source="new_v10_openDSS_replay")
                        child["check"] = ac_feasibility(sims["S0"], sims["S1"], child)
                        child["origin"] = "optimized_v10"
                        seen.add(fp)
                        pool.append(child)
                        new_this_round += 1
                        row.update(status="ac_replayed", ac_score=child["score"],
                                   ac_verification=child["check"])
                        save_json(trial_dir / "ac_score.json", child["score"])
                        save_json(trial_dir / "ac_verification.json", child["check"])
                        print(f"[V1.0 AC] {tag}: thermal={child['score']['thermal_vs_s0_pph']:.2f} p.p.-h; "
                              f"under={child['score']['under_vs_s0_puh']:.3f}; "
                              f"over={child['score']['over_vs_s0_puh']:.3f}; "
                              f"peak={child['score']['peak_import_kw']:.0f} kW", flush=True)
                    except Exception as exc:
                        row.update(status="failed", error=str(exc))
                        save_json(trial_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc()})
                        print(f"[V1.0 ERROR] {tag}: {exc}", flush=True)
                    finally:
                        attempts.append(row)
                        write_frontier(out, pool, sims["S0"], legacy, attempts, settings_summary)
            if new_this_round == 0:
                print("[V1.0] Nenhum novo despacho AC nesta rodada; encerrando sem alterar benchmarks.", flush=True)
                break
            if num_trials >= args.max_candidates:
                break
        write_frontier(out, pool, sims["S0"], legacy, attempts, settings_summary)
        front = [x for x, f in zip(pool, non_dominated([x["score"] for x in pool])) if f]
        print(f"\n[V1.0 FEITO] Avaliados {len(pool)} candidatos AC; fronteira={len(front)}; saída={out}", flush=True)
        if not any(p["check"].get("S4_completely_satisfies_no_aggravation") for p in pool):
            print("AVISO: nenhuma solução foi declarada AC-viável; preservar as violações documentadas.", flush=True)
    except Exception as exc:
        save_json(out / "failure.json", {"error": str(exc), "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
