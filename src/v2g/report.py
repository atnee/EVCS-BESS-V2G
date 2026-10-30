"""S0, S1 and S3 side by side from a saved integration run (no power flow), with the owner economics.

python -m v2g.report results/ieee8500/integration_s3_prelim --configs <config folder of the run>
--configs must be the folder the run used (checked against manifest.json). Reads <folder>/dados/{S0,S1,S3}_*.csv (S1/S3 also *_frozen_taps_*; S0 is the reference for both views),
dados/S1_fleet.json, dados/S3_fleet.json and summary.csv. Writes <folder>/v2g/: one plot per figure
(v2g_*.png), v2g_s0_s1_s3.csv and v2g_economics.csv (S1 billed flat at the tariff_basis base price,
S3 billed with the cesar_v1 dynamic tariff; owner benefit per day, month and car, adapted from
Moreira et al., eq. (6)) and v2g_peak_value_indicative.csv (substation peak cut x demand charge;
indicative, not a charge). Not imported by v2g.runner, so the module stays independent of the network.
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


def fleet_economics(power_kw, dt_h, tariff_steps, remuneration_steps, flat_usd_kwh, days_per_month, n_vehicles):
    """Energy bill of a vehicles x steps grid-side power matrix (+ = discharge, - = charge).

    Dynamic: charging_cost - v2g_revenue (net_cost). Flat: every charged kWh at flat_usd_kwh (flat_cost).
    Both nets are also given per month (x days_per_month) and per car and month (/ n_vehicles)."""
    p = np.asarray(power_kw,float)
    charged = np.maximum(-p,0.).sum(axis=0)*dt_h
    discharged = np.maximum(p,0.).sum(axis=0)*dt_h
    cost = float(np.dot(np.asarray(tariff_steps,float),charged))
    revenue = float(np.dot(np.asarray(remuneration_steps,float),discharged))
    flat = float(flat_usd_kwh*charged.sum())
    return {"charged_kwh": float(charged.sum()), "discharged_kwh": float(discharged.sum()),
            "charging_cost_usd": cost, "v2g_revenue_usd": revenue, "net_cost_usd": cost-revenue,
            "flat_cost_usd": flat, "net_cost_month_usd": (cost-revenue)*days_per_month,
            "flat_cost_month_usd": flat*days_per_month,
            "net_cost_vehicle_month_usd": (cost-revenue)*days_per_month/n_vehicles,
            "flat_cost_vehicle_month_usd": flat*days_per_month/n_vehicles}


def base_usd_kwh(basis):
    return basis["base_hnl_kwh"]/basis["fx_hnl_per_usd"]


def economics_table(fleets, config, dt_h):
    """S1 billed flat at the base price, S3 with the cesar_v1 dynamic tariff; owner benefit = S1 - S3."""
    from core.schemas import TimeGrid
    from v2g.strategies import hourly_to_steps
    basis,prices = config["tariff_basis"],config["cesar_v1"]
    steps = len(fleets["S3"]["vehicle_power_kw"][0])
    grid = TimeGrid(steps=steps,dt_h=dt_h)
    tariff = hourly_to_steps(prices["tariff_usd_kwh"],grid,"tariff_usd_kwh")
    remuneration = hourly_to_steps(prices["v2g_remuneration_usd_kwh"],grid,"v2g_remuneration_usd_kwh")
    base,days = base_usd_kwh(basis),basis["days_per_month"]
    n = len(fleets["S3"]["vehicle_power_kw"])
    s1 = fleet_economics(fleets["S1"]["vehicle_power_kw"],dt_h,tariff,remuneration,base,days,n)
    s3 = fleet_economics(fleets["S3"]["vehicle_power_kw"],dt_h,tariff,remuneration,base,days,n)
    saved = fleets["S3"].get("charging_cost_usd"),fleets["S3"].get("v2g_revenue_usd")
    if None in saved or not np.isclose(s3["net_cost_usd"],saved[0]-saved[1],rtol=1e-9,atol=0):
        raise ValueError(f"S3 net cost {s3['net_cost_usd']} does not match S3_fleet.json {saved}")
    common = {"base_usd_kwh": base, "base_hnl_kwh": basis["base_hnl_kwh"], "fx_hnl_per_usd": basis["fx_hnl_per_usd"],
              "days_per_month": days, "n_vehicles": n}
    rows = [{"case": "S1", "billing": "flat", "charged_kwh": s1["charged_kwh"], "discharged_kwh": s1["discharged_kwh"],
             "charging_cost_usd": s1["flat_cost_usd"], "v2g_revenue_usd": 0., "net_cost_usd": s1["flat_cost_usd"]},
            {"case": "S3", "billing": "dynamic", "charged_kwh": s3["charged_kwh"], "discharged_kwh": s3["discharged_kwh"],
             "charging_cost_usd": s3["charging_cost_usd"], "v2g_revenue_usd": s3["v2g_revenue_usd"],
             "net_cost_usd": s3["net_cost_usd"]}]
    for row in rows:
        benefit = s1["flat_cost_usd"]-row["net_cost_usd"]
        row.update(owner_benefit_day_usd=benefit,owner_benefit_month_usd=benefit*days,
                   owner_benefit_vehicle_month_usd=benefit*days/n,**common)
    return pd.DataFrame(rows)


def peak_value_indicative(table, basis):
    """Substation peak cut S1 -> S3 valued at the medium-voltage demand charge: indicative, not a charge."""
    rate = basis["demand_value_hnl_kw_month"]/basis["fx_hnl_per_usd"]
    peaks = table[table.indicator == "substation_peak_kw"]
    return pd.DataFrame([{"label": "indicative, not a charge", "view": r.view, "peak_cut_kw": r.S1-r.S3,
                          "demand_value_usd_kw_month": rate, "value_usd_month": (r.S1-r.S3)*rate}
                         for r in peaks.itertuples()])


def tariff_figure(config):
    """Charging price and V2G payment per hour of the day (cesar_v1 vectors)."""
    from integration.s0_study import new_axes, _style, MUTED
    prices,window = config["cesar_v1"],config["discharge_window_h"]
    no_charge = prices["no_charge_window_h"]
    ax = new_axes((11,5.4))
    _style(ax,"S3 dynamic tariff: price to charge and payment for V2G discharge")
    hours,width = np.arange(24),.4
    ax.bar(hours-width/2,prices["tariff_usd_kwh"],width,color="#2a78d6",label="charging price")
    ax.bar(hours+width/2,prices["v2g_remuneration_usd_kwh"],width,color="#eb6834",label="V2G payment (discharge)")
    ax.axvspan(window[0]-.5,window[1]-.5,color="#2a78d6",alpha=.08,lw=0,zorder=0,
               label=f"discharge window {window[0]}-{window[1]} h")
    ax.axvspan(window[1]-.5,no_charge[1]-.5,color="#b9b8b3",alpha=.2,lw=0,zorder=0,
               label=f"no charging {no_charge[0]}-{no_charge[1]} h (discharge window included)")
    ax.set_xticks(hours,[f"{h:02d}:00" for h in hours],rotation=90,fontsize=8)
    ax.set_xlim(-.6,23.6); ax.set_xlabel("hour of day",color=MUTED); ax.set_ylabel("energy price (USD/kWh)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="upper left")
    return {"v2g_tariff": ax.figure}


def report(folder, config_dir="configs"):
    import matplotlib
    matplotlib.use("Agg")
    from integration.s0_study import save_figures
    folder = Path(folder)
    config = Path(config_dir)/"v2g.yaml"
    used = json.loads((folder/"manifest.json").read_text())["config_sha256"].get("v2g.yaml")
    if used != hashlib.sha256(config.read_bytes()).hexdigest():
        raise ValueError(f"{config} is not the v2g.yaml of this run (manifest.json); pass the run's --configs")
    parameters = read_parameters(config)
    window = parameters["discharge_window_h"]
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
    economics = economics_table(fleets,parameters,dt_h)
    economics.to_csv(out/"v2g_economics.csv",index=False)
    peak_value = peak_value_indicative(table,parameters["tariff_basis"])
    peak_value.to_csv(out/"v2g_peak_value_indicative.csv",index=False)
    save_figures({**network_figures(flows,slack,window),**fleet_figures(fleets,window,dt_h),**tariff_figure(parameters)},
                 out,clean=True)
    return table,economics,peak_value


# Host-station bus comparison (python -m v2g.report --compare A B ...): line groups by feeder path.
GROUPS = ("trunk_out","trunk_south","other")
BUS_CASES = ("S0","S1_A","S3_A","S1_B","S3_B")
BUS_COLORS = {"S0": "#b9b8b3", "S1_A": "#d03b3b", "S3_A": "#2a78d6", "S1_B": "#eb6834", "S3_B": "#1f9e89"}
BUS_DIFFS = (("S3_A","S1_A"),("S3_B","S1_A"),("S3_B","S3_A"),("S1_B","S1_A"))


def check_manifest(folder, config_dir):
    """Every YAML hashed in <folder>/manifest.json must match the file of config_dir."""
    used = json.loads((Path(folder)/"manifest.json").read_text())["config_sha256"]
    for name,digest in used.items():
        if hashlib.sha256((Path(config_dir)/name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"{Path(config_dir)/name} is not the {name} of {folder} (manifest.json)")


def path_lines(graph, start, end):
    """Element ids on the path start -> end, using closed elements only (open switches would add false loops)."""
    import networkx as nx
    closed = graph.edge_subgraph([(u,v) for u,v,d in graph.edges(data=True) if d.get("closed",True)])
    nodes = nx.shortest_path(closed,start,end)
    return [closed.edges[u,v]["id"] for u,v in zip(nodes,nodes[1:])]


def line_groups(graph, source, bus_a, bus_b):
    """{line id: group}: trunk_out = source -> bus_a, trunk_south = bus_a -> bus_b; other lines are absent."""
    groups = {line: "trunk_out" for line in path_lines(graph,source,bus_a)}
    for line in path_lines(graph,bus_a,bus_b):
        if line in groups:
            raise ValueError(f"{bus_b} is not downstream of {bus_a}: {line} is on both paths")
        groups[line] = "trunk_south"
    return groups


def group_of(lines, groups):
    return pd.Series([groups.get(line,"other") for line in lines],index=lines)


def group_metrics(loading, groups, dt_h):
    """Per group: max loading, hours over 100 % of its worst line, lines over 100 % and line-hours over 100 %."""
    group = group_of(loading.index,groups)
    out = {}
    for g in GROUPS:
        lines = loading[group == g]
        worst = lines.max(axis=1)
        over = lines > 100
        out[f"{g}_max_loading_pct"] = float(worst.max()) if len(lines) else np.nan
        out[f"{g}_worst_line_hours_over_100"] = float(over.loc[worst.idxmax()].sum()*dt_h) if len(lines) else np.nan
        out[f"{g}_lines_over_100"] = int(over.any(axis=1).sum())
        out[f"{g}_line_hours_over_100"] = float(over.values.sum()*dt_h)
    return out


def success_criterion(loading_s3, loading_s0, groups, reachable, hours):
    """Decision 11: per group, 'meets' if no line that is not already over 100 % in S0 exceeds 100 % in S3.
    Groups outside `reachable` are reported as not reachable from the host-station bus."""
    pre = set(loading_s0.index[(loading_s0 > 100).any(axis=1)])
    group = group_of(loading_s3.index,groups)
    rows = []
    for g in GROUPS:
        lines = loading_s3[(group == g) & ~loading_s3.index.isin(pre)]
        over = lines > 100
        steps = np.flatnonzero(over.any(axis=0).values)
        worst = lines.max(axis=1)
        meets = not len(steps)
        rows.append({"group": g, "reachable": g in reachable,
                     "status": ("meets" if meets else "fails") if g in reachable else "not reachable from the bus",
                     "meets": meets, "preexisting_excluded": len(pre & set(loading_s3.index[group == g])),
                     "lines_over_100": int(over.any(axis=1).sum()),
                     "max_excess_pct": 0. if meets else float(worst.max()-100),
                     "worst_line": "" if meets else str(worst.idxmax()),
                     "hours_over_100": " ".join(f"{hours[k]:g}" for k in steps)})
    return pd.DataFrame(rows)


def new_overloads(loading_s1, loading_s3, hours, window):
    """Line-hours over 100 % in S3 but not in S1 outside the discharge window [start, end)."""
    lines = loading_s3.index.intersection(loading_s1.index)
    s1,s3 = loading_s1.loc[lines].values,loading_s3.loc[lines].values
    outside = (np.asarray(hours) < window[0]) | (np.asarray(hours) >= window[1])
    new = (s3 > 100) & ~(s1 > 100) & outside
    return pd.DataFrame([{"line": lines[i], "hour": float(hours[k]), "S1_pct": float(s1[i,k]), "S3_pct": float(s3[i,k])}
                         for i,k in zip(*np.nonzero(new))],columns=["line","hour","S1_pct","S3_pct"])


def bus_comparison_table(flows, slack, groups, delivered):
    """Rows: indicator and view; columns: BUS_CASES and the BUS_DIFFS differences."""
    rows = []
    for view in VIEWS:
        metrics = {}
        for case in BUS_CASES:
            flow = flows[case,"regulated" if case == "S0" else view]
            _,dt = step_hours(flow)
            m = case_metrics(flow,slack)
            metrics[case] = {"substation_peak_kw": m["substation_peak_kw"], "substation_peak_hour": m["substation_peak_hour"],
                             **group_metrics(line_loading(flow),groups,dt),
                             "v_min_pu": m["v_min_pu"], "customers_below_095": m["customers_below_095"],
                             "losses_kwh": m["losses_kwh"]}
        for key in metrics["S0"]:
            rows.append({"indicator": key, "view": view, **{case: metrics[case][key] for case in BUS_CASES}})
    rows.append({"indicator": "v2g_delivered_kwh", "view": "module", **{case: float(delivered[case]) for case in BUS_CASES}})
    table = pd.DataFrame(rows)
    for a,b in BUS_DIFFS:
        table[f"{a} - {b}"] = table[a]-table[b]
    return table


def bus_figures(flows, slack, groups, window):
    from integration.s0_study import new_axes, _style
    figures = {}
    ax = new_axes((10,5.2))
    _style(ax,"Substation active power: S1 and S3 with the host station at m1125976 (A) or m1069504 (B)")
    _shade(ax,window)
    for case in ("S1_A","S3_A","S3_B"):
        _stairs(ax,flows[case,"regulated"],flows[case,"regulated"]["source"].p_kw,color=BUS_COLORS[case],lw=1.8,label=case)
    _day_axis(ax,"active power (kW)"); ax.legend(frameon=False,fontsize=9)
    figures["v2g_bus_substation_power"] = ax.figure
    labels = {"trunk_out": "outgoing trunk (substation to m1125976)", "trunk_south": "south trunk (m1125976 to m1069504)",
              "other": "other lines"}
    for g in GROUPS:
        ax = new_axes((10,5.2))
        _style(ax,f"Worst line loading per hour, {labels[g]}")
        _shade(ax,window)
        for case in ("S1_A","S3_A","S1_B","S3_B"):
            for view,style in (("regulated","-"),("frozen_taps","--")):
                loading = line_loading(flows[case,view])
                worst = loading[group_of(loading.index,groups) == g].max(axis=0)
                _stairs(ax,flows[case,view],worst,color=BUS_COLORS[case],lw=1.5,ls=style,label=f"{case}, {VIEWS[view]}")
        ax.axhline(100,color="#0b0b0b",lw=.8,ls=":")
        _day_axis(ax,"loading, highest physical phase (%)"); ax.legend(frameon=False,fontsize=7,ncol=2)
        figures[f"v2g_bus_line_groups_{g}"] = ax.figure
    ax = new_axes((10,5.2))
    _style(ax,"Minimum bus voltage per hour: host station at m1125976 (A) or m1069504 (B)")
    _shade(ax,window)
    for case in BUS_CASES:
        for view,style in (("regulated","-"),("frozen_taps","--")):
            if case == "S0" and view == "frozen_taps":
                continue
            v = bus_min_voltage(flows[case,view],slack).groupby("time").min()
            _stairs(ax,flows[case,view],v,color=BUS_COLORS[case],lw=1.5,ls=style,
                    label=f"{case}, {VIEWS[view]}" if case != "S0" else "S0")
    ax.axhline(V_MIN,color="#0b0b0b",lw=.8,ls=":")
    _day_axis(ax,"minimum voltage (pu)"); ax.legend(frameon=False,fontsize=7,ncol=2)
    figures["v2g_bus_min_voltage"] = ax.figure
    return figures


def compare_buses(folder_a, folder_b, configs_a, configs_b, output):
    """S1/S3 of both host-station buses against the same S1 reference (S1_A)."""
    import matplotlib
    matplotlib.use("Agg")
    from integration.s0_study import save_figures
    from network.feeder_loader import read_feeder_data
    from network.topology import feeder_graph
    folder_a,folder_b,output = Path(folder_a),Path(folder_b),Path(output)
    check_manifest(folder_a,configs_a); check_manifest(folder_b,configs_b)
    bus_a = read_parameters(Path(configs_a)/"evcs.yaml")["station"]["bus"]
    bus_b = read_parameters(Path(configs_b)/"evcs.yaml")["station"]["bus"]
    window = read_parameters(Path(configs_a)/"v2g.yaml")["discharge_window_h"]
    if read_parameters(Path(configs_b)/"v2g.yaml")["discharge_window_h"] != window:
        raise ValueError("Runs A and B use different discharge windows")
    data = read_feeder_data()
    groups = line_groups(feeder_graph(data),data["source_bus"],bus_a,bus_b)
    slack = str(pd.read_csv(folder_a/"dados"/"S0_injections.csv").bus.unique()[0])
    sources = {"S0": (folder_a,"S0"), "S1_A": (folder_a,"S1"), "S3_A": (folder_a,"S3"),
               "S1_B": (folder_b,"S1"), "S3_B": (folder_b,"S3")}
    flows = {(case,view): load_case(folder,scenario,view) for case,(folder,scenario) in sources.items() for view in VIEWS}
    delivered = {case: pd.read_csv(folder/"summary.csv").set_index("scenario").v2g_delivered_kwh[scenario]
                 for case,(folder,scenario) in sources.items()}
    output.mkdir(parents=True,exist_ok=True)
    table = bus_comparison_table(flows,slack,groups,delivered)
    table.to_csv(output/"s3_bus_comparison.csv",index=False)
    reachable = {"S3_A": ("trunk_out",), "S3_B": ("trunk_out","trunk_south")}
    success,overloads = [],[]
    for view in VIEWS:
        hours,_ = step_hours(flows["S0","regulated"])
        s0 = line_loading(flows["S0","regulated"])
        for case,ok in reachable.items():
            s3 = line_loading(flows[case,view])
            success.append(success_criterion(s3,s0,groups,ok,hours).assign(case=case,view=view))
            overloads.append(new_overloads(line_loading(flows["S1"+case[2:],view]),s3,hours,window)
                             .assign(case=case,reference="S1"+case[2:],view=view))
    success = pd.concat(success,ignore_index=True)[["case","view","group","reachable","status","meets",
                                                    "preexisting_excluded","lines_over_100","max_excess_pct",
                                                    "worst_line","hours_over_100"]]
    success.to_csv(output/"s3_success.csv",index=False)
    overloads = pd.concat(overloads,ignore_index=True)[["case","reference","view","line","hour","S1_pct","S3_pct"]]
    overloads.to_csv(output/"s3_new_overloads_outside_window.csv",index=False)
    pd.Series(groups,name="group").rename_axis("line").to_csv(output/"s3_line_groups.csv")
    save_figures(bus_figures(flows,slack,groups,window),output,clean=True)
    return table,success,overloads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",nargs="?",help="integration output folder (with dados/ and summary.csv)")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--compare",nargs=2,metavar=("FOLDER_A","FOLDER_B"),
                        help="compare two runs that differ only in the host-station bus")
    parser.add_argument("--configs-a"); parser.add_argument("--configs-b"); parser.add_argument("--output")
    args = parser.parse_args()
    if args.compare:
        if not (args.configs_a and args.configs_b and args.output):
            parser.error("--compare needs --configs-a, --configs-b and --output")
        tables = compare_buses(*args.compare,args.configs_a,args.configs_b,args.output)
    elif args.folder:
        tables = report(args.folder,args.configs)
    else:
        parser.error("give a run folder or --compare")
    for table in tables:
        print(table.to_string(index=False),end="\n\n")


if __name__ == "__main__":
    main()
