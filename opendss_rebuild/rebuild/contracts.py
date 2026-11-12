"""Contratos verificáveis de parâmetros e perfis elétricos, sem dependência do solver."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yaml

SCENARIOS = {
    "S0": (False, False, False),
    "S1": (True, False, False),
    "S2": (True, True, False),
    "S3": (True, False, True),
    "S4": (True, True, True),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        result = yaml.safe_load(f)
    if not isinstance(result, dict):
        raise ValueError(f"YAML inválido: {path}")
    return result


def repo_files(repo: Path) -> dict:
    files = {
        "master": repo / "data/ieee8500/source/Master.dss",
        "network": repo / "src/network/data/ieee8500.json",
        "ieee8500": repo / "configs/ieee8500.yaml",
        "evcs": repo / "configs/evcs.yaml",
        "bess": repo / "configs/bess.yaml",
        "v2g": repo / "configs/v2g.yaml",
    }
    missing = [str(p) for p in files.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError("Arquivos do projeto original ausentes:\n"+"\n".join(missing))
    return files


def load_context(repo: Path, folder: Path) -> dict:
    files = repo_files(repo)
    cfg = read_yaml(folder / "settings.yaml")
    grid = read_yaml(files["ieee8500"])
    if grid.get("network") != "ieee8500":
        raise ValueError("O estudo deve utilizar ieee8500")
    multipliers = np.asarray(grid["load_multipliers"], float)
    if len(multipliers) != 24 or not np.isfinite(multipliers).all() or np.any(multipliers <= 0):
        raise ValueError("A curva sintética deve conter 24 valores positivos e finitos")
    network = json.loads(files["network"].read_text(encoding="utf-8"))
    buses = {str(b["id"]).lower(): set(b["phases"].upper()) for b in network["buses"]}
    physical_voltage_nodes = {(bus, ph) for bus, phases in buses.items() for ph in phases}
    line_phases = {str(l["id"]).lower():set(l["phases"].upper()) for l in network["lines"]}
    ampacity = {str(l["id"]).lower():float(network["linecodes"][l["linecode"]]["ampacity_a"])
                for l in network["lines"]}
    return dict(repo=repo, folder=folder, files=files, settings=cfg, multipliers=multipliers,
                network=network, buses=buses, physical_voltage_nodes=physical_voltage_nodes,
                line_phases=line_phases, ampacity=ampacity)


def canonical_profile(df: pd.DataFrame, hours: list[int], buses: dict) -> pd.DataFrame:
    """Converte perfis com sinal positivo = injeção para matriz [(hora,bus,fase)]."""
    required = {"time", "bus", "phase", "p_kw", "q_kvar"}
    missing = required-set(df.columns)
    if missing:
        raise ValueError(f"Colunas de injeção ausentes: {sorted(missing)}")
    data = df[list(required)].copy()
    data["bus"] = data.bus.astype(str).str.lower()
    data["phase"] = data.phase.astype(str).str.upper()
    data["p_kw"] = pd.to_numeric(data.p_kw,errors="raise")
    data["q_kvar"] = pd.to_numeric(data.q_kvar,errors="raise")
    if not np.isfinite(data[["p_kw", "q_kvar"]].to_numpy()).all():
        raise ValueError("Injeção com NaN/inf")
    times = pd.to_datetime(data["time"],utc=True)
    if times.isna().any() or len(times)==0:
        raise ValueError("Série temporal vazia/inválida")
    unique = sorted(times.unique())
    if len(unique) != 24:
        raise ValueError(f"Esperadas 24 horas dos módulos originais; encontradas {len(unique)}")
    index = {time:i for i,time in enumerate(unique)}
    data["hour"] = [index[t] for t in times]
    if data.duplicated(["hour","bus","phase"]).any():
        raise ValueError("Perfil contém pares (hora, barra, fase) duplicados")
    bad = data.loc[~data.apply(lambda x:x["bus"] in buses and x["phase"] in buses.get(x["bus"],set()),axis=1)]
    if not bad.empty:
        raise ValueError("Injeção conectada em barra/fase inexistente: "+str(bad[["bus","phase"]].head(10).values.tolist()))
    if set(data.hour.unique()) != set(range(24)):
        raise ValueError("Horas incompletas")
    return data.loc[data.hour.isin(hours),["hour","bus","phase","p_kw","q_kvar"]].sort_values(["hour","bus","phase"]).reset_index(drop=True)


def validate_team_config(ctx):
    e=read_yaml(ctx["files"]["evcs"]); b=read_yaml(ctx["files"]["bess"]);v=read_yaml(ctx["files"]["v2g"])
    checks = {
        "evcs_others":int(e["integration"]["evs"]),
        "v2g_fleet":int(v["fleet"]["n_vehicles"]),
        "v2g_host_evcs":str(e["station"]["bus"]).lower(),
        "v2g_host_v2g":str(v["standalone"]["station"]["bus"]).lower(),
        "bess_bus":str(b["connection"]["bus"]).lower(),
        "bess_capacity_kwh":float(b["battery"]["capacity_kwh"]),
        "bess_power_kw":float(b["battery"]["power_kw"]),
        "bess_soc_initial":float(b["battery"]["soc_initial"]),
        "v2g_chargers":int(e["station"]["chargers"]),
        "v2g_connection_kw":float(e["station"]["connection_kw"]),
        "bess_strategy":str(b["strategy"]),
        "v2g_strategy":str(v["strategy"]),
    }
    if checks["evcs_others"]+checks["v2g_fleet"] != 3000:
        raise ValueError("Frota de S4 não soma 3.000 veículos; revisar hipótese")
    if checks["v2g_host_evcs"] != checks["v2g_host_v2g"]:
        raise ValueError("Pontos de conexão V2G e EVCS não coincidem")
    for bus in (checks["v2g_host_evcs"],checks["bess_bus"]):
        if ctx["buses"].get(bus) != set("ABC"):
            raise ValueError(f"Conexão trifásica não disponível em {bus}")
    return checks


def energy_metrics(sources: pd.DataFrame, voltages: pd.DataFrame, branches: pd.DataFrame, dt_h:float,
                   v_lo:float=.95,v_hi:float=1.05,limit_pct:float=100,tariff_usd_kwh:float=.15) -> dict:
    """Mesma fronteira de cálculo da avaliação original, sem o duplo cômputo de perdas."""
    if sources.empty or voltages.empty or branches.empty:
        raise ValueError("Não calcular métricas em séries vazias")
    p = sources.p_kw.to_numpy(float)
    v = voltages.v_pu.to_numpy(float)
    l = branches.loading_pct.to_numpy(float)
    if not (np.isfinite(p).all() and np.isfinite(v).all() and np.isfinite(l).all()):
        raise ValueError("Métricas com valores não finitos")
    overloaded=branches.loc[branches.loading_pct>limit_pct]
    under=voltages.loc[voltages.v_pu<v_lo]
    over=voltages.loc[voltages.v_pu>v_hi]
    return {
        "peak_import_kw":float(np.maximum(p,0).max()),
        "import_kwh":float(np.maximum(p,0).sum()*dt_h),
        "export_kwh":float(np.maximum(-p,0).sum()*dt_h),
        "energy_cost_usd":float(np.maximum(p,0).sum()*dt_h*tariff_usd_kwh),
        "loss_kwh":float(sources.loss_kw.sum()*dt_h),
        "v_min_pu":float(np.min(v)),
        "v_max_pu":float(np.max(v)),
        "loading_max_pct":float(np.max(l)),
        "voltage_violation_samples":int(len(under)+len(over)),
        "undervoltage_samples":int(len(under)),
        "overvoltage_samples":int(len(over)),
        "overload_samples":int(len(overloaded)),
        "overloaded_unique_lines":int(overloaded.line.nunique()),
        "overloaded_unique_line_phases":int(overloaded[["line","phase"]].drop_duplicates().shape[0]),
        "steps":int(len(sources)),
        "timestep_h":float(dt_h),
    }


def new_overloads_per_hour(branches:pd.DataFrame,reference:pd.DataFrame,threshold_pct:float=100)->pd.DataFrame:
    """Proíbe subtrair contagens; compara identidades de linhas/fases por horário."""
    required={"hour","line","phase","loading_pct"}
    if not required <= set(branches.columns) or not required <= set(reference.columns):
        raise ValueError("Colunas de correntes incompletas")
    rows=[]
    for h in sorted(set(branches.hour.unique()) & set(reference.hour.unique())):
        base=reference.loc[(reference.hour==h)&(reference.loading_pct>threshold_pct)]
        target=branches.loc[(branches.hour==h)&(branches.loading_pct>threshold_pct)]
        b={(str(r.line),str(r.phase)) for r in base.itertuples()}
        for r in target.itertuples():
            if (str(r.line),str(r.phase)) not in b:
                rows.append(dict(hour=int(h),line=str(r.line),phase=str(r.phase),loading_pct=float(r.loading_pct)))
    return pd.DataFrame(rows,columns=["hour","line","phase","loading_pct"])
