"""Integra perfis de Kenia, Cesar e eletropostos sem reutilizar o solver pandapower."""
from __future__ import annotations
import sys
import re
from pathlib import Path
import numpy as np
import pandas as pd
from .contracts import SCENARIOS,canonical_profile


def from_saved_exports(ctx:dict, exported:Path, scenario:str, hours:list[int]) -> tuple[pd.DataFrame,dict]:
    path=exported/f"{scenario}_injections.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Exportação faltando: {path}; rode --input modules ou forneça --legacy-dados")
    raw=pd.read_csv(path)
    return canonical_profile(raw,hours,ctx["buses"]),{"source":"legacy_exports", "file":str(path)}


def original_load_components(ctx:dict) -> dict:
    """Reads actual IEEE Loads.dss status flags, which were dropped by the MV JSON.

    `status=fixed` original loads are NOT multiplied by OpenDSS LoadMult.
    `status=variable` loads are multiplied. This distinction is important when
    constructing the BESS module's demand_before vector and auditing daily kWh.
    """
    path=Path(ctx["files"]["master"]).parent/"Loads.dss"
    if not path.is_file():
        raise FileNotFoundError(f"IEEE original Loads.dss not found: {path}")
    fixed,variable=0.0,0.0
    n_fixed,n_variable=0,0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line=raw.split("!",1)[0].strip()
        if not re.match(r"^New\s+Load\.",line,re.IGNORECASE):
            continue
        kw=re.search(r"\bkW\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",line,re.IGNORECASE)
        status=re.search(r"\bstatus\s*=\s*([a-zA-Z]+)",line,re.IGNORECASE)
        if not kw:
            raise ValueError("Original IEEE load without explicit kW: "+line[:120])
        val=float(kw.group(1)); mode=status.group(1).lower() if status else "variable"
        if mode=="fixed": fixed+=val; n_fixed+=1
        elif mode=="variable": variable+=val; n_variable+=1
        else: raise ValueError(f"Unexpected OpenDSS status={mode!r}: {line[:120]}")
    if n_fixed+n_variable < 1000:
        raise ValueError("Too few original IEEE loads parsed; check source Loads.dss")
    json_nominal=sum(float(l["p_kw"]) for l in ctx["network"]["loads"])
    if abs(json_nominal-(fixed+variable)) > .01:
        raise ValueError(f"Load totals differ: original={fixed+variable}, JSON={json_nominal} kW")
    return {"fixed_kw":fixed,"variable_kw":variable,"fixed_count":n_fixed,"variable_count":n_variable}


def generate_module_profiles(ctx:dict,hours:list[int], output:Path|None=None, scenarios=None) -> dict:
    """Preserva os módulos oficiais; a calibração do BESS ainda é a legada."""
    repo=ctx["repo"]
    sys.path.insert(0,str(repo/"src"))
    from integration.scenarios import integrated_grid
    from evcs.runner import run_integrated as run_evcs
    from bess.runner import run_integrated as run_bess
    from v2g.runner import run_integrated as run_v2g
    grid=integrated_grid(str(repo/"configs"))
    if grid.steps!=24 or float(grid.dt_h)!=1.0:
        raise ValueError("Este primeiro módulo requer 24 passos horários. Não resample automaticamente.")
    wanted=list(SCENARIOS) if scenarios is None else list(scenarios)
    unknown=set(wanted)-set(SCENARIOS)
    if unknown:
        raise ValueError(f"Unknown scenarios: {sorted(unknown)}")
    # Avoid running the legacy Kenia optimizer (S2/S4) when auditing S0/S1 only.
    ev=run_evcs(str(repo/"configs"),grid) if any(SCENARIOS[sc][0] for sc in wanted) else None
    load_parts=original_load_components(ctx) if any(SCENARIOS[sc][1] for sc in wanted) else None
    result={}
    for scen in wanted:
        use_ev,use_bess,use_v2g=SCENARIOS[scen]
        modules={}; components=[]
        if use_ev:
            modules["evcs"] = ev
            modules["v2g"] = run_v2g(str(repo/"configs"),grid,ev[0],enable_v2g=use_v2g)
            components.extend([modules["evcs"][0].data,modules["v2g"][0].data])
        if use_bess:
            # Mesma fronteira de demanda de integração original. NÃO é despacho
            # recalibrado no OpenDSS: line_limit inclui coeficientes pandapower.
            others=np.zeros(24,dtype=float)
            for profile_data in components:
                others+=profile_data.groupby("time").p_kw.sum().reindex(grid.index,fill_value=0).to_numpy(float)
            # Original IEEE 8500 includes 24 status=fixed loads (~915.8 kW).
            # These are not multiplied by LoadMult; the old nominal*mult formula
            # underestimates native demand at off-peak hours.
            demand=(load_parts["fixed_kw"]+
                    load_parts["variable_kw"]*ctx["multipliers"])-others
            modules["bess"] = run_bess(str(repo/"configs"),grid,demand)
            rejected=np.asarray(modules["bess"][0].metadata.get("rejected_kw",[]),dtype=float)
            if rejected.size and np.any(np.abs(rejected)>1e-5):
                raise ValueError("Comandos BESS rejeitados por limites de SOC/potência: revisar Kenia")
            components.append(modules["bess"][0].data)
        if components:
            raw=pd.concat(components,ignore_index=True).groupby(["time","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
        else:
            # S0: perfil só para manter horizonte; não injeta potência no slack.
            raw=pd.DataFrame({"time":grid.index,"bus":[next(iter(ctx["buses"]))]*24,
                              "phase":[next(iter(ctx["buses"][next(iter(ctx["buses"]))]))]*24,
                              "p_kw":np.zeros(24),"q_kvar":np.zeros(24)})
        # Não criar elementos elétricos para linha fictícia de S0 (zero)
        normalized=canonical_profile(raw,hours,ctx["buses"])
        kpis={}
        for name,(p,tables) in modules.items():
            if output is not None:
                mod_dir=output/"modules"/scen/name
                mod_dir.mkdir(parents=True,exist_ok=True)
                p.data.to_csv(mod_dir/"profile.csv",index=False)
                # KPIs originais, sem inventar valores em comparação com o AC.
                (mod_dir/"kpis.json").write_text(__import__("json").dumps(
                    p.metadata.get("kpis",{}),ensure_ascii=False,indent=2,default=str),encoding="utf-8")
                for table_name, table_data in tables.items():
                    if isinstance(table_data,pd.DataFrame):
                        table_data.to_csv(mod_dir/(table_name+".csv"),index=False)
            for key,value in p.metadata.get("kpis",{}).items():
                kpis[key]=kpis.get(key,0.0)+float(value)
        result[scen]=(normalized,{"source":"module_replay","legacy_bess_surrogate":bool(use_bess),"kpis":kpis})
    return result
