"""Calibrate the BESS `line_limit` strategy from a saved S0-S4 integration run (no power flow, seconds).

python -m integration.bess_calibration results/ieee8500/s4_calibration
The BESS only receives the feeder demand (bess.runner.run_integrated contract), so each line is modelled as
  loading before the BESS = a + b * demand        (least squares over the S1 and S3 hours)
  loading change          = -s * BESS injection    (S2 - S1 and S4 - S3, regulator taps frozen at S0)
demand is what the BESS received (modules/bess/S2|S4/dispatch.csv, demand_before_kw); loading is the highest
physical phase of the hour. The taps are frozen for s so that only the network responds to the BESS.
Monitored lines (S4 decision: lines already above 100 % in S0 do not count): at most 100 % in S0, above
`candidate_pct` in S1 or S3, and s >= `min_sensitivity_pct_per_kw` (the BESS can reach them).
Each line gets margin = its largest |residual|, so the strategy keeps it at 100 % - margin: the prediction
error is covered line by line (target_pct stays at the 100 % limit).
Writes <run>/dados/bess_calibration.csv and <run>/line_limit.yaml (the block for configs/bess.yaml).
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import yaml
from core.schemas import read_parameters


def hourly_loading(path):
    """time x line table of the highest physical-phase loading (%)."""
    b = pd.read_csv(path,usecols=["time","line","element_type","physical_phase","loading_pct"])
    b = b[b.physical_phase & (b.element_type == "line")]
    return b.pivot_table(index="time",columns="line",values="loading_pct",aggfunc="max").sort_index()


def bess_dispatch(run, scenario):
    t = pd.read_csv(Path(run)/"modules"/"bess"/scenario/"dispatch.csv")
    return t.demand_before_kw.to_numpy(float), t.injection_kw.to_numpy(float)


def fit_lines(loading, demand):
    """Least-squares a, b and the residuals of loading = a + b*demand, per line. loading: rows = steps."""
    X = np.c_[np.ones(len(demand)),demand]
    coef,*_ = np.linalg.lstsq(X,loading.to_numpy(),rcond=None)
    residual = loading.to_numpy()-X@coef
    total = ((loading-loading.mean())**2).sum().to_numpy()
    r2 = 1-(residual**2).sum(axis=0)/np.where(total > 0,total,np.nan)
    return pd.DataFrame({"a": coef[0],"b": coef[1],"r2": r2,"max_abs_residual": np.abs(residual).max(axis=0)},
                        index=loading.columns)


def sensitivity(change, injection, min_kw=1.):
    """Slope s of change = -s*injection through the origin, per line, using the steps where the BESS acts."""
    acting = np.abs(injection) > min_kw
    p = injection[acting]
    return pd.Series(-(change.to_numpy()[acting]*p[:,None]).sum(axis=0)/(p**2).sum(),index=change.columns)


def calibrate(run, settings):
    run, dados = Path(run), Path(run)/"dados"
    s0 = hourly_loading(dados/"S0_branches.csv")
    s1, s3 = hourly_loading(dados/"S1_branches.csv"), hourly_loading(dados/"S3_branches.csv")
    d1, p2 = bess_dispatch(run,"S2")
    d3, p4 = bess_dispatch(run,"S4")
    fit = fit_lines(pd.concat([s1,s3],ignore_index=True),np.r_[d1,d3])
    frozen = {k: hourly_loading(dados/f"{k}_frozen_taps_branches.csv") for k in ("S1","S2","S3","S4")}
    change = pd.concat([frozen["S2"]-frozen["S1"],frozen["S4"]-frozen["S3"]],ignore_index=True)
    fit["s"] = sensitivity(change,np.r_[p2,p4])
    fit["s0_max_pct"], fit["s1_max_pct"], fit["s3_max_pct"] = s0.max(), s1.max(), s3.max()
    fit["monitored"] = ((fit.s0_max_pct <= 100) & (fit[["s1_max_pct","s3_max_pct"]].max(axis=1) > settings["candidate_pct"])
                        & (fit.s >= settings["min_sensitivity_pct_per_kw"]))
    return fit.sort_values("s1_max_pct",ascending=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run",help="integration output folder run with --frozen-taps and a BESS that acts in S2/S4")
    parser.add_argument("--configs",default="configs")
    args = parser.parse_args()
    settings = read_parameters(Path(args.configs)/"bess.yaml")["line_limit"]["calibration"]
    fit = calibrate(args.run,settings)
    fit.to_csv(Path(args.run)/"dados"/"bess_calibration.csv",index_label="line")
    chosen = fit[fit.monitored]
    block = {"lines": {line: {"a": round(float(r.a),6),"b": round(float(r.b),8),"s": round(float(r.s),8),
                              "margin": round(float(np.ceil(r.max_abs_residual*100)/100),2)} for line,r in chosen.iterrows()}}
    (Path(args.run)/"line_limit.yaml").write_text(yaml.safe_dump(block,sort_keys=False),encoding="utf-8")
    print(chosen[["s0_max_pct","s1_max_pct","s3_max_pct","a","b","s","r2","max_abs_residual"]].round(4).to_string())
    print(f"monitored lines: {len(chosen)}; margins {chosen.max_abs_residual.min():.2f}-{chosen.max_abs_residual.max():.2f} %")


if __name__ == "__main__":
    main()
