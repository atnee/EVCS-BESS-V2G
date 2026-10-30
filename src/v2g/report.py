"""S0, S1 and S3 side by side from a saved integration run (no power flow).

python -m v2g.report results/ieee8500/integration_s3_prelim --configs <config folder of the run>
--configs must be the folder the run used (checked against manifest.json). Reads <folder>/dados/{S0,S1,S3}_*.csv (S1/S3 also *_frozen_taps_*; S0 is the reference for both views),
dados/S1_fleet.json, dados/S3_fleet.json and summary.csv. Writes <folder>/v2g/: one plot per figure
(v2g_*.png) and v2g_s0_s1_s3.csv. Not imported by v2g.runner, so the module stays independent of the network.
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from core.schemas import read_parameters

CASES = ("S0","S1","S3")
CRITICAL_LINES = {"ln5513564-1": "outgoing trunk", "ln5835160-1": "south trunk", "ln6351510-1": "#4 ACSR"}
V_MIN = .95                 # ANSI C84.1 range A (not the Honduran regulation)
COLORS = {"S0": "#b9b8b3", "S1": "#d03b3b", "S3": "#2a78d6"}
VIEWS = {"regulated": "regulators acting", "frozen_taps": "taps held at S0 values"}
TOP_LINES = 15


def load_case(folder, case, view="regulated"):
    """Solver tables of one case and view; S0 has no frozen-tap solve (it is the reference)."""
    prefix = f"{case}_" if view == "regulated" or case == "S0" else f"{case}_frozen_taps_"
    flow = {name: pd.read_csv(folder/"dados"/f"{prefix}{name}.csv") for name in ("voltages","branches","source")}
    flow["native_loads"] = pd.read_csv(folder/"dados"/f"{case}_native_loads.csv")
    for table in flow.values():
        table["time"] = pd.to_datetime(table.time)
    return flow


def step_hours(flow):
    steps = len(flow["source"])
    return np.arange(steps)*24/steps, 24/steps


def line_loading(flow):
    """Highest physical-phase loading per line and step (lines only), as a lines x steps table."""
    b = flow["branches"]
    b = b[b.physical_phase & (b.element_type == "line")]
    return b.groupby(["line","time"]).loading_pct.max().unstack("time")


def bus_min_voltage(flow, slack):
    v = flow["voltages"]
    return v[v.bus != slack].groupby(["time","bus"]).v_pu.min()


def case_metrics(flow, slack):
    hours,dt = step_hours(flow)
    source = flow["source"]
    peak = int(source.p_kw.idxmax())
    lines = line_loading(flow)
    worst = lines.max(axis=1)
    v = bus_min_voltage(flow,slack)
    below = v[v < V_MIN].index.get_level_values("bus").unique()
    loads = flow["native_loads"].drop_duplicates("asset")
    m = {"substation_peak_kw": float(source.p_kw.iloc[peak]), "substation_peak_hour": float(hours[peak]),
         "max_line_loading_pct": float(worst.max()), "max_line": str(worst.idxmax()),
         "v_min_pu": float(v.min()), "customers_below_095": int(loads.bus.isin(below).sum()),
         "losses_kwh": float(flow["branches"].loss_kw.sum()*dt)}
    for line in CRITICAL_LINES:
        m[f"hours_over_100_{line}"] = float((lines.loc[line] > 100).sum()*dt) if line in lines.index else np.nan
    return m


def comparison_table(flows, slack, summary):
    """Rows: indicator and view; columns: S0, S1, S3, S3 - S1, S3 - S0."""
    rows = []
    for view in VIEWS:
        metrics = {case: case_metrics(flows[case,view],slack) for case in CASES}
        for key in metrics["S0"]:
            row = {"indicator": key, "view": view, **{case: metrics[case][key] for case in CASES}}
            if isinstance(row["S0"],str):
                row.update({"S3 - S1": "", "S3 - S0": ""})
            else:
                row.update({"S3 - S1": row["S3"]-row["S1"], "S3 - S0": row["S3"]-row["S0"]})
            rows.append(row)
    delivered = summary.set_index("scenario").v2g_delivered_kwh
    rows.append({"indicator": "v2g_delivered_kwh", "view": "module", **{case: float(delivered[case]) for case in CASES},
                 "S3 - S1": float(delivered["S3"]-delivered["S1"]), "S3 - S0": float(delivered["S3"]-delivered["S0"])})
    return pd.DataFrame(rows)


def _stairs(ax, flow, values, **kwargs):
    hours,dt = step_hours(flow)
    ax.stairs(np.asarray(values,float),np.append(hours,24),baseline=None,**kwargs)


def _shade(ax, window):
    ax.axvspan(*window,color="#2a78d6",alpha=.08,lw=0,label=f"V2G discharge window {window[0]}-{window[1]} h")


def _day_axis(ax, ylabel):
    from integration.s0_study import MUTED
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3))
    ax.set_xlabel("hour of day",color=MUTED); ax.set_ylabel(ylabel,color=MUTED)


def network_figures(flows, slack, window):
    from integration.s0_study import new_axes, _style
    figures = {}
    ax = new_axes((10,5.2))
    _style(ax,"Substation active power: S0, S1 and S3 (regulators acting)")
    _shade(ax,window)
    for case in CASES:
        _stairs(ax,flows[case,"regulated"],flows[case,"regulated"]["source"].p_kw,color=COLORS[case],lw=1.8,label=case)
    _day_axis(ax,"active power (kW)"); ax.legend(frameon=False,fontsize=9)
    figures["v2g_substation_power"] = ax.figure
    for line,label in CRITICAL_LINES.items():
        ax = new_axes((10,5.2))
        _style(ax,f"Loading of {line} ({label}): S0, S1 and S3")
        _shade(ax,window)
        for case in CASES:
            for view,style in (("regulated","-"),("frozen_taps","--")):
                if case == "S0" and view == "frozen_taps":
                    continue
                lines = line_loading(flows[case,view])
                if line in lines.index:
                    _stairs(ax,flows[case,view],lines.loc[line],color=COLORS[case],lw=1.6,ls=style,
                            label=f"{case}, {VIEWS[view]}" if case != "S0" else "S0")
        ax.axhline(100,color="#0b0b0b",lw=.8,ls=":")
        _day_axis(ax,"loading, highest physical phase (%)"); ax.legend(frameon=False,fontsize=8)
        figures[f"v2g_line_loading_critical_{line}"] = ax.figure
    s1 = flows["S1","regulated"]
    peak = s1["source"].time.iloc[int(s1["source"].p_kw.idxmax())]
    top = line_loading(s1)[peak].nlargest(TOP_LINES).index
    ax = new_axes((11,5.6))
    _style(ax,f"The {TOP_LINES} most loaded lines at the S1 peak ({peak:%H:%M}), regulators acting")
    x,width = np.arange(len(top)),.27
    for k,case in enumerate(CASES):
        ax.bar(x+(k-1)*width,line_loading(flows[case,"regulated"]).loc[top,peak],width,color=COLORS[case],label=case)
    ax.axhline(100,color="#0b0b0b",lw=.8,ls=":")
    ax.set_xticks(x,top,rotation=60,ha="right",fontsize=8); ax.set_ylabel("loading, highest physical phase (%)")
    ax.legend(frameon=False,fontsize=9)
    figures["v2g_line_loading_top"] = ax.figure
    ax = new_axes((10,5.2))
    _style(ax,"Minimum bus voltage per hour: S0, S1 and S3")
    _shade(ax,window)
    for case in CASES:
        for view,style in (("regulated","-"),("frozen_taps","--")):
            if case == "S0" and view == "frozen_taps":
                continue
            v = bus_min_voltage(flows[case,view],slack).groupby("time").min()
            _stairs(ax,flows[case,view],v,color=COLORS[case],lw=1.6,ls=style,
                    label=f"{case}, {VIEWS[view]}" if case != "S0" else "S0")
    ax.axhline(V_MIN,color="#0b0b0b",lw=.8,ls=":")
    _day_axis(ax,"minimum voltage (pu)"); ax.legend(frameon=False,fontsize=8)
    figures["v2g_min_voltage_day"] = ax.figure
    return figures


def fleet_figures(fleets, window, dt_h):
    """Net fleet power by profile (S3, S1 total as reference) and total fleet energy."""
    from integration.s0_study import new_axes, _style
    figures = {}
    ax = new_axes((10,5.2))
    _style(ax,"V2G fleet net power by profile (positive = discharge to the grid)")
    _shade(ax,window)
    edges = np.arange(len(fleets["S3"]["vehicle_power_kw"][0])+1)*dt_h
    for case,style in (("S3","-"),("S1","--")):
        meta = fleets[case]
        p,ids = np.array(meta["vehicle_power_kw"]),list(meta["departure_energy_kwh"])
        # Total first and wider, so a profile that equals it stays visible on top.
        ax.stairs(p.sum(axis=0),edges,baseline=None,color="#0b0b0b",lw=2.6 if case == "S3" else 1.4,ls=style,
                  alpha=.35 if case == "S3" else 1.,label=f"{case} fleet total")
        if case == "S3":
            for profile,color in (("residential","#2a78d6"),("commercial","#eb6834")):
                rows = [i for i,v in enumerate(ids) if v.startswith(profile)]
                ax.stairs(p[rows].sum(axis=0),edges,baseline=None,color=color,lw=1.6,label=f"S3 {profile}")
    ax.axhline(0,color="#52514e",lw=.8)
    _day_axis(ax,"net power (kW)"); ax.legend(frameon=False,fontsize=8)
    figures["v2g_fleet_power_by_profile"] = ax.figure
    ax = new_axes((10,5.2))
    _style(ax,"Energy stored in the V2G fleet (cyclic day)")
    _shade(ax,window)
    for case,style in (("S3","-"),("S1","--")):
        e = np.array(fleets[case]["energy_kwh"]).sum(axis=0)
        ax.plot(np.arange(len(e))*dt_h,e,color=COLORS[case],lw=1.8,ls=style,label=case)
    _day_axis(ax,"fleet energy at step boundaries (kWh)"); ax.legend(frameon=False,fontsize=9)
    figures["v2g_fleet_energy"] = ax.figure
    return figures


def report(folder, config_dir="configs"):
    import matplotlib
    matplotlib.use("Agg")
    from integration.s0_study import save_figures
    folder = Path(folder)
    config = Path(config_dir)/"v2g.yaml"
    used = json.loads((folder/"manifest.json").read_text())["config_sha256"].get("v2g.yaml")
    if used != hashlib.sha256(config.read_bytes()).hexdigest():
        raise ValueError(f"{config} is not the v2g.yaml of this run (manifest.json); pass the run's --configs")
    window = read_parameters(config)["discharge_window_h"]
    # S0 injects nothing but the zero profile at the slack bus, so its only bus is the slack.
    slack = pd.read_csv(folder/"dados"/"S0_injections.csv").bus.unique()
    if len(slack) != 1:
        raise ValueError("S0_injections.csv should hold the slack bus only")
    slack = str(slack[0])
    flows = {(case,view): load_case(folder,case,view) for case in CASES for view in VIEWS}
    fleets = {case: json.loads((folder/"dados"/f"{case}_fleet.json").read_text()) for case in ("S1","S3")}
    summary = pd.read_csv(folder/"summary.csv")
    out = folder/"v2g"
    out.mkdir(exist_ok=True)
    table = comparison_table(flows,slack,summary)
    table.to_csv(out/"v2g_s0_s1_s3.csv",index=False)
    _,dt_h = step_hours(flows["S3","regulated"])
    save_figures({**network_figures(flows,slack,window),**fleet_figures(fleets,window,dt_h)},out,clean=True)
    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",help="integration output folder (with dados/ and summary.csv)")
    parser.add_argument("--configs",default="configs")
    args = parser.parse_args()
    print(report(args.folder,args.configs).to_string(index=False))


if __name__ == "__main__":
    main()
