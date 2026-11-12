#!/usr/bin/env python3
r"""Reoptimization BESS–V2G with OpenDSS sensitivities (IEEE 8500), 24h.

Run from the ROOT of EVCS-BESS-V2G:
 python .\opendss_rebuild\reotimizar_bess_v2g.py --results .\results\replay_s0_s4_opendss \
  --output .\results\reotimizacao_opendss_v08

This is a RESEARCH prototype: local finite-difference AC sensitivities with
frozen controls -> temporal mixed-integer linear optimizer -> full OpenDSS
chronological replay with automatic controls, then honest residual reporting.
No submission claim can be made until the independent AC verification passes.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import traceback
import numpy as np
import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
from rebuild.contracts import load_context,read_yaml,sha256
from rebuild.joint_optimization import solve_joint, OptSettings


def save_json(path,data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")


def read_existing(results):
    for sc in ("S0","S1"):
        for name in ("voltages","branches","source"):
            if not (results/sc/f"{name}.csv").is_file():
                raise FileNotFoundError(f"Faltando {results/sc/(name+'.csv')}; execute replay S0/S1 primeiro")
    if not (results/"S1/injections_replayed.csv").is_file():
        raise FileNotFoundError("Falta S1/injections_replayed.csv")
    if not (results/"modules/S1/v2g/vehicle_power.csv").is_file():
        raise FileNotFoundError("Faltam os dados de Cesar: modules/S1/v2g/vehicle_power.csv")
    if not (results/"modules/S1/v2g/profile.csv").is_file():
        raise FileNotFoundError("Falta o perfil S1 da frota: modules/S1/v2g/profile.csv")
    d={}
    for sc in ("S0","S1"):
        d[sc]={name:pd.read_csv(results/sc/f"{name}.csv") for name in ("voltages","branches","source")}
    s1=pd.read_csv(results/"S1/injections_replayed.csv")
    cols={"hour","bus","phase","p_kw","q_kvar"}
    if not cols<=set(s1.columns) or sorted(s1.hour.unique().tolist())!=list(range(24)):
        raise ValueError("S1/injections_replayed.csv deve cobrir 24 horas")
    for name in ("bus","phase"):
        s1[name]=s1[name].astype(str).str.lower() if name=="bus" else s1[name].astype(str).str.upper()
    s1=s1.groupby(["hour","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
    return d,s1


def read_team(repo,results):
    sys.path.insert(0,str(repo/"src"))
    from core.schemas import TimeGrid
    from core.assets import Battery,Station
    from v2g.runner import vehicles_from_config
    ev=read_yaml(repo/"configs/evcs.yaml")
    v2g=read_yaml(repo/"configs/v2g.yaml")
    bess=read_yaml(repo/"configs/bess.yaml")
    grid=TimeGrid(**v2g["time"])
    if grid.steps!=24 or grid.dt_h!=1.:
        raise ValueError("Esta reotimização exige o horizonte integrado original de 24 h")
    vehicles=vehicles_from_config(v2g)
    station=Station(**ev["station"])
    battery=Battery(**bess["battery"])
    if len(vehicles)!=150 or station.bus.lower()!=v2g["standalone"]["station"]["bus"].lower():
        raise ValueError("Frota e estação não coincidem com as decisões oficiais da equipe")
    vp=pd.read_csv(results/"modules/S1/v2g/vehicle_power.csv")
    if not {"time","vehicle_id","p_kw"}<=set(vp.columns):
        raise ValueError("Saída de Cesar não possui identificação individual por hora")
    vp["time"]=pd.to_datetime(vp.time,utc=True)
    times=sorted(vp.time.unique())
    if len(times)!=24:raise ValueError("Dados V2G S1 não contêm 24 horas")
    tmap={pd.Timestamp(t):i for i,t in enumerate(times)}
    vp["hour"]=vp.time.map(tmap)
    piv=vp.pivot(index="vehicle_id",columns="hour",values="p_kw")
    if set(piv.index)!=set(v.id for v in vehicles) or not set(piv.columns)==set(range(24)):
        raise ValueError("A frota presente nos resultados não corresponde à frota recalculada da configuração")
    matrix=piv.loc[[v.id for v in vehicles],list(range(24))].to_numpy(float)
    mod=pd.read_csv(results/"modules/S1/v2g/profile.csv")
    mod["time"]=pd.to_datetime(mod.time,utc=True)
    mod["hour"]=mod.time.map(tmap)
    mod["bus"]=mod.bus.astype(str).str.lower()
    mod["phase"]=mod.phase.astype(str).str.upper()
    mod=mod.groupby(["hour","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
    fleet_hours=mod.groupby("hour").p_kw.sum().reindex(range(24),fill_value=0).to_numpy(float)
    if not np.allclose(fleet_hours,matrix.sum(axis=0),atol=0.001):
        raise ValueError("O perfil S1 do V2G diverge dos 150 perfis individuais")
    ind=np.asarray(ev["independent_demand_kw"],float)
    tariff=np.asarray(v2g["cesar_v1"]["tariff_usd_kwh"],float)
    rem=np.asarray(v2g["cesar_v1"]["v2g_remuneration_usd_kwh"],float)
    return dict(grid=grid,vehicles=vehicles,station=station,battery=battery,
                bess_bus=str(bess["connection"]["bus"]).lower(),v2g_bus=str(station.bus).lower(),
                old_fleet=matrix,old_fleet_net=fleet_hours,old_fleet_profile=mod,
                ind=ind,tariff=tariff,remuneration=rem,
                discharge_window=tuple(v2g["discharge_window_h"]),
                no_charge_window=tuple(v2g["cesar_v1"]["no_charge_window_h"]),
                configs={"bess":bess,"v2g":v2g,"evcs":ev})


def with_delta(base_hour,bus,delta_kw):
    """Change signed MV injection, preserving other EVCS rows and Q."""
    h=int(base_hour.hour.iloc[0])
    rows=base_hour.copy()
    for ph in "ABC":
        loc=(rows.bus==bus)&(rows.phase==ph)
        if loc.any():
            rows.loc[loc,"p_kw"]+=float(delta_kw)/3.
        else:
            rows=pd.concat([rows,pd.DataFrame([dict(hour=h,bus=bus,phase=ph,p_kw=delta_kw/3.,q_kvar=0.)])],ignore_index=True)
    return rows


def evaluate_s1_replay(baseline,actual):
    maximum={}
    for name,keys,col in [("voltages",["hour","bus","phase"],"v_pu"),
                          ("branches",["hour","line","phase"],"loading_pct"),
                          ("source",["hour"],"p_kw")]:
        left=baseline[name][keys+[col]].copy()
        right=actual[name][keys+[col]].copy()
        j=left.merge(right,on=keys,how="outer",validate="one_to_one",suffixes=("_saved","_rerun"),indicator=True)
        if (j._merge!="both").any():
            raise RuntimeError(f"S1: conjunto de {name} diferente na reprodução")
        maximum[name]=float(np.max(np.abs(j[f"{col}_saved"]-j[f"{col}_rerun"])))
    # Reproduce the actual 24-h historical baseline before using sensitivities.
    if maximum["voltages"]>1e-5 or maximum["branches"]>0.02 or maximum["source"]>2.:
        raise RuntimeError(f"A reprodução S1 mudou: {maximum}. Não calibrar com dados incompatíveis")
    return maximum


def calibrate(ctx,s1_profile,replay_s1,team,output,delta_kw):
    """Compute finite-difference sensitivities at all 24 baseline hours, controls fixed ONLY during perturbations."""
    from rebuild.engine import ODSSEngine,error_checked
    engine=ODSSEngine(ctx,ctx["settings"]["simulation"]["source_mode"],ctx["settings"]["simulation"]["ampacity"])
    if engine.controls!="auto":
        raise ValueError("O estudo exige reguladores/capacitores automaticos na simulação completa")
    engine.compile()
    # Dummy hour 99 is used only to size generator.kVA for the maximum possible
    # BESS and V2G injection; it will NEVER be applied to the circuit.
    probe=s1_profile.copy()
    for bus,capacity in ((team["bess_bus"],team["battery"].power_kw),
                         (team["v2g_bus"],team["station"].connection_kw)):
        probe=pd.concat([probe,pd.DataFrame([
            dict(hour=99,bus=bus,phase=ph,p_kw=capacity/3.,q_kvar=0.) for ph in "ABC"
        ])],ignore_index=True)
    engine.define_assets(probe)
    # key order is stable, deterministic; at least 24 baseline solves + 48 perturbations.
    baseline={name:[] for name in ("source","voltages","branches","regulators","capacitors")}
    sensitive_l=[];sensitive_v=[];sensitive_p=[]
    dss=engine.dss
    for h in range(24):
        base_rows=s1_profile.loc[s1_profile.hour==h]
        base=engine.solve_hour(h,float(ctx["multipliers"][h]),s1_profile)
        for name in baseline:baseline[name].extend(base[name])
        bline={(r["line"],r["phase"]):r["loading_pct"] for r in base["branches"]}
        bvolt={(r["bus"],r["phase"]):r["v_pu"] for r in base["voltages"]}
        bsrc=base["source"][0]["p_kw"]
        breg={(r["regulator"],r["tap"]) for r in base["regulators"]}
        bcap={(r["capacitor"],r["states"]) for r in base["capacitors"]}
        error_checked(dss,"Set controlmode=off")
        pert={}
        try:
            for label,bus in (("bess",team["bess_bus"]),("v2g",team["v2g_bus"])):
                engine.update_assets(with_delta(base_rows,bus,delta_kw))
                error_checked(dss,"Solve")
                if not dss.Solution.Converged():
                    raise RuntimeError(f"Calibração {label} não convergiu, h={h}")
                reading=engine.read_results(h)
                regs={(r["regulator"],r["tap"]) for r in reading["regulators"]}
                caps={(r["capacitor"],r["states"]) for r in reading["capacitors"]}
                if regs!=breg or caps!=bcap:
                    raise RuntimeError(f"Controles mudaram durante calibração fixa, h={h}, recurso={label}")
                pert[label]=reading
                engine.update_assets(base_rows)
                error_checked(dss,"Solve")
                if not dss.Solution.Converged():
                    raise RuntimeError(f"A volta ao S1 falhou, h={h}")
            # Collect ALL physical phases (monitors are selected only in MILP).
            line_pert={label:{(r["line"],r["phase"]):r["loading_pct"] for r in a["branches"]}
                       for label,a in pert.items()}
            volt_pert={label:{(r["bus"],r["phase"]):r["v_pu"] for r in a["voltages"]}
                       for label,a in pert.items()}
            if any(set(v)!=set(bline) for v in line_pert.values()):
                raise RuntimeError(f"Conjunto de linhas mudou ao perturbar h={h}")
            if any(set(v)!=set(bvolt) for v in volt_pert.values()):
                raise RuntimeError(f"Conjunto de barras mudou ao perturbar h={h}")
            for (line,phase),val in bline.items():
                sensitive_l.append(dict(hour=h,line=line,phase=phase,dl_bess=(line_pert["bess"][(line,phase)]-val)/delta_kw,
                                        dl_v2g=(line_pert["v2g"][(line,phase)]-val)/delta_kw))
            for (bus,phase),val in bvolt.items():
                sensitive_v.append(dict(hour=h,bus=bus,phase=phase,dv_bess=(volt_pert["bess"][(bus,phase)]-val)/delta_kw,
                                        dv_v2g=(volt_pert["v2g"][(bus,phase)]-val)/delta_kw))
            sensitive_p.append(dict(hour=h,dp_bess=(pert["bess"]["source"][0]["p_kw"]-bsrc)/delta_kw,
                                    dp_v2g=(pert["v2g"]["source"][0]["p_kw"]-bsrc)/delta_kw))
        finally:
            engine.update_assets(base_rows)
            error_checked(dss,"Set controlmode=static")
        print(f"[sens] h={h:02d} -- {len(bline)} fases de linha, {len(bvolt)} tensões",flush=True)
    dfs={name:pd.DataFrame(rows) for name,rows in baseline.items()}
    match=evaluate_s1_replay(replay_s1,dfs)
    sdir=output/"sensibilidades"
    sdir.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(sensitive_l).to_csv(sdir/"linhas.csv",index=False)
    pd.DataFrame(sensitive_v).to_csv(sdir/"tensoes.csv",index=False)
    pd.DataFrame(sensitive_p).to_csv(sdir/"fonte.csv",index=False)
    save_json(sdir/"calibracao.json",{"status":"S1_REPLAY_MATCHED","delta_kw":delta_kw,
                                     "baseline_error":match,
                                     "controls":"automatic S1, frozen during each sensitivity measurement",
                                     "n_hours":24,"created_utc":datetime.now(timezone.utc).isoformat()})
    return pd.DataFrame(sensitive_l),pd.DataFrame(sensitive_v),pd.DataFrame(sensitive_p),match


def build_scenario_injections(s1,original_fleet,team,solution):
    """REMOVE old S1 fleet charging, add optimized fleet, add optimized BESS. No double counting."""
    old=original_fleet.groupby(["hour","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
    base=s1.merge(old,on=["hour","bus","phase"],how="outer",suffixes=("","_fleet"))
    for col in ("p_kw","q_kvar","p_kw_fleet","q_kvar_fleet"):
        base[col]=base[col].fillna(0.)
    base["p_kw"]-=base["p_kw_fleet"]
    base["q_kvar"]-=base["q_kvar_fleet"]
    base=base[["hour","bus","phase","p_kw","q_kvar"]]
    add=[]
    for h in range(24):
        for bus,value in ((team["v2g_bus"],solution["v2g_net_kw"][h]),
                          (team["bess_bus"],solution["bess_net_kw"][h])):
            for ph in "ABC":
                add.append(dict(hour=h,bus=bus,phase=ph,p_kw=float(value)/3.,q_kvar=0.))
    all_rows=pd.concat([base,pd.DataFrame(add)],ignore_index=True)
    grouped=all_rows.groupby(["hour","bus","phase"],as_index=False)[["p_kw","q_kvar"]].sum()
    expected=float(s1.p_kw.sum()-original_fleet.p_kw.sum()+
                   solution["v2g_net_kw"].sum()+solution["bess_net_kw"].sum())
    if abs(grouped.p_kw.sum()-expected)>1e-4:
        raise RuntimeError("Erro de substituição da frota do S1: dupla contagem")
    return grouped


def simulate_solution(ctx,profiles,team,s1,output):
    from rebuild.engine import ODSSEngine
    engine=ODSSEngine(ctx,ctx["settings"]["simulation"]["source_mode"],ctx["settings"]["simulation"]["ampacity"])
    engine.compile()
    engine.define_assets(profiles)
    frames={name:[] for name in ("source","voltages","branches","regulators","capacitors")}
    for h in range(24):
        result=engine.solve_hour(h,float(ctx["multipliers"][h]),profiles)
        for name in frames:frames[name].extend(result[name])
        print(f"[OpenDSS auto] h={h:02d} import={result['source'][0]['p_kw']:.2f} kW ",
              f"Vmin={min(r['v_pu'] for r in result['voltages']):.5f}",flush=True)
    dfs={name:pd.DataFrame(rows) for name,rows in frames.items()}
    output.mkdir(exist_ok=True,parents=True)
    for name,data in dfs.items():data.to_csv(output/f"{name}.csv",index=False)
    profiles.to_csv(output/"injections_reoptimized.csv",index=False)
    return dfs


def score(s0,s1,s4):
    from rebuild.contracts import energy_metrics
    summary={}
    for name,dfs in (("S0",s0),("S1",s1),("S4_reoptimized",s4)):
        metrics=energy_metrics(dfs["source"],dfs["voltages"],dfs["branches"],1.)
        br=dfs["branches"];v=dfs["voltages"]
        metrics["thermal_severity_pp_hour"]=float(np.maximum(br.loading_pct.to_numpy()-100,0).sum())
        metrics["undervoltage_severity_pu_hour"]=float(np.maximum(.95-v.v_pu.to_numpy(),0).sum())
        metrics["overvoltage_severity_pu_hour"]=float(np.maximum(v.v_pu.to_numpy()-1.05,0).sum())
        summary[name]=metrics
    s0_lines=set(s0["branches"].loc[s0["branches"].loading_pct>100,"line"])
    for name,dfs in (("S1",s1),("S4_reoptimized",s4)):
        lines=set(dfs["branches"].loc[dfs["branches"].loading_pct>100,"line"])
        summary[name]["new_distinct_overloaded_lines_vs_S0"]=len(lines-s0_lines)
        summary[name]["new_overload_line_ids_vs_S0"]=sorted(lines-s0_lines)
    # Compare each hour+line+phase to S0 baseline with no aggravation tolerance.
    key=["hour","line","phase"]
    br=s4["branches"].merge(s0["branches"][key+["loading_pct"]],on=key,
                             suffixes=("_S4","_S0"),validate="one_to_one")
    br["excess_vs_s0_pp"]=br.loading_pct_S4-np.maximum(100.,br.loading_pct_S0)
    vv=s4["voltages"].merge(s0["voltages"][["hour","bus","phase","v_pu"]],
                              on=["hour","bus","phase"],suffixes=("_S4","_S0"),validate="one_to_one")
    vv["extra_under_pu"]=np.maximum(np.minimum(.95,vv.v_pu_S0)-vv.v_pu_S4,0)
    vv["extra_over_pu"]=np.maximum(vv.v_pu_S4-np.maximum(1.05,vv.v_pu_S0),0)
    thermal_new=br[br.excess_vs_s0_pp>0.05]
    volt_new=vv[(vv.extra_under_pu>0.0005)|(vv.extra_over_pu>0.0005)]
    verified={"state":"AC_REPLAY_PERFORMED_NOT_FEASIBLE_IF_VIOLATIONS",
              "new_distinct_lines_vs_s0":summary["S4_reoptimized"]["new_distinct_overloaded_lines_vs_S0"],
              "thermal_samples_aggravated_beyond_s0":int(len(thermal_new)),
              "voltage_samples_aggravated_beyond_s0":int(len(volt_new)),
              "S4_completely_satisfies_no_aggravation":bool(len(thermal_new)==0 and len(volt_new)==0),
              "warning":"No full feeder voltage/thermal feasibility is implied if flag is false. IEEE source 1.05 pu and assumed ampacities."}
    return summary,verified,thermal_new,volt_new


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--repo",type=Path,default=ROOT)
    ap.add_argument("--results",required=True,type=Path,help="Existing replay directory containing S0, S1 and modules/S1/v2g")
    ap.add_argument("--output",required=True,type=Path,help="NEW, empty directory for OpenDSS calibration + results")
    ap.add_argument("--task",choices=["all","calibrate","optimize"],default="all")
    ap.add_argument("--mode",choices=["S2","S3","S4"],default="S4")
    ap.add_argument("--delta-kw",type=float,default=200.)
    ap.add_argument("--thermal-screen",type=float,default=80.)
    ap.add_argument("--voltage-screen-margin",type=float,default=0.005)
    ap.add_argument("--thermal-weight",type=float,default=40.)
    ap.add_argument("--voltage-weight",type=float,default=5000.)
    ap.add_argument("--peak-weight",type=float,default=0.5)
    ap.add_argument("--time-limit",type=float,default=300.)
    ap.add_argument("--mip-gap",type=float,default=0.02)
    ap.add_argument("--overwrite-mode",action="store_true",help="Allow replacing existing optimized outputs of THIS mode only")
    args=ap.parse_args()
    root=args.repo.resolve(); results=args.results.resolve();output=args.output.resolve()
    if not root.exists() or not results.exists():raise FileNotFoundError("Projeto ou replay inexistente")
    if output==results or root==output or results in output.parents:
        raise ValueError("Escolha saída independente, fora do diretório do replay usado como entrada")
    if not 10<=args.delta_kw<=250 or args.thermal_screen<1 or not 0<=args.voltage_screen_margin<=.03:
        raise ValueError("Perturbação deve ser 10..250 kW; revise também limiares de screening")
    if args.time_limit<=0:raise ValueError("Tempo limite precisa ser positivo")
    if args.task in ("all","calibrate") and output.exists() and any(output.iterdir()):
        raise FileExistsError("Saída já existe; escolha pasta nova ou use --task optimize com calibração existente")
    output.mkdir(parents=True,exist_ok=True)
    try:
        ctx=load_context(root,HERE)
        s,s1_profile=read_existing(results)
        team=read_team(root,results)
        s0=s["S0"];s1=s["S1"]
        if args.task in ("all","calibrate"):
            dl,dv,ds,replayerr=calibrate(ctx,s1_profile,s1,team,output,args.delta_kw)
        else:
            sfolder=output/"sensibilidades"
            if not (sfolder/"calibracao.json").is_file():
                raise FileNotFoundError("Execute --task calibrate primeiro nesta saída")
            cal=json.loads((sfolder/"calibracao.json").read_text(encoding="utf-8"))
            if cal.get("status")!="S1_REPLAY_MATCHED":raise RuntimeError("Calibração não aprovada")
            dl=pd.read_csv(sfolder/"linhas.csv");dv=pd.read_csv(sfolder/"tensoes.csv");ds=pd.read_csv(sfolder/"fonte.csv")
            replayerr=cal["baseline_error"]
        if args.task=="calibrate":
            print("Calibração OpenDSS concluída. Use --task optimize com a MESMA pasta de saída.")
            return
        settings=OptSettings(thermal_screen_pct=args.thermal_screen,
                             voltage_screen_margin_pu=args.voltage_screen_margin,
                             thermal_weight=args.thermal_weight,
                             undervoltage_weight=args.voltage_weight,overvoltage_weight=args.voltage_weight,
                             peak_weight=args.peak_weight,
                             bess_throughput_usd_kwh=float(team["battery"].degradation_per_kwh),
                             vehicle_degradation_usd_kwh=float(team["configs"]["v2g"]["cesar_v1"].get("degradation_usd_kwh",0.)),
                             solver_time_limit_s=args.time_limit,mip_rel_gap=args.mip_gap)
        solution=solve_joint(vehicles=team["vehicles"],grid=team["grid"],station=team["station"],
                             battery=team["battery"],bess_bus=team["bess_bus"],v2g_bus=team["v2g_bus"],
                             source_s1=s1["source"],lines_s1=s1["branches"],volt_s1=s1["voltages"],
                             lines_s0=s0["branches"],volt_s0=s0["voltages"],
                             ds_lines=dl,ds_volt=dv,ds_source=ds,
                             fleet_s1_kw=team["old_fleet_net"],fleet_s1_per_vehicle_kw=team["old_fleet"],independent_evcs_kw=team["ind"],
                             discharge_window=team["discharge_window"],no_charge_window=team["no_charge_window"],
                             tariffs=team["tariff"],remuneration=team["remuneration"],
                             settings=settings,mode=args.mode)
        out=output/args.mode
        if out.exists() and any(out.iterdir()) and not args.overwrite_mode:
            raise FileExistsError(f"Resultado {args.mode} ja existe: {out}. Mude a saida ou use --overwrite-mode")
        out.mkdir(exist_ok=True)
        pd.DataFrame({"hour":np.arange(24),"bess_charge_kw":solution["bess_charge_kw"],
                      "bess_discharge_kw":solution["bess_discharge_kw"],
                      "bess_net_injection_kw":solution["bess_net_kw"],
                      "v2g_fleet_net_injection_kw":solution["v2g_net_kw"],
                      "v2g_fleet_S1_reference_kw":team["old_fleet_net"],
                      "v2g_change_from_S1_kw":solution["v2g_net_kw"]-team["old_fleet_net"]}).to_csv(out/"dispatch_24h.csv",index=False)
        pd.DataFrame({"boundary":range(25),"energy_kwh":solution["bess_energy_kwh"],
                      "soc":solution["bess_energy_kwh"]/team["battery"].capacity_kwh}).to_csv(out/"bess_soc.csv",index=False)
        vt=pd.DataFrame(solution["vehicle_power_kw"].T,columns=[v.id for v in team["vehicles"]]);vt.insert(0,"hour",range(24))
        vt.melt(id_vars="hour",var_name="vehicle_id",value_name="p_kw").to_csv(out/"vehicle_dispatch.csv",index=False)
        from v2g.availability import session_energy
        ves=[]
        for i,v in enumerate(team["vehicles"]):
            state=session_energy(solution["vehicle_power_kw"][i],v.battery,v.arrival,v.departure,1.)
            for boundary,e in enumerate(state):
                ves.append(dict(vehicle_id=v.id,boundary=boundary,energy_kwh=float(e),
                                soc=float(e/v.battery.capacity_kwh),
                                reserve_kwh=float(v.reserve_kwh),
                                departure_target_kwh=float(v.departure_target_kwh),
                                departure_hour=int(v.departure)))
        pd.DataFrame(ves).to_csv(out/"vehicle_energy.csv",index=False)
        discharge=np.maximum(solution["vehicle_power_kw"],0.)
        recharge=np.maximum(-solution["vehicle_power_kw"],0.)
        bess_c=solution["bess_charge_kw"];bess_d=solution["bess_discharge_kw"]
        batt=team["battery"]
        throughput=float((bess_c+bess_d).sum())
        wear=float(throughput*batt.degradation_per_kwh)
        cycles=float((bess_c.sum()*batt.eta_charge+bess_d.sum()/batt.eta_discharge)/(2*batt.capacity_kwh))
        finance={"bess_capacity_kwh":batt.capacity_kwh,"bess_power_kw":batt.power_kw,
                 "bess_throughput_ac_kwh":throughput,"bess_equivalent_cycles":cycles,
                 "bess_degradation_usd":wear,"bess_capex_usd":batt.capex,
                 "v2g_vehicle_count":len(team["vehicles"]),
                 "v2g_delivered_kwh":float(discharge.sum()),
                 "v2g_recharge_kwh":float(recharge.sum()),
                 "v2g_charging_cost_usd":float((recharge*team["tariff"][None,:]).sum()),
                 "v2g_discharge_remuneration_usd":float((discharge*team["remuneration"][None,:]).sum()),
                 "v2g_station_capex_usd":team["station"].capex,
                 "scope":"DER operational profiles only; station CAPEX and BESS CAPEX are hypotheses from YAML"}
        save_json(out/"financial_and_fleet_kpis.json",finance)
        save_json(out/"milp_diagnostic.json",solution["report"])
        full=build_scenario_injections(s1_profile,team["old_fleet_profile"],team,solution)
        new=simulate_solution(ctx,full,team,s1,out)
        summary,checked,badline,badvolt=score(s0,s1,new)
        save_json(out/"ac_verification.json",checked)
        (out/"comparison_24h.csv").write_text(pd.DataFrame.from_dict(summary,orient="index").drop(columns=["new_overload_line_ids_vs_S0"],errors="ignore").rename_axis("scenario").reset_index().to_csv(index=False),encoding="utf-8")
        badline.sort_values("excess_vs_s0_pp",ascending=False).to_csv(out/"thermal_aggravations_vs_s0.csv",index=False)
        badvolt.assign(max_aggravation=lambda x:x[["extra_under_pu","extra_over_pu"]].max(axis=1))\
               .sort_values("max_aggravation",ascending=False).to_csv(out/"voltage_aggravations_vs_s0.csv",index=False)
        manifest={"status":"OPEN_DSS_AC_REPLAY_COMPLETED_NOT_AUTOMATICALLY_FEASIBLE",
                  "created_utc":datetime.now(timezone.utc).isoformat(),"mode":args.mode,
                  "S0_S1_dataset":str(results),"delta_kw":args.delta_kw,
                  "simulation":{"source_mode":ctx["settings"]["simulation"]["source_mode"],
                                 "controls":ctx["settings"]["simulation"]["controls"],
                                 "ampacity":ctx["settings"]["simulation"]["ampacity"]},
                  "original_files_sha256":{k:sha256(v) for k,v in ctx["files"].items()},
                  "baseline_replay_max_errors":replayerr,
                  "limitations":["IEEE8500 reference is a hypothetical Honduras proxy; it is NOT a measured Honduran feeder",
                                 "Thermal ampacities are study assumptions",
                                 "DER point connection neglects dedicated transformers",
                                 "Only local, frozen-control OpenDSS derivatives enter the MILP; final AC verification uses automatic controls",
                                 "The MILP permits residual overload/voltage slacks; only the final AC reports establish operational compliance",
                                 "Vehicle charging/discharging uses a continuous LP per car (no within-step simultaneous actions due disjoint windows)"]}
        save_json(out/"manifest.json",manifest)
        print("\n[FEITO] MILP e replay AC concluídos. Resultados:",out)
        print("Indicadores AC:",json.dumps({k:{x:y for x,y in v.items() if x in ["v_min_pu","v_max_pu","loading_max_pct","overload_samples","overloaded_unique_lines","peak_import_kw","thermal_severity_pp_hour","voltage_violation_samples","new_distinct_overloaded_lines_vs_S0"]} for k,v in summary.items()},indent=2))
        print("Verificação contra S0:",json.dumps(checked,indent=2))
        if not checked["S4_completely_satisfies_no_aggravation"]:
            print("AVISO: DESPACHO AINDA NÃO SATISFAZ TODOS OS LIMITES AC; não chamar de S4 validado para periódico.")
    except Exception as exc:
        save_json(output/"failure.json",{"status":"FAIL_NO_VALIDATED_OPTIMIZATION","error":str(exc),
                                         "traceback":traceback.format_exc()})
        raise

if __name__=="__main__":main()
