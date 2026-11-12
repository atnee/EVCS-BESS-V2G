#!/usr/bin/env python3
"""V1.1 — OpenDSS AC-guided per-phase/hour guards and local Pareto search.

Launch from repository root after installing the complete V1.0 and V1.1 patches.
Scientific scope: 24 synthetic hourly IEEE 8500 snapshots. Only actual AC
OpenDSS replays can establish candidate performance. The local linear MILP
never certifies electrical feasibility or global optimality.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import traceback

import numpy as np
import pandas as pd

import reotimizar_bess_v2g as v08
import reotimizar_sequencial_v09 as v09
import reotimizar_pareto_v10 as v10
from rebuild.contracts import load_context, sha256
from rebuild.joint_optimization import OptSettings, solve_joint

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# (thermal weight, under weight, over weight, peak weight,
#  improvement target on critical lines in pp, minimum voltage in pu,
#  maximum voltage in pu, top line/under/over guard counts)
PROFILES = {
    "thermal_guard": (95., 9000., 11000., .6, .12, .00015, .00010, 16, 25, 16),
    "voltage_guard": (48., 23000., 23000., .5, .03, .00035, .00025, 10, 35, 25),
    "balanced_guard": (65., 15000., 15000., .5, .07, .00025, .00015, 14, 30, 20),
}
DEFAULT_SEEDS = ("i01_iteration_01_trial_01_thermal",
                 "i01_iteration_01_trial_01_under_guard")


def required_legacy_hashes(v10dir: Path, ctx: dict) -> None:
    config_path=v10dir/"configuracao.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Faltou configuração da V1.0: {config_path}")
    data=json.loads(config_path.read_text(encoding="utf-8"))
    for label, path in ctx["files"].items():
        saved=data.get("source_hashes", {}).get(label)
        if saved!=sha256(path):
            raise RuntimeError(f"Dado original diferente do usado na V1.0: {label}")
    for key in ("controls", "source_mode"):
        if data.get(key)!=ctx["settings"]["simulation"].get(key):
            raise RuntimeError(f"Configuração elétrica {key} diverge da V1.0")
    if data.get("controls")!="auto":
        raise RuntimeError("V1.1 exige baseline com controles automáticos")


def new_line_ids(s0_br: pd.DataFrame, s1_br: pd.DataFrame) -> set[str]:
    old=set(s0_br.loc[s0_br.loading_pct>100., "line"].astype(str))
    new=set(s1_br.loc[s1_br.loading_pct>100., "line"].astype(str))
    return new-old


def introduced_overloads(ac_br: pd.DataFrame, s0_br: pd.DataFrame) -> set[str]:
    old=set(s0_br.loc[s0_br.loading_pct>100., "line"].astype(str))
    now=set(ac_br.loc[ac_br.loading_pct>100., "line"].astype(str))
    return now-old


def build_guards(s0: dict, s1: dict, baseline: dict, derivatives: tuple[pd.DataFrame, pd.DataFrame],
                 profile: str, bess_trust_kw: float, v2g_trust_kw: float) -> dict:
    """Create AC-informed hourly, phase-specific cuts around the ACTUAL AC parent.

    Guard the FULL S1-only line set across all phases/hours without requiring
    previously-overloaded IEEE lines to be magically below 100%. Incremental
    improvement targets are applied ONLY where the finite differences can respond.
    """
    if profile not in PROFILES:
        raise ValueError(f"Perfil desconhecido: {profile}")
    _, _, _, _, line_step, under_step, over_step, line_count, under_count, over_count=PROFILES[profile]
    ds_lines, ds_volt=derivatives
    line_keys=["hour", "line", "phase"]
    voltage_keys=["hour", "bus", "phase"]
    oldline=s0["branches"][line_keys+["loading_pct"]].rename(columns={"loading_pct":"s0"})
    parentline=baseline["branches"][line_keys+["loading_pct"]].rename(columns={"loading_pct":"parent"})
    line=parentline.merge(oldline,on=line_keys,validate="one_to_one")
    if len(line)!=len(parentline):
        raise ValueError("Faltam elementos do S0 na referência de linhas")
    line=line.merge(ds_lines,on=line_keys,validate="one_to_one")
    if len(line)!=len(parentline):
        raise ValueError("Faltam derivadas OpenDSS para linhas")
    oldv=s0["voltages"][voltage_keys+["v_pu"]].rename(columns={"v_pu":"s0"})
    parentv=baseline["voltages"][voltage_keys+["v_pu"]].rename(columns={"v_pu":"parent"})
    vol=parentv.merge(oldv,on=voltage_keys,validate="one_to_one")
    if len(vol)!=len(parentv):
        raise ValueError("Faltam elementos do S0 na referência de tensões")
    vol=vol.merge(ds_volt,on=voltage_keys,validate="one_to_one")
    if len(vol)!=len(parentv):
        raise ValueError("Faltam derivadas OpenDSS para tensões")
    recovered=new_line_ids(s0["branches"],s1["branches"])
    if not recovered:
        raise ValueError("Nenhuma linha crítica identificada no S1 contra o S0")
    # Do NOT start a guarded solve from a parent which has reopened any S1-only line.
    if introduced_overloads(baseline["branches"],s0["branches"]) & recovered:
        raise ValueError("Semente AC reintroduziu linhas críticas e não atende ao contrato de guarda")
    line_guard={}
    for row in line.loc[line.line.isin(recovered)].itertuples(index=False):
        line_guard[(int(row.hour),str(row.line),str(row.phase))]=100.0
    recovered_n=len(line_guard)
    line["excess"]=(line.parent-np.maximum(100.,line.s0)).clip(lower=0)
    line["max_control_effect"]=(line.dl_bess.abs()*bess_trust_kw+line.dl_v2g.abs()*v2g_trust_kw)
    hot=line.loc[(line.excess>1e-5)&(line.max_control_effect>0.5*line_step)]
    hot=hot.sort_values(["excess","hour","line","phase"],ascending=[False,True,True,True]).head(line_count)
    n_hot=0
    for row in hot.itertuples(index=False):
        key=(int(row.hour),str(row.line),str(row.phase))
        target=float(row.parent-min(line_step,0.5*row.excess))
        line_guard[key]=min(line_guard.get(key,np.inf),target)
        n_hot+=1
    vol["under_excess"]=(np.minimum(.95,vol.s0)-vol.parent).clip(lower=0)
    vol["over_excess"]=(vol.parent-np.maximum(1.05,vol.s0)).clip(lower=0)
    vol["max_control_effect"]=(vol.dv_bess.abs()*bess_trust_kw+vol.dv_v2g.abs()*v2g_trust_kw)
    def select(metric:str,step:float,count:int) -> pd.DataFrame:
        good=vol.loc[(vol[metric]>1e-6)&(vol.max_control_effect>0.5*step)]
        return good.sort_values([metric,"hour","bus","phase"],ascending=[False,True,True,True]).head(count)
    lower={}
    for row in select("under_excess",under_step,under_count).itertuples(index=False):
        lower[(int(row.hour),str(row.bus),str(row.phase))]=float(row.parent+min(under_step,0.5*row.under_excess))
    upper={}
    for row in select("over_excess",over_step,over_count).itertuples(index=False):
        upper[(int(row.hour),str(row.bus),str(row.phase))]=float(row.parent-min(over_step,0.5*row.over_excess))
    as_tuples=lambda d:tuple((*k,float(v)) for k,v in sorted(d.items()))
    return {"line":as_tuples(line_guard),"under":as_tuples(lower),"over":as_tuples(upper),
            "stats":{"recovered_line_ids":len(recovered),"full_recovered_line_phase_hours":recovered_n,
                     "targeted_thermal_samples":n_hot,"targeted_under_samples":len(lower),
                     "targeted_over_samples":len(upper),"guarded_total_line_samples":len(line_guard),
                     "notes":"Cortes lineares locais. A validação final é somente por replay AC."},
            "recovered_line_ids":sorted(recovered)}


def make_opt(profile: str, guards: dict, team: dict, args) -> OptSettings:
    th,under,over,peak,*_=PROFILES[profile]
    return OptSettings(thermal_weight=th,undervoltage_weight=under,
                       overvoltage_weight=over,peak_weight=peak,
                       bess_trust_kw=args.bess_trust_kw,v2g_trust_kw=args.v2g_trust_kw,
                       guarded_line_max_pct=guards["line"],
                       guarded_voltage_min_pu=guards["under"],
                       guarded_voltage_max_pu=guards["over"],
                       bess_throughput_usd_kwh=float(team["battery"].degradation_per_kwh),
                       vehicle_degradation_usd_kwh=float(team["configs"]["v2g"]["cesar_v1"].get("degradation_usd_kwh",0)),
                       solver_time_limit_s=args.time_limit,mip_rel_gap=args.mip_gap)


def check_guards_ac(dfs: dict, guards: dict) -> dict:
    """Count actual (not predicted) line- and voltage-guard satisfaction."""
    x={k:df.set_index(["hour","line","phase"])["loading_pct"] if k=="line" else
       df.set_index(["hour","bus","phase"])["v_pu"] for k,df in
       (("line",dfs["branches"]),("under",dfs["voltages"]),("over",dfs["voltages"]))}
    output={}
    for kind,comp in (("line","le"),("under","ge"),("over","le")):
        passed=0;errors=[]
        for t,elem,phase,bound in guards[kind]:
            actual=float(x[kind].at[(int(t),elem,phase)])
            margin=actual-bound if comp=="le" else bound-actual
            passed+=margin<=1e-7
            errors.append(max(0.,float(margin)))
        output[kind]={"checked":len(errors),"passed":int(passed),
                      "maximum_violation":max(errors,default=0.),
                      "fraction_satisfied":(passed/len(errors) if errors else None)}
    return output


def cost_proxies(item: dict, team: dict) -> dict:
    """Indicative numbers only; source tariff contract and payments need checking."""
    source=np.maximum(item["dfs"]["source"].sort_values("hour").p_kw.to_numpy(float),0.)
    bess=np.asarray(item["bess"],float)
    vehicle=np.asarray(item["vehicles"],float)
    tariff=np.asarray(team["tariff"],float)
    remuneration=np.asarray(team["remuneration"],float)
    if source.shape!=(24,) or tariff.shape!=(24,) or remuneration.shape!=(24,):
        raise ValueError("Séries econômicas inconsistentes")
    return {"source_import_kwh":float(source.sum()),
            "source_energy_cost_proxy_usd":float(np.dot(source,tariff)),
            "bess_throughput_proxy_usd":float(np.abs(bess).sum()*team["battery"].degradation_per_kwh),
            "vehicle_degradation_proxy_usd":float(np.maximum(vehicle,0.).sum()*
                        team["configs"]["v2g"]["cesar_v1"].get("degradation_usd_kwh",0.)),
            "v2g_export_revenue_proxy_usd":float(np.dot(np.maximum(vehicle,0.).sum(axis=0),remuneration)),
            "warning":"Tarifa de referência da frota aplicada à importação como proxy; não implica custo atacadista real."}


def save_state(out: Path, eligible: list[dict], evaluated: list[dict], attempts: list[dict],
               s0: dict, legacy: dict | None, immutable_config: dict) -> None:
    flags=v10.non_dominated([p["score"] for p in eligible])
    names={id(p):bool(flag) for p,flag in zip(eligible,flags)}
    info=[]
    for item in evaluated:
        record=dict(item["score"])
        record.update(name=item["name"],eligible=bool(item["eligible"]),
                      nondominated_eligible=names.get(id(item),False),
                      origin=item["origin"],folder=str(item["folder"]),
                      reopened_distinct_new_lines=int(item["reopened"]),
                      ac_no_aggravation=bool(item["check"].get("S4_completely_satisfies_no_aggravation",False)))
        info.append(record)
    pd.DataFrame(info).to_csv(out/"todos_candidatos_ac_v11.csv",index=False)
    pd.DataFrame([z for z in info if z["nondominated_eligible"]]).to_csv(out/"fronteira_pareto_ac_v11.csv",index=False)
    pd.DataFrame(attempts).to_csv(out/"historico_v11.csv",index=False)
    v10.save_json(out/"resumo_final_v11.json",{
        "status":"AC_EVALUATED_LOCAL_PARETO_NO_GLOBAL_AC_FEASIBILITY_CLAIM",
        "candidates_ac_evaluated":len(evaluated),"ac_eligible_no_reopened_s1_lines":len(eligible),
        "nondominated_eligible":[i["name"] for i,f in zip(eligible,flags) if f],
        "ac_feasible_no_aggravation":[i["name"] for i in evaluated if
          i["check"].get("S4_completely_satisfies_no_aggravation",False)],
        "legacy_s4_ac_score":legacy,"immutable_config":immutable_config,
        "limitations":["24 hourly synthetic OpenDSS snapshots, not field measurements",
          "Study ampacities and original IEEE 1.05pu source",
          "Local frozen-control derivatives; each output candidate replayed with auto controls",
          "No guarantee of complete thermal/voltage feasibility or global AC optimality",
          "Cost fields are indicative proxies, not a validated cost-benefit study"],
        "generated_utc":datetime.now(timezone.utc).isoformat()})


def cli():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--repo",type=Path,default=ROOT)
    ap.add_argument("--results",type=Path,required=True,help="Results directory of S0/S1/S4 replay")
    ap.add_argument("--v10-dir",type=Path,required=True,help="Existing V1.0 Pareto directory")
    ap.add_argument("--output",type=Path,required=True,help="New empty output folder")
    ap.add_argument("--task",choices=("inspect","run"),default="run")
    ap.add_argument("--seeds",default=",".join(DEFAULT_SEEDS),help="Comma-separated V1.0 AC candidate names")
    ap.add_argument("--profiles",default="thermal_guard,voltage_guard",help="Comma-separated: "+",".join(PROFILES))
    ap.add_argument("--max-iterations",type=int,default=1)
    ap.add_argument("--max-candidates",type=int,default=4)
    ap.add_argument("--bess-trust-kw",type=float,default=180.)
    ap.add_argument("--v2g-trust-kw",type=float,default=150.)
    ap.add_argument("--delta-kw",type=float,default=100.)
    ap.add_argument("--time-limit",type=float,default=300.)
    ap.add_argument("--mip-gap",type=float,default=.02)
    return ap.parse_args()


def main() -> None:
    args=cli()
    names=[s.strip() for s in args.profiles.split(",") if s.strip()]
    seeds=[s.strip() for s in args.seeds.split(",") if s.strip()]
    if not names or len(set(names))!=len(names) or any(x not in PROFILES for x in names):
        raise ValueError("Perfis inválidos: "+",".join(PROFILES))
    if not seeds or len(set(seeds))!=len(seeds) or any("/" in s or "\\" in s or s in (".","..") for s in seeds):
        raise ValueError("Nomes de sementes inválidos")
    if min(args.max_iterations,args.max_candidates)<1 or not (10<=args.delta_kw<=250):
        raise ValueError("Iterações, nº de candidatos ou delta não permitido")
    if not (0<args.bess_trust_kw<=2200 and 0<args.v2g_trust_kw<=1694 and args.time_limit>0 and 0<args.mip_gap<1):
        raise ValueError("Regiões de confiança ou parâmetros MILP inválidos")
    if "guarded_line_max_pct" not in OptSettings.__dataclass_fields__:
        raise RuntimeError("Instale o ZIP V1.1 completo: joint_optimization.py sem os cortes AC da V1.1")
    replay=args.results.resolve();old=args.v10_dir.resolve();out=args.output.resolve();repo=args.repo.resolve()
    if not replay.is_dir() or not old.is_dir():
        raise FileNotFoundError("Faltam o replay S0/S1/S4 ou resultados V1.0")
    if any(out==r or r in out.parents for r in (replay,old,repo) if r!=repo):
        raise ValueError("Pasta de saída não pode estar dentro das entradas")
    if out in (repo,replay,old) or (out.exists() and any(out.iterdir())):
        raise FileExistsError("Escolha uma nova pasta de saída vazia para a V1.1")
    out.mkdir(parents=True,exist_ok=True)
    try:
        ctx=load_context(repo,HERE)
        required_legacy_hashes(old,ctx)
        sims,s1=v08.read_existing(replay)
        team=v08.read_team(repo,replay)
        frontier=pd.read_csv(old/"fronteira_pareto_ac.csv")
        eligible=[];evaluated=[];seen=set()
        for name in frontier.name.astype(str):
            folder=old/"candidates"/name
            if not all((folder/f"{x}.csv").is_file() for x in (*v09.TABLES,"vehicle_dispatch","injections_reoptimized","dispatch_24h")):
                print(f"[V1.1] Semente V1.0 ignorada por AC incompleto: {name}",flush=True)
                continue
            item=v10.read_saved_candidate(folder,name,team,s1,team["old_fleet_profile"])
            item.update(score=v10.summarize(item,sims["S0"]),
                        check=v10.ac_feasibility(sims["S0"],sims["S1"],item),
                        origin="v10_imported_ac",eligible=False)
            item["reopened"]=len(introduced_overloads(item["dfs"]["branches"],sims["S0"]["branches"]))
            item["eligible"]=item["reopened"]==0
            seen.add(v10.solution_hash(item))
            evaluated.append(item)
            if item["eligible"]:eligible.append(item)
        if not eligible:
            raise RuntimeError("Nenhuma semente V1.0 AC sem novas linhas sobrecarregadas")
        by_name={i["name"]:i for i in eligible}
        if any(name not in by_name for name in seeds):
            raise ValueError("Sementes solicitadas ausentes na fronteira AC completa: "+
                             str(sorted(set(seeds)-set(by_name))))
        legacy=(v10.summarize({"name":"legacy_s4","folder":replay/"S4",
                              "dfs":v09.load_tables(replay/"S4")},sims["S0"])
                if all((replay/"S4"/f"{n}.csv").is_file() for n in v09.TABLES) else None)
        config={"profiles":names,"seeds":seeds,"max_iterations":args.max_iterations,
                "max_candidates":args.max_candidates,"bess_trust_kw":args.bess_trust_kw,
                "v2g_trust_kw":args.v2g_trust_kw,"delta_kw":args.delta_kw,
                "source_sha256":{k:sha256(p) for k,p in ctx["files"].items()}}
        v10.save_json(out/"configuracao_v11.json",config)
        attempts=[]
        save_state(out,eligible,evaluated,attempts,sims["S0"],legacy,config)
        print(f"[V1.1] Importados {len(evaluated)} candidatos AC, {len(eligible)} sem reabertura de linhas novas",flush=True)
        if args.task=="inspect":
            print(f"[V1.1 INSPECT] {out/'fronteira_pareto_ac_v11.csv'}",flush=True)
            return
        trials=0
        for iteration in range(1,args.max_iterations+1):
            active=[by_name[name] for name in seeds] if iteration==1 else [
                i for i,f in zip(eligible,v10.non_dominated([x["score"] for x in eligible]))
                if f and i["origin"]=="v11_optimized"]
            if not active:
                print("[V1.1] Nenhuma nova semente AC admissível disponível",flush=True)
                break
            improved=0
            for parent in active:
                if trials>=args.max_candidates:break
                work=out/f"iteration_{iteration:02d}"/f"seed_{parent['name']}"
                work.mkdir(parents=True,exist_ok=True)
                ref={"dfs":parent["dfs"],"profile":parent["profile"],
                     "bess":parent["bess"],"fleet":parent["fleet"],"vehicles":parent["vehicles"]}
                dl,dv,ds,check=v09.finite_difference(ctx,team,ref,work,args.delta_kw)
                v10.save_json(work/"reproducao_ac.json",check)
                for profile in names:
                    if trials>=args.max_candidates:break
                    trials+=1
                    tag=f"i{iteration:02d}_{parent['name']}_{profile}"
                    childdir=out/"candidates"/tag
                    childdir.mkdir(parents=True,exist_ok=False)
                    row={"name":tag,"parent":parent["name"],"profile":profile,
                         "status":"started","folder":str(childdir)}
                    try:
                        guards=build_guards(sims["S0"],sims["S1"],parent["dfs"],(dl,dv),
                                            profile,args.bess_trust_kw,args.v2g_trust_kw)
                        v10.save_json(childdir/"guardas_lineares.json",guards)
                        opt=make_opt(profile,guards,team,args)
                        solution=solve_joint(
                            vehicles=team["vehicles"],grid=team["grid"],station=team["station"],
                            battery=team["battery"],bess_bus=team["bess_bus"],v2g_bus=team["v2g_bus"],
                            source_s1=parent["dfs"]["source"],lines_s1=parent["dfs"]["branches"],
                            volt_s1=parent["dfs"]["voltages"],lines_s0=sims["S0"]["branches"],
                            volt_s0=sims["S0"]["voltages"],ds_lines=dl,ds_volt=dv,ds_source=ds,
                            fleet_s1_kw=team["old_fleet_net"],fleet_s1_per_vehicle_kw=team["old_fleet"],
                            independent_evcs_kw=team["ind"],discharge_window=team["discharge_window"],
                            no_charge_window=team["no_charge_window"],tariffs=team["tariff"],
                            remuneration=team["remuneration"],settings=opt,mode="S4",
                            network_reference_bess_kw=parent["bess"],
                            network_reference_fleet_kw=parent["fleet"])
                        v09.save_candidate(solution,childdir,team)
                        child=v10.solution_from_milp(solution,childdir,tag)
                        key=v10.solution_hash(child)
                        if key in seen:
                            row["status"]="duplicate_commands"
                            continue
                        v10.energy_and_fleet_check(child["bess"],child["vehicles"],team)
                        injections=v08.build_scenario_injections(s1,team["old_fleet_profile"],team,solution)
                        physical=v08.simulate_solution(ctx,injections,team,sims["S1"],childdir)
                        child.update(dfs=physical,profile=injections,origin="v11_optimized")
                        child["score"]=v10.summarize(child,sims["S0"],source="v11_opendss_auto")
                        child["check"]=v10.ac_feasibility(sims["S0"],sims["S1"],child)
                        child["reopened"]=len(introduced_overloads(physical["branches"],sims["S0"]["branches"]))
                        child["eligible"]=child["reopened"]==0
                        guard_check=check_guards_ac(physical,guards)
                        proxy=cost_proxies(child,team)
                        v10.save_json(childdir/"guardas_ac_verificacao.json",guard_check)
                        v10.save_json(childdir/"indicadores_economicos_proxy.json",proxy)
                        v10.save_json(childdir/"ac_score_v11.json",child["score"])
                        v10.save_json(childdir/"ac_verification_v11.json",child["check"])
                        seen.add(key);evaluated.append(child)
                        if child["eligible"]:
                            eligible.append(child)
                            improved+=1
                            row["status"]="ac_eligible_pareto_pool"
                        else:
                            row["status"]="ac_rejected_reopened_s1_only_lines"
                        row.update(reopened_new_lines=child["reopened"],
                                   thermal_pph=child["score"]["thermal_vs_s0_pph"],
                                   under_puh=child["score"]["under_vs_s0_puh"],
                                   over_puh=child["score"]["over_vs_s0_puh"],
                                   peak_kw=child["score"]["peak_import_kw"],
                                   real_ac_guards=guard_check)
                        print(f"[V1.1 AC] {tag}: thermal={row['thermal_pph']:.3f}, "
                              f"under={row['under_puh']:.4f}, over={row['over_puh']:.4f}, "
                              f"new_lines={row['reopened_new_lines']}, {row['status']}",flush=True)
                    except Exception as exc:
                        row.update(status="failed",error=str(exc))
                        v10.save_json(childdir/"error.json",{"error":str(exc),
                                               "traceback":traceback.format_exc()})
                        print(f"[V1.1 ERROR] {tag}: {exc}",flush=True)
                    finally:
                        attempts.append(row)
                        save_state(out,eligible,evaluated,attempts,sims["S0"],legacy,config)
            if improved==0:
                print("[V1.1] Nenhum novo candidato AC passou o filtro de não reabertura",flush=True)
                break
            if trials>=args.max_candidates:break
        print(f"[V1.1 CONCLUÍDO] {len(evaluated)} candidatos AC; "
              f"fronteira={sum(v10.non_dominated([x['score'] for x in eligible]))} | {out}",flush=True)
        if not any(x["check"].get("S4_completely_satisfies_no_aggravation",False) for x in evaluated):
            print("AVISO: nenhuma solução com ausência total de agravamentos AC foi identificada",flush=True)
    except Exception as exc:
        v10.save_json(out/"failure.json",{"error":str(exc),"traceback":traceback.format_exc()})
        raise

if __name__=="__main__":
    main()
