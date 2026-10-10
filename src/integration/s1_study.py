"""S1 study: hubs and eletropostos from the screening, minute-level charging sessions and an
intraday power flow (planning.resolution_min, 15 min by default) compared with S0 at the same resolution.

python -m integration.s1_study                     # fleets: every fleet of the screening (sensitivity 2000-5000)
python -m integration.s1_study --evs 2000 5000
python -m integration.s1_study --intraday-only      # redraw intraday_sensitivity.png from saved results
Reads results/<feeder>/evcs_screening/ (python -m integration.evcs_screening) and writes
results/<feeder>/s1/; the feeder comes from configs/network.yaml.
"""
from pathlib import Path
import argparse
import shutil
import dataclasses
import json
import numpy as np
import pandas as pd
from core.schemas import make_profile, read_parameters
from evcs.planning import from_config, with_fleet, simulate, resample
from network.pandapower_solver import PandapowerSolver
from integration.coordinator import combine
from integration.scenarios import integrated_grid, study_feeder
from integration.impact import impact_figures, impact_summary, voltage_change, network_state_figures, flow_from_csv
from integration.evcs_screening import plot_sites, TYPE_STYLE, study_dir
from integration.s0_study import (run_s0, feeder_voltages, plot_voltage_envelope, new_axes, save_figures, marker_scale,
                                  _style, _edges, INK, MUTED, SURFACE, V_LIMITS)

S0_GRAY, S1_RED = "#b9b8b3", "#d03b3b"


def load_fleets(screening_dir):
    fleets = pd.read_csv(Path(screening_dir)/"dados"/"fleet_scenarios.csv")
    fleets["sites"] = fleets.sites.apply(json.loads)
    fleets["site_phases"] = (fleets.site_phases if "site_phases" in fleets else pd.Series("{}",index=fleets.index)
                             ).fillna("{}").apply(json.loads)  # phase of each single-phase site
    return fleets.set_index("evs")


def run_s1(evs, config_dir="configs", screening_dir=None, s0=None, sites=None, phases=None):
    params = with_fleet(from_config(read_parameters(Path(config_dir)/"evcs.yaml")),evs)
    screening_dir = screening_dir or study_dir(config_dir)
    row = load_fleets(screening_dir).loc[int(evs)]
    sites = row.sites if sites is None else sites
    phases = row.site_phases if phases is None else phases
    res = params.resolution_min
    s0 = run_s0(config_dir,res,study_feeder(config_dir)) if s0 is None else s0
    sessions,power = simulate(params,sites)
    network = s0["network"]
    grid = dataclasses.replace(integrated_grid(config_dir),steps=1440//res,dt_h=res/60)
    profiles = [make_profile(grid,"external-zero",network.slack_bus,"ABC",np.zeros(grid.steps))]
    profiles += [make_profile(grid,f"{sites[bus]}-{bus}",bus,phases.get(bus,"ABC"),-resample(kw,res)) for bus,kw in power.items()]
    profile = combine(profiles,network)
    flow = PandapowerSolver(s0["multipliers"]).solve(network,profile)
    # Same stations with the regulator taps of S0: the stations' own effect, without the regulators' response.
    frozen = PandapowerSolver(s0["multipliers"],fixed_taps=s0["flow"]["source"]).solve(network,profile)
    times = flow["source"].time
    study = dict(s0,flow=flow,peak=times[int(flow["source"].p_kw.idxmax())],valley=times[int(flow["source"].p_kw.idxmin())])
    return dict(study=study,s0=s0,frozen=frozen,params=params,sites=sites,phases=phases,sessions=sessions,power=power,evs=int(evs))


def delta_v(s1):
    """S1 - S0 voltage per bus, phase and step (negative = voltage dropped)."""
    a, b = feeder_voltages(s1["s0"]), feeder_voltages(s1["study"])
    d = a.merge(b,on=["time","bus","phase"],suffixes=("_s0","_s1"))
    d["dv_pu"] = d.v_pu_s1-d.v_pu_s0
    return d.merge(s1["study"]["buses"][["bus","distance_km","x","y"]],on="bus")


def by_type(s1, resolution_min):
    """Grid power per station type at a given resolution (kW)."""
    out = {}
    for bus,kw in s1["power"].items():
        key = s1["sites"][bus]
        out[key] = out.get(key,0)+resample(kw,resolution_min)
    return out


def energy_kwh(study):
    """Daily energy of the substation and of the line/transformer losses, any resolution."""
    dt_h = 24/len(study["flow"]["source"])
    return float(study["flow"]["source"].p_kw.sum()*dt_h), float(study["flow"]["branches"].loss_kw.sum()*dt_h)


def _time_axis(ax):
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3)); ax.set_xlabel("hour of day",color=MUTED)


def plot_allocation(s1, ax=None):
    ax = ax or new_axes((11,8.5))
    n = {k: sum(v==k for v in s1["sites"].values()) for k in ("dc","ac","ac1")}
    buses = s1["study"]["buses"]; load = buses[buses.load_kw > 0]
    return plot_sites(ax,s1["study"]["graph"],s1["sites"],s1["params"],
                      f"{s1['evs']} EVs: {n['dc']} DC hub(s), {n['ac']} three-phase and {n['ac1']} single-phase AC stations",
                      load[["x","y"]].assign(weight=load.load_kw))


def plot_ev_demand_hourly(s1, ax=None):
    """Hourly EV demand per station type, stacked."""
    ax = ax or new_axes((10,5))
    _style(ax,"EV charging demand per station type (hourly average)")
    hours, bottom = np.arange(24)+.5, np.zeros(24)
    for key,kw in by_type(s1,60).items():
        st = TYPE_STYLE[key]
        ax.bar(hours,kw,.85,bottom=bottom,color=st["color"],label=st["label"],edgecolor=SURFACE,linewidth=1)
        bottom += kw
    _time_axis(ax); ax.set_ylabel("kW",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


def plot_substation_hourly(s1, ax=None):
    """Hourly substation active power, S0 vs S1."""
    ax = ax or new_axes((10,5))
    _style(ax,"Substation active power, S0 vs S1 (hourly average)")
    hours = np.arange(24)+.5
    for name,study,color,marker in (("S0 (no EVs)",s1["s0"],S0_GRAY,"o"),("S1 (with EVs)",s1["study"],S1_RED,"v")):
        p = study["flow"]["source"].p_kw.to_numpy()
        ax.plot(hours,resample(np.repeat(p,1440//len(p)),60),color=color,lw=2,marker=marker,ms=4,label=name)
    _time_axis(ax); ax.set_ylabel("kW",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


def plot_ev_demand_intraday(s1, ax=None):
    """Total EV demand at 1 min, at the power-flow resolution and at 1 h, plus each station type."""
    res = s1["params"].resolution_min
    total = sum(s1["power"].values())
    ax = ax or new_axes((12,5.4))
    _style(ax,f"EV charging demand over the day (peak {total.max():.0f} kW at 1 min, "
              f"{resample(total,res).max():.0f} kW at {res} min, {resample(total,60).max():.0f} kW at 1 h)")
    ax.plot(np.arange(1440)/60,total,color="#86b6ef",lw=.8,label="total, 1 min")
    ax.step(np.arange(1440//res)*res/60,resample(total,res),where="post",color="#1c5cab",lw=2,label=f"total, {res} min")
    ax.step(np.arange(24),resample(total,60),where="post",color=INK,lw=1.4,ls=(0,(4,2)),label="total, 1 h")
    for key,kw in by_type(s1,res).items():
        ax.step(np.arange(len(kw))*res/60,kw,where="post",color=TYPE_STYLE[key]["color"],lw=1.2,alpha=.9,
                label=f'{TYPE_STYLE[key]["label"]} ({res} min)')
    _time_axis(ax); ax.set_ylabel("kW",color=MUTED); ax.legend(frameon=False,fontsize=8.5,ncol=3,loc="upper left")
    return ax


def plot_substation_intraday(s1, ax=None):
    """Substation active power at the power-flow resolution, S0 vs S1."""
    res = s1["params"].resolution_min
    ax = ax or new_axes((12,5))
    _style(ax,f"Substation active power, S0 vs S1 ({res} min)")
    for name,study,color in (("S0 (no EVs)",s1["s0"],S0_GRAY),("S1 (with EVs)",s1["study"],S1_RED)):
        p = study["flow"]["source"].p_kw.to_numpy()
        ax.step(np.arange(len(p))*24/len(p),p,where="post",color=color,lw=2,label=name)
    _time_axis(ax); ax.set_ylabel("kW",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


def plot_min_voltage_intraday(s1, ax=None):
    """Lowest feeder voltage at each step, S0 vs S1."""
    res = s1["params"].resolution_min
    ax = ax or new_axes((12,5))
    _style(ax,f"Lowest feeder voltage at each step, S0 vs S1 ({res} min)")
    for name,study,color in (("S0 (no EVs)",s1["s0"],S0_GRAY),("S1 (with EVs)",s1["study"],S1_RED)):
        v = feeder_voltages(study).groupby("time").v_pu.min().to_numpy()
        ax.step(np.arange(len(v))*24/len(v),v,where="post",color=color,lw=2.2,label=name)
    for limit in V_LIMITS:
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    _time_axis(ax); ax.set_ylabel("voltage (pu)",color=MUTED); ax.legend(frameon=False,fontsize=9,loc="upper right")
    return ax


def intraday_table(flow, slack, exclude=()):
    """Per step: substation P, minimum feeder voltage, highest line loading (lines in `exclude`,
    the base-case overloads, left out), highest regulator/transformer loading and regulator taps."""
    v = flow["voltages"]
    b = flow["branches"]
    b = b[b.physical_phase]
    lines = b[(b.element_type=="line") & ~b.line.isin(list(exclude))].groupby("time").loading_pct.max()
    equipment = b[b.element_type=="trafo"].groupby("time").loading_pct.max()
    source = flow["source"].set_index("time")
    table = pd.DataFrame({"p_kw": source.p_kw,"v_min_pu": v[v.bus!=slack].groupby("time").v_pu.min(),
                          "line_max_pct": lines,"equipment_max_pct": equipment})
    taps = source.filter(like="tap_")
    return table.join(taps).reset_index()


def base_overloads(flow):
    """Lines above 100 % at some step of the base case (reported, not attributed to the stations)."""
    b = flow["branches"]
    b = b[b.physical_phase & (b.element_type=="line")]
    return sorted(b[b.loading_pct > 100].line.unique())


FLEET_COLORS = ["#9ec5f4","#5598e6","#2a6cc0","#123f7a","#0b2447"]  # light → dark = more cars


def _fleet_tables(output):
    output = Path(output)
    s0 = pd.read_csv(output/"dados"/"s0_intraday.csv")
    fleets = sorted(int(d.name.split("_")[1]) for d in output.glob("evs_*") if (d/"dados"/"intraday.csv").exists())
    colors = FLEET_COLORS[-len(fleets):] if len(fleets) <= len(FLEET_COLORS) else FLEET_COLORS*9
    return s0,[(evs,color,output/f"evs_{evs}") for evs,color in zip(fleets,colors)]


def sensitivity_figures(output):
    """Intraday curves of every fleet (evs_<n>/) against S0, one plot per quantity: {file name: Figure}."""
    s0, fleets = _fleet_tables(output)
    hours = np.arange(len(s0))*24/len(s0)
    figures = {}
    specs = [("sensitivity_ev_demand","EV charging demand of each fleet (15 min)","kW",None),
             ("sensitivity_substation_power","Substation active power, S0 and each fleet (15 min)","kW","p_kw"),
             ("sensitivity_min_voltage","Lowest feeder voltage, S0 and each fleet (15 min)","voltage (pu)","v_min_pu"),
             ("sensitivity_line_loading","Most loaded line, S0 and each fleet (15 min, base-case overloads left out)",
              "loading (%)","line_max_pct")]
    for name,title,ylabel,key in specs:
        ax = new_axes((12,5))
        _style(ax,title)
        if key:
            ax.step(hours,s0[key],where="post",color=S0_GRAY,lw=2.6,label="S0 (no EVs)")
        for evs,color,d in fleets:
            if key:
                y = pd.read_csv(d/"dados"/"intraday.csv")[key]
                ax.step(hours,y,where="post",color=color,lw=1.6,label=f"{evs} EVs")
            else:
                y = pd.read_csv(next((d/"dados").glob("station_power_*min.csv"))).drop(columns="hour").sum(axis=1)
                ax.step(np.arange(len(y))*24/len(y),y,where="post",color=color,lw=1.8,label=f"{evs} EVs")
        if key == "v_min_pu":
            for limit in V_LIMITS:
                ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
        if key == "line_max_pct":
            ax.axhline(100,color=MUTED,lw=1,ls=(0,(4,3)))
        _time_axis(ax); ax.set_ylabel(ylabel,color=MUTED)
        ax.legend(frameon=False,fontsize=9,ncol=3,loc="upper left" if key != "v_min_pu" else "lower left")
        figures[name] = ax.figure
    return figures


def export_sensitivity(output):
    """Write the all-fleet intraday figures into `output` (the S1 folder)."""
    import matplotlib
    matplotlib.use("Agg")
    save_figures(sensitivity_figures(output),output)


def session_figures(s1):
    """When cars arrive, how long they charge, their SOC on arrival and how long they wait: one plot each."""
    s, p = s1["sessions"], s1["params"]
    specs = [("sessions_arrivals","Arrivals per hour","hour of day",lambda part: part.arrival_min/60,np.arange(25)),
             ("sessions_charging_time","Charging time","minutes",lambda part: part.duration_min,20),
             ("sessions_arrival_soc","State of charge on arrival","%",lambda part: part.soc_arrival*100,np.arange(0,101,5)),
             ("sessions_waiting_time","Waiting time in the queue (served sessions)","minutes",
              lambda part: part.wait_min.dropna(),np.arange(0,32,2))]
    figures = {}
    for name,title,xlabel,value,bins in specs:
        ax = new_axes((9,5))
        for key in ("ac","dc"):
            part = s[s.type==key]
            if part.empty:
                continue
            st = TYPE_STYLE[key]
            label = ("AC charging stations" if key == "ac" else "DC hubs")+(
                f" ({(~part.served).sum()} gave up)" if name == "sessions_waiting_time" else f" ({len(part)} sessions)")
            ax.hist(value(part),bins=bins,color=st["color"],alpha=.75,label=label,edgecolor=SURFACE)
        _style(ax,title); ax.set_xlabel(xlabel,color=MUTED); ax.set_ylabel("sessions",color=MUTED)
        ax.legend(frameon=False,fontsize=8.5)
        figures[name] = ax.figure
    return figures


def plot_profile_comparison(s1, ax=None):
    """Minimum phase voltage vs. distance at the S1 peak step: S0 in gray, S1 in red."""
    study = s1["study"]
    d = delta_v(s1)
    d = d[d.time==study["peak"]].groupby(["bus","distance_km"])[["v_pu_s0","v_pu_s1"]].min().reset_index()
    ax = ax or new_axes((10,5))
    k = marker_scale(study["graph"])
    _style(ax,f"Minimum bus voltage against distance at the S1 peak ({study['peak']:%H:%M})")
    ax.scatter(d.distance_km,d.v_pu_s0,s=26*k,color=S0_GRAY,label="S0 (no EVs)",zorder=2)
    ax.scatter(d.distance_km,d.v_pu_s1,s=22*k,color=S1_RED,marker="v",label="S1 (with EVs)",zorder=3)
    ax.vlines(d.distance_km,d.v_pu_s0,d.v_pu_s1,color=S1_RED,lw=.8,alpha=.5)
    for limit in V_LIMITS:
        ax.axhline(limit,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_xlabel("electrical distance from the substation (km)",color=MUTED); ax.set_ylabel("voltage (pu)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="lower left",ncol=2)
    return ax


def fleet_figures(s1, s0, base=None, frozen=None, case=None):
    """Every figure of one fleet as single plots: {file name: Figure}. base/case/frozen: S0, S1 and frozen-tap
    flows (default: those in s1)."""
    study, res = s1["study"], s1["params"].resolution_min
    base = base or s0["flow"]; case = case or study["flow"]; frozen = frozen if frozen is not None else s1.get("frozen")
    total = resample(sum(s1["power"].values()),res)
    slack = s0["network"].slack_bus
    figures = {"stations_map": plot_allocation(s1).figure,
               "demand_ev_hourly_by_type": plot_ev_demand_hourly(s1).figure,
               "demand_ev_intraday": plot_ev_demand_intraday(s1).figure,
               "demand_substation_hourly": plot_substation_hourly(s1).figure,
               "demand_substation_intraday": plot_substation_intraday(s1).figure,
               "voltage_min_intraday": plot_min_voltage_intraday(s1).figure,
               "voltage_min_vs_distance_peak": plot_profile_comparison(s1).figure,
               "voltage_range_day": plot_voltage_envelope(study).figure,
               **session_figures(s1),
               **impact_figures(base,case,s0["buses"],s0["graph"],slack,frozen,dict(s1["sites"]),"S1",total),
               **network_state_figures(base,case,s0["buses"],s0["graph"],slack,s1["sites"],"S1")}
    return figures


def summary(s1):
    study, s0, s, p = s1["study"], s1["s0"], s1["sessions"], s1["params"]
    d = delta_v(s1)
    worst = d.loc[d.dv_pu.idxmin()]
    b = study["flow"]["branches"]; b = b[b.physical_phase & (b.time==study["peak"])]
    total = sum(s1["power"].values())
    per_type = {}
    for key,t in p.types.items():
        part = s[s.type==key]
        per_type[t.name] = dict(sites=[bus for bus,k in s1["sites"].items() if k==key],
                                chargers_per_site=t.chargers_per_site,charger_kw=t.charger_kw,sessions=len(part),
                                served_share=float(part.served.mean()) if len(part) else 1.,
                                energy_kwh=float(part.energy_kwh.sum()),
                                unserved_kwh=float(part.loc[~part.served,"energy_kwh"].sum()),
                                mean_duration_min=float(part.duration_min.mean()) if len(part) else 0.,
                                mean_wait_min=float(part.wait_min.mean()) if part.served.any() else 0.)
    (e0,l0),(e1,l1) = energy_kwh(s0),energy_kwh(study)
    return {"evs": s1["evs"], "battery_kwh_mean": p.fleet.battery_kwh, "resolution_min": p.resolution_min,
            "daily_public_kwh": p.fleet.daily_public_kwh, "types": per_type,
            "ev_peak_kw": {"1min": float(total.max()), f"{p.resolution_min}min": float(resample(total,p.resolution_min).max()),
                           "60min": float(resample(total,60).max())},
            "peak_s0_kw": float(s0["flow"]["source"].p_kw.max()), "peak_s1_kw": float(study["flow"]["source"].p_kw.max()),
            "energy_s0_kwh": e0, "energy_s1_kwh": e1, "losses_s0_kwh": l0, "losses_s1_kwh": l1,
            "max_drop_pu": float(-worst.dv_pu), "max_drop_at": f"{worst.bus}.{worst.phase} {worst.time}",
            "v_min_s0_pu": float(feeder_voltages(s0).v_pu.min()), "v_min_s1_pu": float(feeder_voltages(study).v_pu.min()),
            "buses_below_0.95": int(feeder_voltages(study).query("v_pu < .95").bus.nunique()),
            "max_equipment_loading_peak_pct": float(b[b.element_type=="trafo"].loading_pct.max()),
            "max_line_loading_peak_pct": float(b[b.element_type=="line"].loading_pct.max()),
            "impact": impact_summary(s0["flow"],study["flow"],study["buses"],study["network"].slack_bus,s1["frozen"])}


def export_s1(output=None, config_dir="configs", screening_dir=None, fleets=None):
    """Writes to `output` (default results/<feeder>/s1)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output = output or study_dir(config_dir,"s1")
    screening_dir = screening_dir or study_dir(config_dir)
    table = load_fleets(screening_dir)
    params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
    if not fleets:
        fleets = sorted({params.fleet.evs,*table.index})
    s0 = run_s0(config_dir,params.resolution_min,study_feeder(config_dir))
    overloads = base_overloads(s0["flow"])
    base_tables = Path(output)/"dados"; base_tables.mkdir(parents=True,exist_ok=True)
    intraday_table(s0["flow"],s0["network"].slack_bus,overloads).to_csv(base_tables/"s0_intraday.csv",index=False)
    for name in ("voltages","branches","source"):  # base case at the same resolution, for redrawing
        s0["flow"][name].to_csv(base_tables/f"s0_{name}.csv",index=False)
    results = []
    for evs in fleets:
        s1 = run_s1(evs,config_dir,screening_dir,s0)
        out = Path(output)/f"evs_{evs}"; out.mkdir(parents=True,exist_ok=True)
        for old in out.glob("*"):
            shutil.rmtree(old) if old.is_dir() else old.unlink()
        tables = out/"dados"; tables.mkdir()  # CSV tables apart from the figures
        for name in ("voltages","branches","source"):
            s1["study"]["flow"][name].to_csv(tables/f"{name}.csv",index=False)
            s1["frozen"][name].to_csv(tables/f"frozen_taps_{name}.csv",index=False)  # for redrawing
        day = intraday_table(s1["study"]["flow"],s0["network"].slack_bus,overloads)
        for name,flow in (("regulated",s1["study"]["flow"]),("frozen_taps",s1["frozen"])):
            change = voltage_change(s0["flow"],flow,s0["buses"],s0["network"].slack_bus)
            day[f"max_drop_{name}_pct"] = -change.groupby("time").dv_pu.min().to_numpy()*100
        day.to_csv(tables/"intraday.csv",index=False)
        delta_v(s1).to_csv(tables/"delta_v.csv",index=False)
        s1["sessions"].to_csv(tables/"sessions.csv",index=False)
        res = s1["params"].resolution_min
        pd.DataFrame(s1["power"]).assign(minute=range(1440)).to_csv(tables/"station_power_1min.csv",index=False)
        pd.DataFrame({b: resample(kw,res) for b,kw in s1["power"].items()}).assign(
            hour=np.arange(1440//res)*res/60).to_csv(tables/f"station_power_{res}min.csv",index=False)
        pd.DataFrame({b: resample(kw,60) for b,kw in s1["power"].items()}).assign(hour=range(24)).to_csv(
            tables/"station_power_hourly.csv",index=False)
        save_figures(fleet_figures(s1,s0),out)
        result = summary(s1)
        (out/"summary.json").write_text(json.dumps(result,indent=2,default=str)+"\n",encoding="utf-8")
        results.append(result)
    export_sensitivity(output)
    if Path(output).name == "s1":  # default layout results/<feeder>/s1: refresh results/<feeder>/resumo
        from integration.resumo import export_resumo
        export_resumo(Path(output).parent)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output",help="default: results/<feeder>/s1")
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--screening",help="default: results/<feeder>/evcs_screening")
    parser.add_argument("--evs",type=int,nargs="*")
    parser.add_argument("--intraday-only",action="store_true",
                        help="only redraw the all-fleet figures and the summary from the files in --output")
    args = parser.parse_args()
    if args.intraday_only:
        export_sensitivity(args.output or study_dir(args.configs,"s1"))
        from integration.resumo import export_resumo
        export_resumo(Path(args.output or study_dir(args.configs,"s1")).parent)
        return
    print(json.dumps(export_s1(args.output,args.configs,args.screening,args.evs),indent=2,default=str))


if __name__ == "__main__":
    main()
