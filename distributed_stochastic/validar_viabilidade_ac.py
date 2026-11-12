"""Audit AC electrical feasibility of a 24h OpenDSS replay against S0.

Strict: 0.95 <= V <= 1.05 pu and loading <= 100% for every sample.
Baseline non-aggravation: loading <= max(100, S0 loading) and
  min(0.95,S0 V) <= V <= max(1.05,S0 V), for each matching phase-hour.

Neither criterion asserts equipment CAPEX, EV mobility, charging-port ratings,
BESS energy limits, contingency resilience, or stochastic robustness.

Example:
 python -m distributed_stochastic.validar_viabilidade_ac \
   --baseline ../EVCS-BESS-V2G/results/replay_s0_s4_opendss \
   --candidate results/v21_siting_pilot/AC \
   --output results/v21_siting_pilot/viabilidade_ac
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd


def load_table(path: Path, keys: list[str], value: str, hours: int) -> pd.DataFrame:
    if not path.is_file():
        raise ValueError(f"Arquivo não encontrado: {path}")
    a = pd.read_csv(path)
    wanted = keys + [value]
    missing = set(wanted) - set(a.columns)
    if missing:
        raise ValueError(f"{path}: colunas ausentes: {sorted(missing)}")
    if a[keys].isna().any().any() or a[value].isna().any():
        raise ValueError(f"{path}: chaves ou valores nulos")
    if a.duplicated(keys).any():
        raise ValueError(f"{path}: chaves duplicadas: {keys}")
    if not np.isfinite(pd.to_numeric(a[value], errors="coerce")).all():
        raise ValueError(f"{path}: valores não finitos")
    if set(a.hour.astype(int)) != set(range(hours)) or (a.hour != a.hour.astype(int)).any():
        raise ValueError(f"{path}: horários incompatíveis com 0..{hours - 1}")
    return a[wanted].copy()


def pair_samples(s0: pd.DataFrame, ac: pd.DataFrame, keys: list[str], value: str) -> pd.DataFrame:
    merged = s0.merge(ac, on=keys, suffixes=("_s0", "_ac"), how="outer", indicator=True,
                      validate="one_to_one")
    missing = merged[merged["_merge"] != "both"]
    if len(missing):
        raise ValueError(f"Amostras não correspondentes entre S0 e AC: {len(missing)}; "
                         f"exemplos={missing[keys + ['_merge']].head(3).to_dict('records')}")
    return merged.drop(columns="_merge")


def evaluate(branch: pd.DataFrame, volt: pd.DataFrame, *, v_min: float = 0.95,
             v_max: float = 1.05, thermal_pct: float = 100.,
             tol_v_pu: float = 1e-6, tol_thermal_pp: float = 1e-3):
    """Return summary and per-phase-hour violation tables (tolerances numeric only)."""
    b = branch.copy()
    v = volt.copy()
    b["strict_limit_pct"] = thermal_pct
    b["baseline_limit_pct"] = np.maximum(thermal_pct, b.loading_pct_s0)
    b["strict_excess_pp"] = np.maximum(0., b.loading_pct_ac - thermal_pct)
    b["baseline_excess_pp"] = np.maximum(0., b.loading_pct_ac - b.baseline_limit_pct)
    b["strict_violation"] = b.loading_pct_ac > thermal_pct + tol_thermal_pp
    b["baseline_aggravation"] = b.loading_pct_ac > b.baseline_limit_pct + tol_thermal_pp
    b["new_overload_vs_s0"] = (b.loading_pct_s0 <= thermal_pct + tol_thermal_pp) & b.strict_violation

    v["strict_low_limit_pu"] = v_min
    v["strict_high_limit_pu"] = v_max
    v["baseline_low_limit_pu"] = np.minimum(v_min, v.v_pu_s0)
    v["baseline_high_limit_pu"] = np.maximum(v_max, v.v_pu_s0)
    v["strict_under_depth_pu"] = np.maximum(0., v_min - v.v_pu_ac)
    v["strict_over_depth_pu"] = np.maximum(0., v.v_pu_ac - v_max)
    v["baseline_under_depth_pu"] = np.maximum(0., v.baseline_low_limit_pu - v.v_pu_ac)
    v["baseline_over_depth_pu"] = np.maximum(0., v.v_pu_ac - v.baseline_high_limit_pu)
    v["strict_under"] = v.v_pu_ac < v_min - tol_v_pu
    v["strict_over"] = v.v_pu_ac > v_max + tol_v_pu
    v["baseline_under_aggravation"] = v.v_pu_ac < v.baseline_low_limit_pu - tol_v_pu
    v["baseline_over_aggravation"] = v.v_pu_ac > v.baseline_high_limit_pu + tol_v_pu
    v["baseline_aggravation"] = v.baseline_under_aggravation | v.baseline_over_aggravation

    # Distinguish lines with overload at ANY time in S0 from phase-hour new events.
    s0_overloaded_lines = set(b.loc[b.loading_pct_s0 > thermal_pct + tol_thermal_pp, "line"])
    ac_overloaded_lines = set(b.loc[b.strict_violation, "line"])
    new_distinct_lines = ac_overloaded_lines - s0_overloaded_lines

    strict_v_bad = v.strict_under | v.strict_over
    aggr_v_bad = v.baseline_aggravation
    summary = {
        "verification_scope": "all_matching_phase_hour_samples_in_export",
        "criterion_strict": f"{v_min} <= voltage pu <= {v_max}; line loading <= {thermal_pct}%",
        "criterion_relative_s0": "No per-line-phase-hour or bus-phase-hour deterioration outside existing S0 violation envelope",
        "tolerance_voltage_pu": tol_v_pu,
        "tolerance_thermal_pp": tol_thermal_pp,
        "branch_samples": int(len(b)), "voltage_samples": int(len(v)),
        "hours": sorted(set(map(int, b.hour))),
        "strict_overload_samples": int(b.strict_violation.sum()),
        "strict_overloaded_unique_lines": int(b.loc[b.strict_violation, "line"].nunique()),
        "strict_undervoltage_samples": int(v.strict_under.sum()),
        "strict_overvoltage_samples": int(v.strict_over.sum()),
        "strict_voltage_samples": int(strict_v_bad.sum()),
        "new_overload_samples_relative_s0": int(b.new_overload_vs_s0.sum()),
        "new_distinct_overloaded_lines_relative_s0": int(len(new_distinct_lines)),
        "new_distinct_overloaded_line_ids": sorted(new_distinct_lines),
        "distinct_lines_with_new_hourly_overload_vs_s0": int(b.loc[b.new_overload_vs_s0, "line"].nunique()),
        "thermal_aggravations_relative_s0": int(b.baseline_aggravation.sum()),
        "voltage_aggravations_relative_s0": int(aggr_v_bad.sum()),
        "max_loading_pct": float(b.loading_pct_ac.max()),
        "min_voltage_pu": float(v.v_pu_ac.min()),
        "max_voltage_pu": float(v.v_pu_ac.max()),
        "strict_thermal_severity_pp_hour": float(b.strict_excess_pp.sum()),
        "relative_s0_thermal_severity_pp_hour": float(b.baseline_excess_pp.sum()),
        "relative_s0_voltage_severity_pu_hour": float(v.baseline_under_depth_pu.sum()+v.baseline_over_depth_pu.sum()),
        "strict_pass": bool((~b.strict_violation).all() and (~strict_v_bad).all()),
        "relative_s0_no_aggravation_pass": bool((~b.baseline_aggravation).all() and (~aggr_v_bad).all()),
        "full_operational_feasibility_certified": False,
        "uncertified_items": [
            "Equipment/transformer/charger rating and connection engineering",
            "BESS SOC, energy conservation and end-of-day target",
            "Individual V2G mobility, trip energy, charger ports and SOC",
            "N-1 security, dynamic control limits, harmonics and protection",
            "Stochastic uncertainty and out-of-sample network feasibility",
            "Convergence/solver diagnostics not included in exported CSVs",
        ],
    }
    return summary, b, v


def run(baseline: Path, candidate: Path, output: Path, *, hours=24, v_min=0.95, v_max=1.05,
        thermal_pct=100., tol_v_pu=1e-6, tol_thermal_pp=1e-3):
    s0 = baseline / "S0"
    br_keys = ["hour", "line", "phase"]
    vo_keys = ["hour", "bus", "phase"]
    b = pair_samples(load_table(s0/"branches.csv",br_keys,"loading_pct",hours),
                     load_table(candidate/"branches.csv",br_keys,"loading_pct",hours),br_keys,"loading_pct")
    v = pair_samples(load_table(s0/"voltages.csv",vo_keys,"v_pu",hours),
                     load_table(candidate/"voltages.csv",vo_keys,"v_pu",hours),vo_keys,"v_pu")
    summary,b,v = evaluate(b,v,v_min=v_min,v_max=v_max,thermal_pct=thermal_pct,
                           tol_v_pu=tol_v_pu,tol_thermal_pp=tol_thermal_pp)
    summary.update(baseline_s0=str(s0.resolve()),candidate_ac=str(candidate.resolve()),
                   output=str(output.resolve()),nominal_hours=hours)
    output.mkdir(parents=True, exist_ok=True)
    (output/"viabilidade_ac.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    bad_b = b[b.strict_violation | b.baseline_aggravation]
    bad_v = v[v.strict_under | v.strict_over | v.baseline_aggravation]
    bad_b.sort_values(["baseline_excess_pp","strict_excess_pp"],ascending=False).to_csv(output/"violacoes_termicas.csv",index=False)
    bad_v.sort_values(["baseline_under_depth_pu","baseline_over_depth_pu","strict_under_depth_pu","strict_over_depth_pu"],ascending=False).to_csv(output/"violacoes_tensao.csv",index=False)
    per_hour = pd.DataFrame({"hour":list(range(hours))})
    for source,prefix in ((b,"line"),(v,"voltage")):
        if prefix=="line":
            cols={"strict_violation":"overload_samples", "baseline_aggravation":"thermal_aggravations",
                  "strict_excess_pp":"thermal_severity_pp"}
        else:
            cols={"strict_under":"undervoltage_samples","strict_over":"overvoltage_samples",
                  "baseline_aggravation":"voltage_aggravations"}
        stats=source.groupby("hour")[list(cols)].agg({k:"sum" for k in cols}).rename(columns=cols).reset_index()
        per_hour=per_hour.merge(stats,on="hour",how="left")
    per_hour.to_csv(output/"indicadores_horarios.csv",index=False)
    print(json.dumps({k:summary[k] for k in ("strict_pass","relative_s0_no_aggravation_pass",
        "strict_overload_samples","strict_voltage_samples","thermal_aggravations_relative_s0",
        "voltage_aggravations_relative_s0","new_distinct_overloaded_lines_relative_s0")},indent=2))
    print("Relatório:",output/"viabilidade_ac.json")
    return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline",type=Path,required=True,help="Pasta replay_s0_s4_opendss com S0/")
    parser.add_argument("--candidate",type=Path,required=True,help="Pasta AC/ com branches.csv e voltages.csv")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--hours",type=int,default=24)
    parser.add_argument("--v-min",type=float,default=0.95)
    parser.add_argument("--v-max",type=float,default=1.05)
    parser.add_argument("--thermal-pct",type=float,default=100.0)
    parser.add_argument("--tol-v-pu",type=float,default=1e-6)
    parser.add_argument("--tol-thermal-pp",type=float,default=1e-3)
    parser.add_argument("--no-fail",action="store_true",help="Exit 0 on violations: diagnosis-only")
    args=parser.parse_args(argv)
    try:
        if not (0 < args.v_min < args.v_max) or args.hours <= 0 or args.thermal_pct <= 0:
            raise ValueError("Limites ou duração inválidos")
        summary=run(args.baseline,args.candidate,args.output,hours=args.hours,
                    v_min=args.v_min,v_max=args.v_max,thermal_pct=args.thermal_pct,
                    tol_v_pu=args.tol_v_pu,tol_thermal_pp=args.tol_thermal_pp)
    except (ValueError,FileNotFoundError,KeyError) as e:
        print("ERROR:",e,file=sys.stderr)
        return 1
    # A violation is an audit failure, not a solver crash. --no-fail is useful for data exploration.
    return 0 if args.no_fail or summary["strict_pass"] else 2


if __name__=="__main__":
    raise SystemExit(main())
