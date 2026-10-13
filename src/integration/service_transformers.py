"""Service transformer loading in S0 and S1: share of the rating used by each of the 1,177 IEEE 8500 service
transformers and the intraday demand of the most and least loaded ones (no power flow, seconds).

python -m integration.service_transformers            # every fleet of results/<feeder>/s1
python -m integration.service_transformers --evs 3000
The MV reduction lumps each service transformer and its customer on the primary bus, so the power flow does not
see these transformers. Their loading is rebuilt from saved results: customer demand = nominal P, Q x the daily
curve at the S1 step (loads are constant PQ, so this is what the power flow used), plus the grid-side power of a
station that shares the transformer. Hypothesis (configs/evcs.yaml, `service_transformers:`): a single-phase
eletroposto at a customer bus is served by that customer's transformer on the same phase; three-phase stations
and DC hubs have their own transformer, absent from the IEEE data, and are left out.
Writes results/<feeder>/s1/evs_<n>/transformers_*.png and dados/service_transformers*.csv.
"""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from core.schemas import read_parameters
from network.feeder_loader import read_feeder_data
from integration.scenarios import study_feeder
from integration.zoning import service_transformers
from integration.impact import LOADING_BANDS, REGULATED, BASE_GRAY
from integration.s0_study import _style, new_axes, save_figures, INK, MUTED

LINE_COLORS = ["#2a78d6","#eb6834","#1baf7a","#4a3aa7","#d03b3b"]  # categorical slots, one per transformer


def customers_table(feeder):
    """One row per service transformer (one per IEEE 8500 load): id, bus, phases, kva, p_kw, q_kvar (nominal)."""
    loads = pd.DataFrame(read_feeder_data(feeder)["loads"])
    loads["kva"] = loads.id.str.lower().map(service_transformers(feeder))
    if loads.kva.isna().any():
        raise ValueError("Loads without a service transformer rating")
    return loads[["id","bus","phases","kva","p_kw","q_kvar"]]


def shared_stations(customers, sites, phases, share=True):
    """Transformer id -> station bus, for single-phase stations at a customer bus on that customer's phase.
    sites: bus -> station type; phases: bus -> phase of single-phase sites (absent = three-phase).
    The largest transformer is taken when several customers share the bus and phase."""
    if not share:
        return {}
    assigned = {}
    for bus in sites:
        phase = phases.get(bus,"ABC")
        if len(phase) != 1:
            continue  # three-phase AC and DC hubs: own transformer
        here = customers[(customers.bus == bus) & customers.phases.str.contains(phase)]
        if len(here):
            assigned[here.sort_values("kva").id.iloc[-1]] = bus
    return assigned


def transformer_demand(customers, multipliers, station_kw=None, assigned=None):
    """Active power (kW) and loading (% of kVA) per step and transformer: (kw, pct), steps x transformers.
    station_kw: bus -> grid-side kW per step; assigned: transformer id -> station bus. Chargers at unity power factor."""
    m = np.asarray(multipliers,float)[:,None]
    p = m*customers.p_kw.to_numpy()[None,:]
    q = m*customers.q_kvar.to_numpy()[None,:]
    ids = customers.id.tolist()
    for tid,bus in (assigned or {}).items():
        p[:,ids.index(tid)] += np.asarray(station_kw[bus],float)
    kw = pd.DataFrame(p,columns=ids)
    pct = pd.DataFrame(100*np.hypot(p,q)/customers.kva.to_numpy()[None,:],columns=ids)
    return kw,pct


def transformer_summary(customers, assigned, pct0, pct1, kw1, dt_h):
    t = customers.assign(station=customers.id.map(assigned).fillna(""))
    t["peak_s0_pct"], t["peak_s1_pct"] = pct0.max().to_numpy(), pct1.max().to_numpy()
    t["peak_s1_step"] = pct1.to_numpy().argmax(axis=0)
    t["hours_over_100_s1"] = (pct1 > 100).sum().to_numpy()*dt_h
    t["energy_s1_kwh"] = kw1.sum().to_numpy()*dt_h
    return t.sort_values(["peak_s1_pct","id"],ascending=[False,True]).reset_index(drop=True)  # ties: by id


def select(table, most, least):
    """The `most` highest and `least` lowest transformers by S1 peak loading."""
    return table.head(most), table.tail(least).iloc[::-1]


# ------------------------------------------------------------------------------------------- figures
def _label(row):
    return f"{row.id}\n{row.kva:g} kVA" + ("\n+ station" if row.station else "")


def plot_loading_bands(table, evs):
    ax = new_axes()
    _style(ax,f"Service transformers by peak loading: S0 and S1 ({evs} EVs)")
    labels = [b[2] for b in LOADING_BANDS]
    x = np.arange(len(labels))
    for k,(col,name,color) in enumerate((("peak_s0_pct","S0",BASE_GRAY),("peak_s1_pct",f"S1 ({evs} EVs)",REGULATED))):
        counts = [int(((table[col] >= lo) & (table[col] < hi)).sum()) for lo,hi,_ in LOADING_BANDS]
        bars = ax.bar(x+(k-.5)*.38,counts,width=.38,color=color,label=name)
        ax.bar_label(bars,fontsize=8,color=INK,padding=2)
    ax.set_xticks(x,labels); ax.set_xlabel("peak loading of the day (% of kVA rating)",color=MUTED)
    ax.set_ylabel("service transformers",color=MUTED); ax.legend(frameon=False,fontsize=9)
    return ax


def plot_selected_loading(most, least, evs):
    rows = pd.concat([most,least])
    ax = new_axes((11,6))
    _style(ax,f"Peak loading of the {len(most)} most and {len(least)} least loaded service transformers ({evs} EVs)")
    x = np.arange(len(rows))
    s0 = ax.bar(x-.19,rows.peak_s0_pct,width=.38,color=BASE_GRAY,label="S0")
    s1 = ax.bar(x+.19,rows.peak_s1_pct,width=.38,color=REGULATED,label=f"S1 ({evs} EVs)")
    for bars in (s0,s1):
        ax.bar_label(bars,fmt="%.0f",fontsize=8,color=INK,padding=2)
    ax.axhline(100,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.annotate("rating",(x[-1]+.5,100),xytext=(0,3),textcoords="offset points",ha="right",fontsize=8,color=MUTED)
    ax.axvline(len(most)-.5,color=MUTED,lw=.8)
    ax.set_xticks(x,[_label(r) for r in rows.itertuples()],fontsize=8)
    ax.set_ylabel("peak loading (% of kVA rating)",color=MUTED); ax.legend(frameon=False,fontsize=9,loc="upper right")
    return ax


def plot_intraday(rows, kw, hours, title):
    ax = new_axes()
    _style(ax,title)
    for color,row in zip(LINE_COLORS,rows.itertuples()):
        ax.plot(hours,kw[row.id],color=color,lw=1.6,label=_label(row).replace("\n",", "))
    ax.plot(hours,kw[rows.id].mean(axis=1),color=INK,lw=2,ls=(0,(4,2)),label="mean of these transformers")
    if len(rows) > 1 and np.allclose(kw[rows.id].to_numpy(),kw[rows.id[:1]].to_numpy()):
        ax.annotate("the curves coincide: same load and the same daily curve",(.99,.02),xycoords="axes fraction",
                    ha="right",fontsize=9,color=MUTED)
    ax.set_xlim(0,24); ax.set_xticks(range(0,25,3)); ax.set_xlabel("hour of day",color=MUTED)
    ax.set_ylabel("active power (kW)",color=MUTED); ax.legend(frameon=False,fontsize=8,loc="upper left")
    return ax


def transformer_figures(table, kw1, hours, most, least, evs):
    top,bottom = select(table,most,least)
    return {"transformers_loading_bands": plot_loading_bands(table,evs).figure,
            "transformers_loading_selected": plot_selected_loading(top,bottom,evs).figure,
            "transformers_intraday_most_loaded": plot_intraday(
                top,kw1,hours,f"Intraday demand of the {most} most loaded service transformers (S1, {evs} EVs)").figure,
            "transformers_intraday_least_loaded": plot_intraday(
                bottom,kw1,hours,f"Intraday demand of the {least} least loaded service transformers (S1, {evs} EVs)").figure}


# ------------------------------------------------------------------------------------------- study
def export_fleet(fleet_dir, customers, sites, phases, settings):
    """Tables and figures of one fleet folder (results/<feeder>/s1/evs_<n>). Returns the per-transformer table."""
    import matplotlib
    matplotlib.use("Agg")
    fleet_dir = Path(fleet_dir)
    evs = int(fleet_dir.name.split("_")[1])
    source = pd.read_csv(fleet_dir/"dados"/"source.csv")
    stations = pd.read_csv(fleet_dir/"dados"/"station_power_15min.csv").drop(columns="hour")
    if len(stations) != len(source):
        raise ValueError("Station power and power flow steps differ")
    dt_h = 24/len(source)
    assigned = shared_stations(customers,sites,phases,settings["share_single_phase_eletropostos"])
    station_kw = {bus: stations[bus].to_numpy() for bus in set(assigned.values())}
    _,pct0 = transformer_demand(customers,source.load_multiplier)
    kw1,pct1 = transformer_demand(customers,source.load_multiplier,station_kw,assigned)
    table = transformer_summary(customers,assigned,pct0,pct1,kw1,dt_h)
    table["peak_s1_time"] = source.time.iloc[table.peak_s1_step].to_numpy()
    table.drop(columns="peak_s1_step").to_csv(fleet_dir/"dados"/"service_transformers.csv",index=False)
    top,bottom = select(table,settings["most_loaded"],settings["least_loaded"])
    kw1.assign(time=source.time)[["time",*top.id,*bottom.id]].to_csv(
        fleet_dir/"dados"/"service_transformers_intraday.csv",index=False)
    hours = np.arange(len(source))*dt_h
    save_figures(transformer_figures(table,kw1,hours,settings["most_loaded"],settings["least_loaded"],evs),fleet_dir)
    return table


def export_transformers(feeder_dir, config_dir="configs", fleets=None):
    """Every fleet folder of feeder_dir/s1 (or only `fleets`). Returns {evs: table}."""
    from integration.s1_study import load_fleets
    feeder_dir = Path(feeder_dir)
    settings = read_parameters(Path(config_dir)/"evcs.yaml")["service_transformers"]
    table = load_fleets(feeder_dir/"evcs_screening")
    customers = customers_table(feeder_dir.name)
    done = {}
    for d in sorted((feeder_dir/"s1").glob("evs_*"),key=lambda p: int(p.name.split("_")[1])):
        evs = int(d.name.split("_")[1])
        if (fleets and evs not in fleets) or not (d/"dados"/"station_power_15min.csv").exists():
            continue
        done[evs] = export_fleet(d,customers,table.loc[evs].sites,table.loc[evs].site_phases,settings)
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--results",default="results")
    parser.add_argument("--evs",type=int,nargs="*")
    args = parser.parse_args()
    for evs,t in export_transformers(Path(args.results)/study_feeder(args.configs),args.configs,args.evs).items():
        print(f"{evs} EVs: {(t.station != '').sum()} transformers shared with a station; above 100 % at peak: "
              f"S0 {(t.peak_s0_pct > 100).sum()}, S1 {(t.peak_s1_pct > 100).sum()}")


if __name__ == "__main__":
    main()
