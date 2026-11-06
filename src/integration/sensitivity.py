"""Sensitivity study of the S0-S4 integration: one full run per case of configs/sensitivity.yaml, in parallel.

python -m integration.sensitivity                       # every case, then the report (hours)
python -m integration.sensitivity --cases seed_01 load_110
python -m integration.sensitivity --report              # only rebuild the report from saved cases
Each case writes results/<feeder>/sensitivity/<case>/: its config folder (configs/), the usual integration outputs
(summary.csv, criterio.csv, figures) and compact tables in dados/ (line peaks, hourly series, taps). Raw per-bus
tables are deleted after the compact ones are written, except for the cases in `keep_raw_tables`. A case that
fails (for example out of memory) is retried in sequence at the end. configs/ is never modified.
Report: results/<feeder>/sensitivity/RELATORIO.md, sensitivity.csv and sensitivity_*.png.
"""
from pathlib import Path
import argparse
import copy
import json
import os
import shutil
import time
import traceback
import numpy as np
import pandas as pd
import yaml
from integration.scenarios import study_feeder
from integration.parallel import SINGLE_THREAD
from integration.impact import tap_operations

RAW = ("voltages","branches","native_loads","frozen_taps_voltages","frozen_taps_branches")


# ------------------------------------------------------------------------------------------- case configs
def set_key(tree, dotted, value):
    """tree[a][b]... = value for 'a.b...'; the key must already exist (a typo cannot create a new one)."""
    *parents,last = dotted.split(".")
    node = tree
    for key in parents:
        if not isinstance(node,dict) or key not in node:
            raise ValueError(f"Unknown key {dotted!r}")
        node = node[key]
    if not isinstance(node,dict) or last not in node:
        raise ValueError(f"Unknown key {dotted!r}")
    node[last] = value


def case_configs(base, case, feeder="ieee8500"):
    """{file: yaml tree} of one case from the base trees {file: tree}; pure, the base is not changed."""
    c = copy.deepcopy(base)
    for name,keys in (case.get("set") or {}).items():
        for key,value in keys.items():
            set_key(c[name],key,value)
    if "seed" in case:
        set_key(c["evcs.yaml"],"planning.seed",int(case["seed"]))
        set_key(c["v2g.yaml"],"fleet.seed",int(case["seed"]))
    if "load_scale" in case:
        c[f"{feeder}.yaml"]["load_multipliers"] = [round(float(m)*case["load_scale"],6) for m in c[f"{feeder}.yaml"]["load_multipliers"]]
    if "bess_scale" in case:
        b = c["bess.yaml"]["battery"]
        for key in ("capacity_kwh","power_kw","capex"):
            b[key] = float(b[key])*case["bess_scale"]
    if "v2g_fleet" in case:
        n, evcs, v2g = int(case["v2g_fleet"]), c["evcs.yaml"], c["v2g.yaml"]
        total = evcs["integration"]["evs"]+v2g["fleet"]["n_vehicles"]  # fleet size of the study (3000)
        ports = evcs["station"]["chargers"]-v2g["fleet"]["n_vehicles"]  # ports of the independent demand (4)
        per_port = evcs["station"]["capex"]/evcs["station"]["chargers"]
        v2g["fleet"]["n_vehicles"], evcs["integration"]["evs"] = n, total-n
        for station in (evcs["station"],v2g["standalone"]["station"]):
            station["chargers"] = n+ports
            station["connection_kw"] = float((n+ports)*station["charger_kw"])
            station["capex"] = float((n+ports)*per_port)
    if "resolution_min" in case:
        from integration.s0_study import load_curve_from
        steps = 1440//int(case["resolution_min"])
        per_hour = steps//24
        for name in ("evcs.yaml","bess.yaml","v2g.yaml"):
            c[name]["time"].update(steps=steps,dt_h=24/steps)
        c[f"{feeder}.yaml"]["load_multipliers"] = [round(float(m),6) for m in load_curve_from(c[f"{feeder}.yaml"]["load_multipliers"],steps)]
        repeat = lambda values: [float(v) for v in values for _ in range(per_hour)]  # hourly kW held over the hour
        c["evcs.yaml"]["independent_demand_kw"] = repeat(c["evcs.yaml"]["independent_demand_kw"])
        c["bess.yaml"]["standalone_demand_kw"] = repeat(c["bess.yaml"]["standalone_demand_kw"])
        c["v2g.yaml"]["standalone"]["independent_evcs_kw"] = repeat(c["v2g.yaml"]["standalone"]["independent_evcs_kw"])
    return c


def write_case_configs(folder, trees):
    folder = Path(folder); folder.mkdir(parents=True,exist_ok=True)
    for name,tree in trees.items():
        (folder/name).write_text(yaml.safe_dump(tree,sort_keys=False,allow_unicode=True),encoding="utf-8")


# ------------------------------------------------------------------------------------------- one case
def regulator_patch(settings):
    """Make integration.coordinator load the feeder with these regulator settings. Returns the function that
    undoes it: worker processes are reused between cases, so the patch must not outlive its case."""
    import integration.coordinator as coordinator
    from network.feeder_loader import load_feeder
    def load(feeder="ieee8500", path=None):
        network = load_feeder(feeder,path)
        for r in network.equipment["feeder_data"][0]["regulators"]:
            if "vreg_pu" in r:
                r.update(settings)
        return network
    coordinator.load_feeder = load
    return lambda: setattr(coordinator,"load_feeder",load_feeder)


def compact_tables(out):
    """Small tables kept for every case: line peaks per scenario, hourly feeder series, taps."""
    from integration.resumo import line_peaks
    dados = out/"dados"
    peaks, series = {}, []
    for f in sorted(dados.glob("S?_branches.csv")):
        sc = f.name[:2]
        peaks[sc],_ = line_peaks(pd.read_csv(f))
        frozen = dados/f"{sc}_frozen_taps_branches.csv"
        if frozen.exists():
            peaks[f"{sc}_frozen"],_ = line_peaks(pd.read_csv(frozen))
        v = pd.read_csv(dados/f"{sc}_voltages.csv")
        src = pd.read_csv(dados/f"{sc}_source.csv")
        t = pd.DataFrame({"scenario": sc,"time": src.time,"p_kw": src.p_kw,"v_min_pu": v.groupby("time").v_pu.min().reindex(src.time).to_numpy()})
        for col in [c for c in src.columns if c.startswith("tap_")]:
            t[col] = src[col]
        series.append(t)
    pd.DataFrame(peaks).to_csv(dados/"line_peaks.csv",index_label="line")
    pd.concat(series).to_csv(dados/"series.csv",index=False)


def run_case(name, case, root, base_trees, feeder, keep_raw, calibration=None):
    """Full S0-S4 run of one case into root/name. Returns a status dict (never raises)."""
    out = Path(root)/name
    start, undo = time.time(), None
    try:
        trees = case_configs(base_trees,case,feeder)
        if calibration is not None:
            trees["bess.yaml"]["line_limit"]["lines"] = calibration
        if out.exists():
            shutil.rmtree(out)
        write_case_configs(out/"configs",trees)
        (out/"case.json").write_text(json.dumps({"name": name,**case},indent=2,default=str),encoding="utf-8")
        if case.get("regulators"):
            undo = regulator_patch(case["regulators"])
        import matplotlib
        matplotlib.use("Agg")
        from integration.coordinator import export_results
        from integration.resumo import criterion_table
        export_results(out,out/"configs",frozen_taps=True)
        criterion_table(out)
        compact_tables(out)
        if not keep_raw:
            for f in (out/"dados").glob("*.csv"):
                if any(f.name.endswith(f"_{kind}.csv") for kind in RAW):
                    f.unlink()
        status = dict(case=name,ok=True,minutes=round((time.time()-start)/60,1))
    except Exception as error:  # reported and retried, never hides the failure
        status = dict(case=name,ok=False,minutes=round((time.time()-start)/60,1),error=f"{type(error).__name__}: {error}",
                      trace=traceback.format_exc())
    finally:
        if undo:
            undo()
    out.mkdir(parents=True,exist_ok=True)
    (out/"status.json").write_text(json.dumps(status,indent=2),encoding="utf-8")
    return status


def recalibration(root, source):
    """line_limit lines recalibrated on a saved case (integration.bess_calibration)."""
    from integration.bess_calibration import calibrate
    from core.schemas import read_parameters
    settings = read_parameters(Path(root)/source/"configs"/"bess.yaml")["line_limit"]["calibration"]
    fit = calibrate(Path(root)/source,settings)
    chosen = fit[fit.monitored]
    return {line: {"a": round(float(r.a),6),"b": round(float(r.b),8),"s": round(float(r.s),8),
                   "margin": round(float(np.ceil(r.max_abs_residual*100)/100),2)} for line,r in chosen.iterrows()}


# ------------------------------------------------------------------------------------------- orchestration
def run_all(config_dir="configs", results="results", only=None, workers=None, log=print):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    feeder = study_feeder(config_dir)
    spec = yaml.safe_load((Path(config_dir)/"sensitivity.yaml").read_text(encoding="utf-8"))
    cases = {k: v for k,v in spec["cases"].items() if not only or k in only}
    root = Path(results)/feeder/"sensitivity"; root.mkdir(parents=True,exist_ok=True)
    base = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in Path(config_dir).glob("*.yaml") if p.name != "sensitivity.yaml"}
    keep = set(spec.get("keep_raw_tables",[]))
    independent = [k for k,v in cases.items() if "recalibrate_from" not in v]
    dependent = [k for k,v in cases.items() if "recalibrate_from" in v]
    workers = workers or int(spec.get("workers",4))
    # longest cases first so they do not end the night alone
    independent.sort(key=lambda k: -int(1440//int(cases[k].get("resolution_min",60))))
    statuses = {}
    saved = {k: os.environ.get(k) for k in SINGLE_THREAD}
    os.environ.update({k: "1" for k in SINGLE_THREAD})
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_case,k,cases[k],root,base,feeder,k in keep): k for k in independent}
            for f in as_completed(futures):
                k = futures[f]
                try:
                    statuses[k] = f.result()
                except Exception as error:  # broken pool (a worker died): retried below
                    statuses[k] = dict(case=k,ok=False,error=f"{type(error).__name__}: {error}")
                log(f"{time.strftime('%H:%M')} {k}: {'ok' if statuses[k]['ok'] else 'FAILED '+statuses[k].get('error','')} "
                    f"({statuses[k].get('minutes','?')} min)")
    finally:
        for k,v in saved.items():
            os.environ.pop(k,None) if v is None else os.environ.__setitem__(k,v)
    for k in [k for k in independent if not statuses[k]["ok"]]:  # retry alone, in this process
        log(f"{time.strftime('%H:%M')} retrying {k} in sequence")
        statuses[k] = run_case(k,cases[k],root,base,feeder,k in keep)
        log(f"{time.strftime('%H:%M')} {k}: {'ok' if statuses[k]['ok'] else 'FAILED '+statuses[k].get('error','')}")
    for k in dependent:
        source = cases[k]["recalibrate_from"]
        if not statuses.get(source,{}).get("ok",(root/source/"criterio.csv").exists()):
            statuses[k] = dict(case=k,ok=False,error=f"source case {source} failed")
            continue
        log(f"{time.strftime('%H:%M')} {k}: recalibrating on {source}")
        statuses[k] = run_case(k,cases[k],root,base,feeder,k in keep,calibration=recalibration(root,source))
        log(f"{time.strftime('%H:%M')} {k}: {'ok' if statuses[k]['ok'] else 'FAILED '+statuses[k].get('error','')} ({statuses[k].get('minutes')} min)")
    return statuses


# ------------------------------------------------------------------------------------------- report
def collect(root, base_dir, spec):
    """One row per case and scenario: criterion and the main feeder indicators."""
    rows = []
    folders = [("base",{"group": "base","description": "official configs (results/<feeder>/integration)"},Path(base_dir))]
    folders += [(k,v,Path(root)/k) for k,v in spec["cases"].items()]
    for name,case,folder in folders:
        if not (folder/"criterio.csv").exists():
            rows.append(dict(case=name,group=case["group"],description=case["description"],scenario="S4",status="no result"))
            continue
        crit = pd.read_csv(folder/"criterio.csv").set_index("scenario")
        summ = pd.read_csv(folder/"summary.csv").set_index("scenario")
        for sc in crit.index:
            taps = tap_operations({"source": pd.read_csv(folder/"dados"/f"{sc}_source.csv")})
            rows.append(dict(case=name,group=case["group"],description=case["description"],scenario=sc,status="ok",
                             **crit.loc[sc].to_dict(),v_max_pu=summ.v_max_pu[sc],peak_kw=summ.peak_import_kw[sc],
                             loss_mwh=summ.loss_kwh[sc]/1000,bess_kwh=summ.bess_throughput_ac_kwh[sc],v2g_kwh=summ.v2g_delivered_kwh[sc],
                             capex_usd=summ.capex_usd[sc],tap_operations=sum(taps.values()),**{f"taps_{k}": v for k,v in taps.items()},
                             **variant(case)))
    return pd.DataFrame(rows)


BASE_BAND_PU = 0.016667  # regulator band of the study (2 V on a 120 V base), network data
VARIANT_KEYS = {"group","description","seed","regulators","bess_scale","resolution_min"}


def variant(case):
    """Band (V), step (min) and BESS scale of a case, and whether it differs from the base only in these and the seed
    (the sets compared in the tap-operation table)."""
    regulators = case.get("regulators") or {}
    return dict(band_v=round(float(regulators.get("band_pu",BASE_BAND_PU))*120,1),step_min=int(case.get("resolution_min",60)),
                bess_scale=float(case.get("bess_scale",1.)),
                comparable=set(case) <= VARIANT_KEYS and set(regulators) <= {"band_pu"})


def _num(x, d=0):
    return "—" if pd.isna(x) else f"{x:,.{d}f}".replace(",","X").replace(".",",").replace("X",".")


def report(config_dir="configs", results="results"):
    feeder = study_feeder(config_dir)
    spec = yaml.safe_load((Path(config_dir)/"sensitivity.yaml").read_text(encoding="utf-8"))
    root = Path(results)/feeder/"sensitivity"
    t = collect(root,Path(results)/feeder/"integration",spec)
    t.to_csv(root/"sensitivity.csv",index=False)
    figures(t[t.scenario == "S4"],root)
    s4 = t[t.scenario == "S4"]
    doc = ["# Sensibilidade do S0–S4","",
           f"Gerado por `python -m integration.sensitivity` em {time.strftime('%Y-%m-%d %H:%M')}. Casos definidos antes de rodar em "
           "`configs/sensitivity.yaml`; todos aparecem aqui. Critério: nenhuma linha que estava até 100 % no S0 do próprio caso passa "
           "de 100 %, e tensão mínima ≥ 0,95 pu com os reguladores atuando. Tabela completa (S0–S4 de cada caso): `sensitivity.csv`.",""]
    for group in dict.fromkeys(s4.group):
        g = s4[s4.group == group]
        doc += [f"## {group.capitalize()}","",
                "| Caso | Descrição | S4: linhas novas > 100 % | Maior carga | Linha·h | Tensão mínima | Taps do S0 | Ponta (kW) | Critério |",
                "|---|---|---|---|---|---|---|---|---|"]
        for r in g.itertuples():
            if r.status != "ok":
                doc.append(f"| {r.case} | {r.description} | sem resultado | | | | | | |"); continue
            doc.append(f"| {r.case} | {r.description} | {int(r.new_overloads)} | {_num(r.new_max_pct,1)} % | {_num(r.new_line_hours,1)} | "
                       f"{_num(r.v_min_pu,3)} pu | {_num(r.v_min_frozen_pu,3)} pu | {_num(r.peak_kw)} | {'**atende**' if r.success else 'não atende'} |")
        doc.append("")
    seeds = t[(t.group == "sementes") & (t.status == "ok")]
    if len(seeds):
        doc += ["## Sementes: todos os cenários","","| Cenário | Atende (casos) | Linhas novas > 100 % (mín–máx) | Tensão mínima (mín–máx) |","|---|---|---|---|"]
        for sc,g in seeds.groupby("scenario"):
            doc.append(f"| {sc} | {int(g.success.sum())} de {len(g)} | {int(g.new_overloads.min())}–{int(g.new_overloads.max())} | "
                       f"{_num(g.v_min_pu.min(),3)}–{_num(g.v_min_pu.max(),3)} pu |")
        doc.append("")
    doc += tap_section(t)
    doc += ["## Figuras","","- `sensitivity_s4_new_overloads.png`: linhas novas acima de 100 % no S4, por caso.",
            "- `sensitivity_s4_min_voltage.png`: tensão mínima do S4 por caso, com os reguladores atuando e com os taps do S0.",""]
    (root/"RELATORIO.md").write_text("\n".join(doc),encoding="utf-8")
    return root/"RELATORIO.md"


def tap_table(t):
    """Tap operations of the day (sum over regulators of |tap step|) per scenario, for each set of comparable cases
    (same band, step and BESS scale; the seeds are the repetitions). Mean, min and max over the set."""
    ok = t[(t.status == "ok") & (t.comparable == True)]
    g = ok.groupby(["band_v","step_min","bess_scale","scenario"]).tap_operations
    return pd.DataFrame({"cases": g.size(),"mean": g.mean(),"min": g.min(),"max": g.max()}).reset_index()


def tap_section(t):
    tab = tap_table(t)
    if tab.empty:
        return []
    doc = ["## Operações de tap","",
           "Soma, nos 4 bancos, dos degraus de tap movidos no dia (`integration.impact.tap_operations`). Cada linha junta os casos "
           "que só diferem do base pela semente: média (mínimo–máximo). O passo de 15 min tem 4 vezes mais passos, então só se "
           "compara com o próprio passo.","",
           "| Banda | Passo | BESS | Casos | S0 | S1 | S2 | S3 | S4 |","|---|---|---|---|---|---|---|---|---|"]
    for (band,step,scale),g in tab.groupby(["band_v","step_min","bess_scale"]):
        cells = {r.scenario: f"{_num(r['mean'],1)} ({int(r['min'])}–{int(r['max'])})" if r.cases > 1 else f"{int(r['mean'])}"
                 for _,r in g.iterrows()}
        doc.append(f"| {_num(band,0)} V | {int(step)} min | × {_num(scale,2)} | {int(g.cases.max())} | "+" | ".join(cells.get(sc,"—") for sc in ("S0","S1","S2","S3","S4"))+" |")
    return doc+[""]


def figures(s4, root):
    import matplotlib
    matplotlib.use("Agg")
    from integration.s0_study import _style, new_axes, save_figures, MUTED
    from integration.impact import REGULATED, FROZEN
    s4 = s4[s4.status == "ok"]
    y = np.arange(len(s4))
    ax = new_axes((10,max(6,.32*len(s4))))
    _style(ax,"S4: new lines above 100 % per sensitivity case")
    ax.barh(y,s4.new_overloads,color=FROZEN)
    ax.set_yticks(y,s4.case,fontsize=8); ax.invert_yaxis(); ax.set_xlabel("lines (at most 100 % in the case's S0)",color=MUTED)
    fig1 = ax.figure
    ax = new_axes((10,max(6,.32*len(s4))))
    _style(ax,"S4: lowest voltage of the day per sensitivity case")
    ax.plot(s4.v_min_pu,y,"o",color=REGULATED,label="regulators acting")
    ax.plot(s4.v_min_frozen_pu,y,"s",color=FROZEN,label="taps held at S0")
    ax.axvline(.95,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_yticks(y,s4.case,fontsize=8); ax.invert_yaxis(); ax.set_xlabel("voltage (pu)",color=MUTED); ax.legend(frameon=False,fontsize=9)
    save_figures({"sensitivity_s4_new_overloads": fig1,"sensitivity_s4_min_voltage": ax.figure},root)


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--results",default="results")
    parser.add_argument("--cases",nargs="*")
    parser.add_argument("--workers",type=int)
    parser.add_argument("--report",action="store_true",help="only rebuild the report")
    args = parser.parse_args()
    if not args.report:
        statuses = run_all(args.configs,args.results,args.cases,args.workers,log=lambda m: print(m,flush=True))
        failed = [k for k,s in statuses.items() if not s["ok"]]
        print("failed:",failed or "none",flush=True)
    print(report(args.configs,args.results))


if __name__ == "__main__":
    main()
