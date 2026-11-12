"""V2.0 — sondagem elétrica AC de barras selecionadas no OpenDSS.

Sonda perturbações assinadas de potência ativa (positiva = injeção; negativa = absorção),
com estado dos controles CONGELADO no S1 cronológico de cada hora. Não há despacho
SOC, instalação ou verificação de carregadores. Diferenciais locais, não garantia AC.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


def _frame(table) -> pd.DataFrame:
    return pd.DataFrame(table)


def _metric(br: pd.DataFrame, vo: pd.DataFrame) -> dict:
    l = br.loading_pct.to_numpy(float)
    v = vo.v_pu.to_numpy(float)
    return {
        "overload_samples":int((l>100).sum()),
        "thermal_severity_pp":float(np.maximum(0,l-100).sum()),
        "undervoltage_samples":int((v<0.95).sum()),
        "overvoltage_samples":int((v>1.05).sum()),
        "undervoltage_severity_pu":float(np.maximum(0,.95-v).sum()),
        "overvoltage_severity_pu":float(np.maximum(0,v-1.05).sum()),
        "vmin_pu":float(np.min(v)), "vmax_pu":float(np.max(v)),
        "max_line_loading_pct":float(np.max(l)),
    }


def _check_reference(result: dict, ref_dir: Path, h: int):
    """Impedir sensibilidade obtida em condição diferente do S1 usado na triagem."""
    errors={}
    for kind, keys, value, tol in (
        ("voltages", ["hour","bus","phase"], "v_pu", 0.002),
        ("branches", ["hour","line","phase"], "loading_pct", 0.5),
        ("source", ["hour"], "p_kw", 5.0),
    ):
        ref = pd.read_csv(ref_dir/kind/f"{h:02d}.csv") if (ref_dir/kind/f"{h:02d}.csv").is_file() else None
        if ref is None:
            ref = pd.read_csv(ref_dir/f"{kind}.csv")
            ref = ref.loc[ref.hour == h]
        actual = _frame(result[kind])
        joined = ref[keys+[value]].merge(actual[keys+[value]],on=keys,
                    suffixes=("_ref","_now"),how="outer",indicator=True,validate="one_to_one")
        if (joined._merge != "both").any() or len(joined) != len(ref) or len(joined) != len(actual):
            raise RuntimeError(f"Replay h={h} {kind} não corresponde ao arquivo S1 (IDs diferentes)")
        max_error=float((joined[value+"_ref"]-joined[value+"_now"]).abs().max())
        errors[kind]=max_error
        if not np.isfinite(max_error) or max_error > tol:
            raise RuntimeError(f"Replay S1 h={h}: {kind} delta={max_error:.6f} > {tol}. Interrompido.")
    for kind, key in (("regulators","regulator"),("capacitors","capacitor")):
        reference = ref_dir/f"{kind}.csv"
        if reference.is_file():
            ref = pd.read_csv(reference).loc[lambda x:x.hour==h]
            actual = _frame(result[kind])
            col = "tap" if kind == "regulators" else "states"
            joined=ref.merge(actual,on=["hour",key],how="outer",suffixes=("_ref","_now"),indicator=True)
            if (joined._merge!="both").any() or any(str(x).replace(' ','')!=str(y).replace(' ','')
                for x,y in zip(joined[col+"_ref"],joined[col+"_now"])):
                raise RuntimeError(f"Estado dos controles difere do S1 para h={h} ({kind})")
    return errors


def validate_source_response(power_kw: float, source_delta_kw: float) -> None:
    """Valida somente sinal; perdas e cargas dependentes de V mudam a magnitude."""
    if not np.isfinite(power_kw) or not np.isfinite(source_delta_kw) or abs(power_kw)<1e-10:
        raise RuntimeError("Perturbação ou variação da fonte não finita/nula")
    if (power_kw>0 and source_delta_kw>=-0.1) or (power_kw<0 and source_delta_kw<=0.1):
        raise RuntimeError(f"Sinal da fonte inconsistente: recurso={power_kw}, fonte={source_delta_kw}")


def _matching_effect(base: pd.DataFrame, trial: pd.DataFrame, keys: list[str], var: str) -> pd.DataFrame:
    joined=base[keys+[var]].merge(trial[keys+[var]],on=keys,
                    suffixes=("_base","_probe"),how="outer",indicator=True,validate="one_to_one")
    if (joined._merge != "both").any():
        raise RuntimeError(f"Elementos diferentes após perturbação: {var}")
    return joined.drop(columns="_merge")


def normalize_probe_powers(delta_kw: float, probe_powers_kw=None) -> tuple[float, ...]:
    """Compatibilidade V2.0; opcionais +P e -P para derivadas locais assimétricas."""
    powers = (delta_kw,) if probe_powers_kw is None else tuple(probe_powers_kw)
    if not powers:
        raise ValueError("Forneça pelo menos uma perturbação")
    powers = tuple(float(p) for p in powers)
    if any(not np.isfinite(p) or abs(p) < 1e-10 or abs(p) > 2200 for p in powers):
        raise ValueError("Cada perturbação deve ter módulo em (0, 2200] kW")
    if len(set(powers)) != len(powers):
        raise ValueError("Perturbações repetidas")
    return powers


def probe(repo: Path, baseline: Path, screen_dir: Path, output: Path,
          hours: tuple[int,...]=(18,), delta_kw: float=200., max_per_type: int=4,
          save_derivatives: bool=False, probe_powers_kw=None) -> dict:
    repo,baseline,screen_dir,output=[Path(x).resolve() for x in (repo,baseline,screen_dir,output)]
    powers=normalize_probe_powers(delta_kw, probe_powers_kw)
    if any(int(h) not in range(24) for h in hours) or len(set(hours))!=len(hours):
        raise ValueError("hours precisa conter horários distintos de 0 a 23")
    if max_per_type < 1:
        raise ValueError("max_per_type >=1")
    # Mantém todas as dependências OpenDSS na execução que efetivamente precisa do solver.
    sys.path.insert(0,str(repo/"opendss_rebuild"))
    from rebuild.contracts import load_context
    from rebuild.engine import ODSSEngine, error_checked
    ctx=load_context(repo,repo/"opendss_rebuild")
    if ctx["settings"]["simulation"]["controls"]!="auto":
        raise RuntimeError("Sondagem requer OpenDSS automático, com estados congelados SOMENTE após S1 por hora")
    candidates=[]
    for kind in ("bess","v2g"):
        df=pd.read_csv(screen_dir/f"candidatos_{kind}.csv").head(max_per_type)
        for row in df.itertuples(index=False):
            bus=str(row.bus).lower()
            if ctx["buses"].get(bus)!=set("ABC"):
                raise RuntimeError(f"Barra deixou de ser trifásica no contexto OpenDSS: {bus}")
            candidates.append({"bus":bus,"resource":kind.upper(),"rank":int(row.candidate_rank)})
    unique_buses=sorted({x["bus"] for x in candidates})
    saved=baseline/"S1"/"injections_replayed.csv"
    if not saved.is_file():
        raise FileNotFoundError(f"Faltam injeções S1: {saved}")
    profiles=pd.read_csv(saved)
    cols=["hour","bus","phase","p_kw","q_kvar"]
    if not set(cols) <= set(profiles.columns):
        raise ValueError("injections_replayed.csv não possui colunas esperadas")
    profiles=profiles[cols].copy()
    profiles.bus=profiles.bus.astype(str).str.lower()
    profiles.phase=profiles.phase.astype(str).str.upper()
    if not set(range(24)) <= set(profiles.hour):
        raise ValueError("S1 sem 24 horários")
    placeholders=pd.DataFrame([{"hour":-1,"bus":b,"phase":p,
                                 "p_kw":max(abs(p) for p in powers)/3.,"q_kvar":0.}
                                for b in unique_buses for p in "ABC"])
    engine=ODSSEngine(ctx)
    engine.compile()
    engine.define_assets(pd.concat([profiles,placeholders],ignore_index=True))
    output.mkdir(parents=True,exist_ok=True)
    rows=[];checks=[];line_parts=[];volt_parts=[]
    for h in range(max(hours)+1):
        base=engine.solve_hour(h,float(ctx["multipliers"][h]),profiles)
        if h not in hours:
            continue
        check=_check_reference(base,baseline/"S1",h)
        checks.append({"hour":h, **{f"max_error_{k}":v for k,v in check.items()}})
        print(f"[S1 verificado] h={h:02d}: erro de tensão {check['voltages']:.6g} pu")
        error_checked(engine.dss,"Set controlmode=off")
        br0=_frame(base["branches"]);vo0=_frame(base["voltages"])
        s0_metrics=_metric(br0,vo0)
        for b in unique_buses:
            for power in powers:
                injection=pd.DataFrame([{"hour":h,"bus":b,"phase":p,
                                          "p_kw":power/3.,"q_kvar":0.}
                                          for p in "ABC"])
                augmented=pd.concat([profiles.loc[profiles.hour==h],injection],ignore_index=True)
                engine.update_assets(augmented)
                error_checked(engine.dss,"Solve")
                if not engine.dss.Solution.Converged():
                    raise RuntimeError(f"Sondagem não convergiu: h={h} bus={b} power={power}")
                pert=engine.read_results(h)
                br1=_frame(pert["branches"]);vo1=_frame(pert["voltages"])
                metrics=_metric(br1,vo1)
                base_p=float(base["source"][0]["p_kw"])
                pert_p=float(pert["source"][0]["p_kw"])
                source_delta=pert_p-base_p
                # A subestação deve reagir ao sinal de ΔP local. Perdas e cargas
                # dependentes da tensão fazem ΔP_fonte diferir de -ΔP_recurso.
                try:
                    validate_source_response(power,source_delta)
                except RuntimeError as exc:
                    raise RuntimeError(f"Barra {b}, h={h}: {exc}") from exc
                for label in [c for c in candidates if c["bus"]==b]:
                    rows.append({"hour":h,**label,"probe_kw":power,
                        "direction":"discharge_or_load_reduction" if power>0 else "charge_or_load_increase",
                        "source_delta_kw":source_delta,
                        "loss_delta_kw":float(pert["source"][0]["loss_kw"]-base["source"][0]["loss_kw"]),
                        **{f"base_{k}":v for k,v in s0_metrics.items()},
                        **{f"after_{k}":v for k,v in metrics.items()},
                        **{f"change_{k}":metrics[k]-s0_metrics[k] for k in s0_metrics}})
                if save_derivatives:
                    lines=_matching_effect(br0,br1,["hour","line","phase"],"loading_pct")
                    lines["bus_injection"]=b
                    lines["probe_kw"]=power
                    lines["d_loading_pct_per_kw"]=(lines.loading_pct_probe-lines.loading_pct_base)/power
                    line_parts.append(lines)
                    volts=_matching_effect(vo0,vo1,["hour","bus","phase"],"v_pu")
                    volts["bus_injection"]=b
                    volts["probe_kw"]=power
                    volts["d_v_pu_per_kw"]=(volts.v_pu_probe-volts.v_pu_base)/power
                    volt_parts.append(volts)
                # É obrigatório devolver o circuito ao S1 antes da próxima
                # combinação barra/sinal, mantendo taps/caps congelados.
                engine.update_assets(profiles.loc[profiles.hour==h])
                error_checked(engine.dss,"Solve")
                if not engine.dss.Solution.Converged():
                    raise RuntimeError("Não convergiu ao retornar à referência S1")
        error_checked(engine.dss,"Set controlmode=static")
    table=pd.DataFrame(rows).sort_values(["resource","rank","hour","probe_kw"])
    table.to_csv(output/"sondagens_ac_v20.csv",index=False)
    pd.DataFrame(checks).to_csv(output/"verificacao_baseline_s1.csv",index=False)
    if save_derivatives:
        pd.concat(line_parts,ignore_index=True).to_csv(output/"derivadas_linhas.csv.gz",index=False,compression="gzip")
        pd.concat(volt_parts,ignore_index=True).to_csv(output/"derivadas_tensoes.csv.gz",index=False,compression="gzip")
    manifest={"status":"LOCAL_FROZEN_CONTROL_FINITE_DIFFERENCE_NOT_PLACEMENT_OPTIMIZATION",
              "generated_utc":datetime.now(timezone.utc).isoformat(),
              "hours":list(hours),"probe_kw":delta_kw if len(powers)==1 else None,
              "probe_powers_kw":list(powers),"candidate_buses":unique_buses,
              "n_physical_probes":len(unique_buses)*len(hours)*len(powers),
              "assumptions":["Controles congelados após reproduzir S1 cronológico",
                             "Potência ativa assinada repartida igualmente em ABC",
                             "Resultados locais não são otimizador estocástico nem certificação de interconexão",
                             "Sem SOC, contingências, capacidade de transformadores adicionais ou tráfego de veículos"]}
    (output/"manifest_sondagem.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    return manifest
