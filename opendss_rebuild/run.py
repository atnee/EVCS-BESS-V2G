#!/usr/bin/env python3
"""IEEE 8500 full OpenDSS: auditoria e execução S0–S4.

Exemplos (na raiz do projeto EVCS-BESS-V2G):
    python opendss_rebuild/run.py audit
    python opendss_rebuild/run.py run --scenarios S0 --input none --hours 18
    python opendss_rebuild/run.py run --scenarios S0 S1 S4 --input modules
    python opendss_rebuild/run.py run --scenarios S0 S1 S4 --input exports --legacy-dados results/ieee8500/integration/dados
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import sys
import traceback
import pandas as pd
from rebuild.contracts import (SCENARIOS,repo_files,read_yaml,load_context,validate_team_config,sha256)
from rebuild.profiles import from_saved_exports,generate_module_profiles
from rebuild.report import build_summary

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent


def serialize(path:Path,obj:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding="utf-8")


def audit(ctx):
    teams=validate_team_config(ctx)
    data=ctx["network"]
    proof={
        "python":platform.python_version(),"status":"CONFIGURATION_AUDIT_ONLY_NO_POWER_FLOW",
        "source_master":str(ctx["files"]["master"]),
        "source_reference_pu":1.05,
        "mode":ctx["settings"]["simulation"]["source_mode"],
        "controls":ctx["settings"]["simulation"]["controls"],
        "ampacity_definition":ctx["settings"]["simulation"]["ampacity"],
        "n_primary_buses":len(data["buses"]),
        "n_primary_line_elements":len(data["lines"]),
        "n_primary_physical_bus_phases":len(ctx["physical_voltage_nodes"]),
        "n_primary_physical_line_phases":sum(len(p) for p in ctx["line_phases"].values()),
        "n_original_loads_in_json":len(data["loads"]),
        "n_regulators_in_json":len(data["regulators"]),
        "n_capacitors_in_json":len(data["capacitors"]),
        "teams":teams,
        "synthetic_load_multipliers":ctx["multipliers"].tolist(),
        "file_hashes_sha256":{str(f.relative_to(ctx['repo'])):sha256(f) for f in ctx["files"].values()},
        "settings_sha256":sha256(ctx["folder"]/"settings.yaml"),
        "scientific_caveat":"Nenhuma saída aqui constitui validação independente ou resultado revisado do artigo."
    }
    print(json.dumps(proof,indent=2,ensure_ascii=False))
    return proof


def empty_profile():
    return pd.DataFrame(columns=["hour","bus","phase","p_kw","q_kvar"])


def run(ctx,args):
    from rebuild.engine import ODSSEngine
    wanted=args.scenarios
    hours=sorted(set(args.hours if args.hours is not None else range(24)))
    if not hours or any(h not in range(24) for h in hours):
        raise ValueError("Horas devem estar entre 0 e 23")
    if args.input=="none" and wanted!=["S0"]:
        raise ValueError("--input none só é permitido com --scenarios S0")
    output=Path(args.output)
    if not output.is_absolute():output=ctx["folder"]/output
    output=output.resolve()
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        raise FileExistsError(f"Saída não vazia: {output}. Use --output NOVA_PASTA ou --overwrite")
    output.mkdir(parents=True,exist_ok=True)
    serialize(output/"auditoria_config.json",audit(ctx))
    generated=generate_module_profiles(ctx,hours,output,wanted) if args.input=="modules" else {}
    profiles={};meta={}
    for sc in wanted:
        if args.input=="modules":
            profiles[sc],meta[sc]=generated[sc]
        elif args.input=="exports":
            if sc=="S0":
                # S0 não possui injeção externa e prescinde de CSV.
                profiles[sc],meta[sc]=empty_profile(),{"source":"original_ieee_only"}
            else:
                base=Path(args.legacy_dados).resolve()
                profiles[sc],meta[sc]=from_saved_exports(ctx,base,sc,hours)
                meta[sc]["legacy_bess_surrogate"]=sc in ("S2","S4")
        else:
            profiles[sc],meta[sc]=empty_profile(),{"source":"original_ieee_only"}
    observed={}
    for sc in wanted:
        print(f"[OpenDSS] {sc}, horas {hours}, fonte={meta[sc]['source']}",flush=True)
        engine=ODSSEngine(ctx,ctx["settings"]["simulation"]["source_mode"],
                             ctx["settings"]["simulation"]["ampacity"])
        engine.compile()
        engine.define_assets(profiles[sc])
        frames={key:[] for key in ("source","voltages","branches","regulators","capacitors")}
        for hour in hours:
            v=engine.solve_hour(hour,float(ctx["multipliers"][hour]),profiles[sc])
            for key in frames:frames[key].extend(v[key])
            print(f"  h={hour:02d} import={v['source'][0]['p_kw']:.1f} kW "
                  f"Vmin={min(x['v_pu'] for x in v['voltages']):.5f} ",flush=True)
        dfs={key:pd.DataFrame(rows) for key,rows in frames.items()}
        if any(x.empty for x in (dfs["source"],dfs["voltages"],dfs["branches"])):
            raise RuntimeError("Dados de energia/voltagem/corrente vazios")
        out=output/sc
        out.mkdir(exist_ok=True,parents=True)
        for key,df in dfs.items():df.to_csv(out/(key+".csv"),index=False)
        profiles[sc].to_csv(out/"injections_replayed.csv",index=False)
        observed[sc]=dfs
    summary,extras=build_summary(observed,ctx,meta,dt_h=1.0)
    summary.to_csv(output/"summary.csv",index=False)
    for name,table in extras.items():table.to_csv(output/(name+".csv"),index=False)
    try:
        from opendssdirect import dss
        engine_version=str(dss.Basic.Version())
    except Exception:
        engine_version="unavailable"
    serialize(output/"manifest.json",{
        "status":"POWER_FLOW_COMPUTED_NOT_YET_EXTERNALLY_VALIDATED",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "backend":"opendssdirect.py / DSS C-API", "backend_version":engine_version,
        "scenarios":wanted,"hours":hours,"input_mode":args.input,
        "model":"FULL ORIGINAL IEEE8500 SECONDARY NETWORK",
        "profile_provenance":meta,
        "legacy_surrogate_warning":"BESS line_limit coefficients from pandapower MUST be recalibrated with OpenDSS",
        "reproducibility":{
            "master_sha256":sha256(ctx["files"]["master"]),
            "ieee8500_json_sha256":sha256(ctx["files"]["network"]),
            "settings_sha256":sha256(ctx["folder"]/"settings.yaml")},
        "limitations":[
            "Estudo com curva de carga SINTÉTICA, sem medição de campo de Honduras.",
            "Ativos DER conectados diretamente ao barramento MT: transformadores dedicados novos ainda não modelados.",
            "Kenia line_limit ainda usa surrogate calibrado em pandapower; este estágio é replay, não nova otimização.",
            "Amostras de sobretensão/subtensão são contadas apenas nas fases físicas da MT do JSON.",
            "A contagem de ampacidade 'study' usa hipóteses do artigo, não necessariamente o padrão do DSS.",
            "A execução por horas selecionadas é diagnóstica e não substitui a sequência cronológica de 24h."
        ]})
    print("\nResumo de resultados:\n",summary.to_string(index=False),"\nArquivos:",output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task",choices=["audit","run"])
    parser.add_argument("--repo",type=Path,default=ROOT)
    parser.add_argument("--output",type=Path,default=Path("results/replay_01"))
    parser.add_argument("--input",choices=["none","modules","exports"],default="modules")
    parser.add_argument("--legacy-dados",type=Path,default=ROOT/"results/ieee8500/integration/dados")
    parser.add_argument("--scenarios",nargs="+",choices=list(SCENARIOS),default=list(SCENARIOS))
    parser.add_argument("--hours",nargs="+",type=int,default=None)
    parser.add_argument("--overwrite",action="store_true")
    args=parser.parse_args()
    ctx=load_context(args.repo.resolve(),HERE)
    if args.task=="audit":
        audit(ctx)
    else:
        try:
            run(ctx,args)
        except Exception as exc:
            out=Path(args.output)
            if not out.is_absolute():out=HERE/out
            serialize(out/"failure.json",{
                "type":type(exc).__name__,"error":str(exc),
                "traceback":traceback.format_exc(),
                "status":"FAILED_NO_VALIDATED_RESULTS"})
            raise

if __name__=="__main__":main()
