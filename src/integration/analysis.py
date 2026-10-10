"""S0 vs S1 analysis: network and power-quality metrics of the base case and of S1, side by side.

python -m integration.analysis                # S1 fleet = the largest simulated (default 10 000 cars)
python -m integration.analysis --evs 5000
Reads the saved S1 study (results/<feeder>/s1, 15 min) and writes results/<feeder>/s0_vs_s1/:
one plot per figure (English) and ANALISE.md (Portuguese report with S0, S1 and S0 x S1). No power flow.

Power-quality references (thresholds used for comparison; neither is the Honduran regulation):
- ANSI C84.1: range A 0.95-1.05 pu, range B 0.917-1.058 pu.
- ANEEL PRODIST Module 8, 1 kV < Vn < 69 kV: adequate 0.93-1.05 pu, precarious 0.90-0.93 pu, critical
  < 0.90 or > 1.05 pu; DRP (share of time precarious) limit 3 %, DRC (critical) limit 0.5 %; unbalance
  FD95% limit 2 %; power factor reference 0.92. PRODIST measures a week (1008 readings); here one day
  of 96 readings.
Unbalance is the phase voltage unbalance rate (PVUR, IEEE: largest deviation from the mean phase voltage
over the mean), from phase magnitudes; the sequence-based factor needs phase angles, which are not saved.
Harmonics, flicker and transients are outside a fundamental-frequency power flow and are not assessed.
"""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from network.feeder_loader import read_feeder_data
from network.topology import feeder_graph, bus_table
from integration.scenarios import study_feeder
from integration.impact import flow_from_csv, network_state_figures, peak_step, tap_operations, REGULATED, BASE_GRAY
from integration.s0_study import _style, _edges, new_axes, save_figures, marker_scale, INK, MUTED

ANSI_A, ANSI_B = (.95,1.05), (.917,1.058)
PRODIST = dict(precarious=(.90,.93), adequate=(.93,1.05))  # critical: < 0.90 or > 1.05
DRP_LIMIT, DRC_LIMIT, FD_LIMIT, PF_REF = 3., .5, 2., .92
CASE_COLORS = {"S0": BASE_GRAY, "S1": REGULATED}


def _dt_h(flow):
    t = pd.DatetimeIndex(flow["source"].time)
    return (t[1]-t[0]).total_seconds()/3600 if len(t) > 1 else 1.


def bus_voltages(flow, slack):
    v = flow["voltages"]
    return v[v.bus != slack]


def pvur(flow, three_phase):
    """Phase voltage unbalance rate (%) per three-phase bus and step."""
    v = flow["voltages"]
    v = v[v.bus.isin(three_phase)].pivot_table(index=["time","bus"],columns="phase",values="v_pu")[["A","B","C"]]
    mean = v.mean(axis=1)
    return (v.sub(mean,axis=0).abs().max(axis=1)/mean*100).rename("pvur_pct").reset_index()


def prodist_shares(worst):
    """Per bus: share of time (%) precarious (DRP) and critical (DRC), from the worst phase each step."""
    precarious = worst.v_pu.between(*PRODIST["precarious"],inclusive="left")
    critical = (worst.v_pu < PRODIST["precarious"][0]) | (worst.v_pu > PRODIST["adequate"][1])
    g = worst.assign(p=precarious,c=critical).groupby("bus")
    return pd.DataFrame({"drp_pct": g.p.mean()*100,"drc_pct": g.c.mean()*100})


def case_metrics(flow, slack, three_phase, loads, base_overloads):
    """Every indicator of one case. Returns (metrics dict, per-bus table, PVUR table)."""
    dt = _dt_h(flow)
    source = flow["source"]
    s_kva = np.hypot(source.p_kw,source.q_kvar)
    pf = source.p_kw/s_kva
    peak = int(source.p_kw.idxmax())
    v = bus_voltages(flow,slack)
    worst = v.groupby(["time","bus"]).v_pu.min().reset_index()
    per_bus = prodist_shares(worst)
    per_bus["v_min_pu"] = worst.groupby("bus").v_pu.min()
    per_bus["hours_below_095"] = worst.assign(b=worst.v_pu < ANSI_A[0]).groupby("bus").b.sum()*dt
    unb = pvur(flow,three_phase)
    per_bus = per_bus.join(unb.groupby("bus").pvur_pct.quantile(.95).rename("pvur95_pct"))
    b = flow["branches"]; b = b[b.physical_phase]
    lines = b[(b.element_type=="line") & ~b.line.isin(list(base_overloads))]
    line_step = lines.groupby(["line","time"]).loading_pct.max()
    equip = b[b.element_type=="trafo"].groupby("time").loading_pct.max()
    losses_kw = flow["branches"].groupby("time").loss_kw.sum()
    below = per_bus.index[per_bus.v_min_pu < ANSI_A[0]]
    affected = loads[loads.bus.isin(below)]
    energy = float(source.p_kw.sum()*dt)
    taps = tap_operations(flow)
    m = {
        "peak_kw": float(source.p_kw.max()), "peak_time": pd.Timestamp(source.time.iloc[peak]),
        "energy_mwh": energy/1000, "load_factor": float(source.p_kw.mean()/source.p_kw.max()),
        "pf_min": float(pf.min()), "pf_peak": float(pf.iloc[peak]), "pf_mean": float((source.p_kw.sum())/s_kva.sum()),
        "losses_mwh": float(losses_kw.sum()*dt/1000), "losses_pct": float(losses_kw.sum()*dt/energy*100),
        "losses_peak_kw": float(losses_kw.max()),
        "v_min": float(v.v_pu.min()), "v_max": float(v.v_pu.max()),
        "v_mean_dev_pct": float((v.v_pu-1).abs().mean()*100),
        "ansi_a_out_pct": float((~v.v_pu.between(*ANSI_A)).mean()*100),
        "ansi_b_out_pct": float((~v.v_pu.between(*ANSI_B)).mean()*100),
        "buses_below_095": int((per_bus.v_min_pu < ANSI_A[0]).sum()),
        "bus_hours_below_095": float(per_bus.hours_below_095.sum()),
        "prodist_adequate_pct": float(v.v_pu.between(*PRODIST["adequate"]).mean()*100),
        "prodist_precarious_pct": float(v.v_pu.between(*PRODIST["precarious"],inclusive="left").mean()*100),
        "prodist_critical_pct": float(((v.v_pu < .90) | (v.v_pu > 1.05)).mean()*100),
        "buses_drp_over": int((per_bus.drp_pct > DRP_LIMIT).sum()), "buses_drc_over": int((per_bus.drc_pct > DRC_LIMIT).sum()),
        "drp_max": float(per_bus.drp_pct.max()), "drc_max": float(per_bus.drc_pct.max()),
        "customers_below_095": int(len(affected)), "customers_total": int(len(loads)),
        "customers_kw_below_095": float(affected.p_kw.sum()),
        "pvur_max": float(unb.pvur_pct.max()), "pvur95_max": float(per_bus.pvur95_pct.max()),
        "pvur95_median": float(per_bus.pvur95_pct.median()),
        "buses_fd_over": int((per_bus.pvur95_pct > FD_LIMIT).sum()),
        "line_max_pct": float(line_step.max()),
        "lines_over_100": int((line_step.groupby("line").max() > 100).sum()),
        "line_hours_over_100": float((line_step > 100).sum()*dt),
        "lines_over_80": int((line_step.groupby("line").max() > 80).sum()),
        "equipment_max_pct": float(equip.max()),
        "tap_operations": int(sum(taps.values())), "tap_max": int(flow["source"].filter(like="tap_").max().max()),
    }
    return m, per_bus, unb


ROWS = [  # (section, label, key, format, better)
    ("Demanda","Ponta na subestação (kW)","peak_kw","{:,.0f}","low"),
    ("Demanda","Hora da ponta","peak_time","{:%H:%M}",None),
    ("Demanda","Energia no dia (MWh)","energy_mwh","{:,.1f}",None),
    ("Demanda","Fator de carga","load_factor","{:.3f}","high"),
    ("Perdas","Perdas no dia (MWh)","losses_mwh","{:,.2f}","low"),
    ("Perdas","Perdas / energia (%)","losses_pct","{:.2f}","low"),
    ("Perdas","Perdas na hora mais carregada (kW)","losses_peak_kw","{:,.0f}","low"),
    ("Tensão","Tensão mínima (pu)","v_min","{:.3f}","high"),
    ("Tensão","Tensão máxima (pu)","v_max","{:.3f}","low"),
    ("Tensão","Desvio médio em relação a 1 pu (%)","v_mean_dev_pct","{:.2f}","low"),
    ("Tensão","Leituras fora da faixa A da ANSI, 0,95–1,05 (%)","ansi_a_out_pct","{:.2f}","low"),
    ("Tensão","Leituras fora da faixa B da ANSI, 0,917–1,058 (%)","ansi_b_out_pct","{:.2f}","low"),
    ("Tensão","Barras que ficam abaixo de 0,95 pu em algum momento","buses_below_095","{:,d}","low"),
    ("Tensão","Barra-horas abaixo de 0,95 pu","bus_hours_below_095","{:,.1f}","low"),
    ("Tensão","Consumidores em barras abaixo de 0,95 pu","customers_below_095","{:,d}","low"),
    ("Tensão","Carga desses consumidores (kW)","customers_kw_below_095","{:,.0f}","low"),
    ("PRODIST","Leituras adequadas, 0,93–1,05 (%)","prodist_adequate_pct","{:.2f}","high"),
    ("PRODIST","Leituras precárias, 0,90–0,93 (%)","prodist_precarious_pct","{:.2f}","low"),
    ("PRODIST","Leituras críticas, < 0,90 ou > 1,05 (%)","prodist_critical_pct","{:.2f}","low"),
    ("PRODIST","Maior DRP de uma barra (%) — limite 3 %","drp_max","{:.2f}","low"),
    ("PRODIST","Barras com DRP acima de 3 %","buses_drp_over","{:,d}","low"),
    ("PRODIST","Maior DRC de uma barra (%) — limite 0,5 %","drc_max","{:.2f}","low"),
    ("PRODIST","Barras com DRC acima de 0,5 %","buses_drc_over","{:,d}","low"),
    ("Desequilíbrio","Maior desequilíbrio em um instante, PVUR (%)","pvur_max","{:.2f}","low"),
    ("Desequilíbrio","Maior PVUR 95 % de uma barra (%) — referência 2 %","pvur95_max","{:.2f}","low"),
    ("Desequilíbrio","PVUR 95 % mediano das barras trifásicas (%)","pvur95_median","{:.2f}","low"),
    ("Desequilíbrio","Barras trifásicas com PVUR 95 % acima de 2 %","buses_fd_over","{:,d}","low"),
    ("Fator de potência","Fator de potência na ponta","pf_peak","{:.3f}","high"),
    ("Fator de potência","Fator de potência médio do dia","pf_mean","{:.3f}","high"),
    ("Fator de potência","Menor fator de potência do dia","pf_min","{:.3f}","high"),
    ("Carregamento","Linha mais carregada (%)","line_max_pct","{:.1f}","low"),
    ("Carregamento","Linhas acima de 100 % em algum momento","lines_over_100","{:,d}","low"),
    ("Carregamento","Linha-horas acima de 100 %","line_hours_over_100","{:,.1f}","low"),
    ("Carregamento","Linhas acima de 80 % em algum momento","lines_over_80","{:,d}","low"),
    ("Carregamento","Transformador/regulador mais carregado (%)","equipment_max_pct","{:.1f}","low"),
    ("Reguladores","Operações de tap no dia","tap_operations","{:,d}","low"),
    ("Reguladores","Maior posição de tap (máximo do equipamento: 16)","tap_max","{:,d}",None),
]


def _fmt(fmt, value):
    return fmt.format(value).replace(",","X").replace(".",",").replace("X",".") if not isinstance(value,pd.Timestamp) else fmt.format(value)


def metrics_table(m0, m1):
    """S0, S1 and the change, as markdown sections."""
    out, section = [], None
    for sec,label,key,fmt,better in ROWS:
        if sec != section:
            out += ["",f"### {sec}","","| Indicador | S0 | S1 | Variação |","|---|---|---|---|"]
            section = sec
        a,b = m0[key],m1[key]
        if isinstance(a,pd.Timestamp):
            change = ""
        elif a == b:
            change = "="
        else:
            diff = b-a
            rel = f" ({'+' if diff > 0 else ''}{_fmt('{:.1f}',100*diff/abs(a))} %)" if a not in (0,0.) else ""
            worse = better and ((better == "low" and diff > 0) or (better == "high" and diff < 0))
            change = f"{'+' if diff > 0 else ''}{_fmt(fmt.replace(',d',',.0f') if 'd' in fmt else fmt,diff)}{rel}" + (" ⚠" if worse else "")
        out.append(f"| {label} | {_fmt(fmt,a)} | {_fmt(fmt,b)} | {change} |")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------- figures
def plot_station_composition(sites, params, evs, ax=None):
    """Number of stations of each type and their installed power, for one fleet."""
    from integration.evcs_screening import TYPE_STYLE
    ax = ax or new_axes((9,5))
    keys = [k for k in ("dc","ac","ac1") if k in params.types]
    counts = [sum(v==k for v in sites.values()) for k in keys]
    kw = [c*params.types[k].site_kw for c,k in zip(counts,keys)]
    y = np.arange(len(keys))
    ax.barh(y,counts,.6,color=[TYPE_STYLE[k]["color"] for k in keys])
    for i,(c,k,p) in enumerate(zip(counts,keys,kw)):
        t = params.types[k]
        ax.annotate(f"{c} sites · {t.chargers_per_site}×{t.charger_kw:g} kW each · {p:,.0f} kW installed",
                    (c,i),xytext=(6,0),textcoords="offset points",va="center",fontsize=9,color=INK)
    _style(ax,f"Charging stations for {evs:,} EVs: {sum(counts)} sites, {sum(kw):,.0f} kW installed")
    ax.set_yticks(y,[TYPE_STYLE[k]["label"] for k in keys]); ax.invert_yaxis()
    ax.set_xlim(0,max(counts)*1.9); ax.set_xlabel("number of sites",color=MUTED)
    return ax


def plot_composition_by_fleet(fleets, params, ax=None):
    """Stacked number of sites per type for every simulated fleet."""
    from integration.evcs_screening import TYPE_STYLE
    ax = ax or new_axes((10,5.5))
    _style(ax,"Charging-station composition for each fleet size")
    x = np.arange(len(fleets)); bottom = np.zeros(len(fleets))
    for k in ("dc","ac","ac1"):
        if k not in params.types:
            continue
        n = np.array([sum(v==k for v in s.values()) for s in fleets.sites])
        ax.bar(x,n,.6,bottom=bottom,color=TYPE_STYLE[k]["color"],label=TYPE_STYLE[k]["label"])
        for i,(b,c) in enumerate(zip(bottom,n)):
            if c:
                ax.annotate(str(c),(i,b+c/2),ha="center",va="center",fontsize=9,color="white")
        bottom += n
    ax.set_xticks(x,[f"{e:,} EVs" for e in fleets.index]); ax.set_ylabel("number of sites",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="upper left")
    return ax


def _cases_bar(ax, labels, values, title, ylabel, fmt="{:.2f}"):
    x = np.arange(len(labels))
    for j,(case,vals) in enumerate(values.items()):
        bars = ax.bar(x+(j-.5)*.38,vals,.38,color=CASE_COLORS[case],label=case)
        for xi,v in zip(x+(j-.5)*.38,vals):
            ax.annotate(fmt.format(v),(xi,v),xytext=(0,3),textcoords="offset points",ha="center",fontsize=8,color=INK)
    _style(ax,title); ax.set_xticks(x,labels); ax.set_ylabel(ylabel,color=MUTED); ax.legend(frameon=False,fontsize=9)


def comparison_figures(f0, f1, m0, m1, b0, b1, u0, u1, buses, graph, slack, sites, params, fleets, evs, base_over):
    figures = {}
    labels = {"S0": "S0 (no EVs)", "S1": f"S1 ({evs:,} EVs)"}

    figures["stations_composition"] = plot_station_composition(sites,params,evs).figure
    figures["stations_composition_by_fleet"] = plot_composition_by_fleet(fleets,params).figure

    ax = new_axes((10,5))
    _cases_bar(ax,["below 0.917\n(out of B)","0.917–0.95\n(range B only)","0.95–1.05\n(range A)","1.05–1.058\n(range B only)","above 1.058\n(out of B)"],
               {c: [100*np.mean(v < .917),100*np.mean((v >= .917)&(v < .95)),100*np.mean((v >= .95)&(v <= 1.05)),
                    100*np.mean((v > 1.05)&(v <= 1.058)),100*np.mean(v > 1.058)]
                for c,v in (("S0",bus_voltages(f0,slack).v_pu.to_numpy()),("S1",bus_voltages(f1,slack).v_pu.to_numpy()))},
               "Voltage readings per ANSI C84.1 range (all buses, phases and 15-min steps)","share of readings (%)")
    ax.set_yscale("symlog",linthresh=1)
    figures["quality_voltage_ansi_ranges"] = ax.figure

    ax = new_axes((9,5))
    _cases_bar(ax,["adequate\n0.93–1.05","precarious\n0.90–0.93","critical\n<0.90 or >1.05"],
               {c: [m["prodist_adequate_pct"],m["prodist_precarious_pct"],m["prodist_critical_pct"]] for c,m in (("S0",m0),("S1",m1))},
               "Voltage readings per PRODIST Module 8 band (medium voltage)","share of readings (%)")
    ax.set_yscale("symlog",linthresh=1)
    figures["quality_voltage_prodist_bands"] = ax.figure

    ax = new_axes((10,5))
    _style(ax,"Distribution of all voltage readings (buses × phases × 15-min steps)")
    for c,f in (("S0",f0),("S1",f1)):
        v = np.sort(bus_voltages(f,slack).v_pu.to_numpy())
        ax.plot(v,np.arange(1,len(v)+1)/len(v)*100,color=CASE_COLORS[c],lw=2,label=labels[c])
    for lim,txt in ((.95,"0.95 (ANSI A)"),(.93,"0.93 (PRODIST)"),(1.05,"1.05")):
        ax.axvline(lim,color=MUTED,lw=1,ls=(0,(4,3)))
        ax.annotate(txt,(lim,2),xytext=(3,0),textcoords="offset points",fontsize=8,color=MUTED,rotation=90,va="bottom")
    ax.set_xlabel("voltage (pu)",color=MUTED); ax.set_ylabel("readings at or below this voltage (%)",color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="upper left")
    figures["quality_voltage_distribution"] = ax.figure

    ax = new_axes((10,5))
    _cases_bar(ax,["buses below 0.95 pu","buses with DRP > 3 %","buses with DRC > 0.5 %","customers below 0.95 pu"],
               {c: [m["buses_below_095"],m["buses_drp_over"],m["buses_drc_over"],m["customers_below_095"]] for c,m in (("S0",m0),("S1",m1))},
               "Buses and customers exposed to low voltage","count",fmt="{:.0f}")
    figures["quality_buses_and_customers_affected"] = ax.figure

    pos = {n: d["xy"] for n,d in graph.nodes(data=True)}
    from matplotlib.colors import LinearSegmentedColormap
    import matplotlib.pyplot as plt
    cmap = LinearSegmentedColormap.from_list("seq",["#f0efec","#ec835a","#d03b3b","#7a1f1f"])
    for key,title,label,name in (("hours_below_095","Hours per day below 0.95 pu in S1","hours below 0.95 pu","quality_hours_below_095_map_S1"),
                                 ("pvur95_pct","Voltage unbalance in S1: PVUR 95th percentile per three-phase bus","PVUR 95 % (%)","quality_unbalance_map_S1")):
        s = b1[key].dropna()
        ax = new_axes((11,8.5)); _style(ax,title); _edges(ax,graph,pos,emphasis=False)
        xy = np.array([pos[b] for b in s.index])
        pts = ax.scatter(xy[:,0],xy[:,1],c=s.to_numpy(),cmap=cmap,vmin=0,vmax=max(s.max(),1e-6),s=46*marker_scale(graph),
                         edgecolor=MUTED,linewidth=.3,zorder=3)
        bar = plt.colorbar(pts,ax=ax,shrink=.6,pad=.01); bar.set_label(label,color=MUTED)
        ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
        figures[name] = ax.figure

    ax = new_axes((10,5))
    _style(ax,"Voltage unbalance (PVUR 95th percentile) of the three-phase buses")
    bins = np.linspace(0,max(b0.pvur95_pct.max(),b1.pvur95_pct.max(),FD_LIMIT)*1.05,40)
    for c,b in (("S0",b0),("S1",b1)):
        ax.hist(b.pvur95_pct.dropna(),bins=bins,color=CASE_COLORS[c],alpha=.6,label=labels[c])
    ax.axvline(FD_LIMIT,color=MUTED,lw=1,ls=(0,(4,3))); ax.annotate("2 % reference",(FD_LIMIT,ax.get_ylim()[1]*.9),xytext=(3,0),
                                                                    textcoords="offset points",fontsize=8,color=MUTED)
    ax.set_xlabel("PVUR 95 % (%)",color=MUTED); ax.set_ylabel("three-phase buses",color=MUTED); ax.legend(frameon=False,fontsize=9)
    figures["quality_unbalance_distribution"] = ax.figure

    hours = lambda f: np.arange(len(f["source"]))*24/len(f["source"])
    for name,title,ylabel,value,ref in (
            ("quality_power_factor_day","Power factor at the substation over the day","power factor",
             lambda f: f["source"].p_kw/np.hypot(f["source"].p_kw,f["source"].q_kvar),PF_REF),
            ("quality_losses_day","Feeder losses over the day","kW",lambda f: f["branches"].groupby("time").loss_kw.sum().to_numpy(),None),
            ("demand_substation_power_day","Substation active power over the day","kW",lambda f: f["source"].p_kw,None),
            ("quality_min_voltage_day","Lowest feeder voltage over the day","voltage (pu)",
             lambda f: bus_voltages(f,slack).groupby("time").v_pu.min().to_numpy(),.95)):
        ax = new_axes((11,5)); _style(ax,title)
        for c,f in (("S0",f0),("S1",f1)):
            ax.step(hours(f),value(f),where="post",color=CASE_COLORS[c],lw=2,label=labels[c])
        if ref:
            ax.axhline(ref,color=MUTED,lw=1,ls=(0,(4,3)))
        ax.set_xlim(0,24); ax.set_xticks(range(0,25,3)); ax.set_xlabel("hour of day",color=MUTED); ax.set_ylabel(ylabel,color=MUTED)
        ax.legend(frameon=False,fontsize=9)
        figures[name] = ax.figure

    ax = new_axes((10,5))
    _style(ax,"Loading duration curve of the most loaded line (base-case overloads left out)")
    for c,f in (("S0",f0),("S1",f1)):
        b = f["branches"]; b = b[b.physical_phase & (b.element_type=="line") & ~b.line.isin(list(base_over))]
        top = np.sort(b.groupby("time").loading_pct.max().to_numpy())[::-1]
        ax.plot(np.arange(1,len(top)+1)*24/len(top),top,color=CASE_COLORS[c],lw=2,label=labels[c])
    ax.axhline(100,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.set_xlabel("hours per day at or above this loading",color=MUTED); ax.set_ylabel("loading (%)",color=MUTED)
    ax.set_xlim(0,24); ax.legend(frameon=False,fontsize=9)
    figures["quality_line_loading_duration"] = ax.figure

    figures.update(network_state_figures(f0,f1,buses,graph,slack,sites,"S1"))
    return figures


def report(m0, m1, evs, sites, params, feeder):
    n = {k: sum(v==k for v in sites.values()) for k in ("dc","ac","ac1")}
    kw = sum(params.types[k].site_kw for k in sites.values())
    def bullet_case(m, name):
        return [f"- **Demanda:** ponta de {_fmt('{:,.0f}',m['peak_kw'])} kW às {m['peak_time']:%H:%M}, "
                f"{_fmt('{:,.1f}',m['energy_mwh'])} MWh no dia, fator de carga {_fmt('{:.3f}',m['load_factor'])}.",
                f"- **Perdas:** {_fmt('{:,.2f}',m['losses_mwh'])} MWh no dia ({_fmt('{:.2f}',m['losses_pct'])} % da energia).",
                f"- **Tensão:** de {_fmt('{:.3f}',m['v_min'])} a {_fmt('{:.3f}',m['v_max'])} pu; "
                f"{_fmt('{:.2f}',m['ansi_a_out_pct'])} % das leituras fora da faixa A da ANSI; "
                f"{m['buses_below_095']} barras abaixo de 0,95 pu em algum momento ({m['customers_below_095']} dos "
                f"{m['customers_total']} consumidores).",
                f"- **PRODIST:** {_fmt('{:.2f}',m['prodist_adequate_pct'])} % das leituras adequadas; maior DRP "
                f"{_fmt('{:.2f}',m['drp_max'])} % ({m['buses_drp_over']} barras acima de 3 %); maior DRC "
                f"{_fmt('{:.2f}',m['drc_max'])} % ({m['buses_drc_over']} barras acima de 0,5 %).",
                f"- **Desequilíbrio:** PVUR 95 % de até {_fmt('{:.2f}',m['pvur95_max'])} % (mediana "
                f"{_fmt('{:.2f}',m['pvur95_median'])} %); {m['buses_fd_over']} barras trifásicas acima de 2 %.",
                f"- **Fator de potência na subestação:** {_fmt('{:.3f}',m['pf_peak'])} na ponta, "
                f"{_fmt('{:.3f}',m['pf_mean'])} na média do dia, mínimo {_fmt('{:.3f}',m['pf_min'])}.",
                f"- **Carregamento:** linha mais carregada {_fmt('{:.1f}',m['line_max_pct'])} %; {m['lines_over_100']} linhas acima "
                f"de 100 % em algum momento ({_fmt('{:.1f}',m['line_hours_over_100'])} linha-horas); transformador/regulador "
                f"mais carregado {_fmt('{:.1f}',m['equipment_max_pct'])} %.",
                f"- **Reguladores:** {m['tap_operations']} operações de tap no dia."]
    return "\n".join([
        f"# Análise S0 × S1 — {feeder.upper()}, {_fmt('{:,d}',evs)} carros elétricos","",
        "Gerado por `python -m integration.analysis` a partir dos resultados salvos do estudo S1 (passo de 15 min, um dia). "
        "Os gráficos (um por arquivo, em inglês) estão nesta pasta.","",
        "## O que é comparado","",
        "- **S0 — caso base:** o alimentador IEEE 8500 com as cargas existentes e a curva diária, sem veículos elétricos.",
        f"- **S1 — com recarga de veículos elétricos:** o mesmo dia com {evs:,} carros recarregando em {len(sites)} estações: "
        f"{n['dc']} hubs DC, {n['ac']} eletropostos trifásicos e {n['ac1']} eletropostos monofásicos, "
        f"{_fmt('{:,.0f}',kw)} kW instalados (alocação por cobertura total; gráficos `stations_composition*.png`).".replace(f"{evs:,}",_fmt('{:,d}',evs)),
        "- **Critérios de qualidade de energia** (referências de comparação, nenhuma é a norma de Honduras): faixas A e B da "
        "ANSI C84.1; faixas adequada/precária/crítica, DRP, DRC, desequilíbrio (2 %) e fator de potência (0,92) do PRODIST "
        "Módulo 8 para média tensão, aplicados a um dia de 96 leituras (o PRODIST usa uma semana).","",
        "## Caso S0 (base)","",*bullet_case(m0,"S0"),"",
        "## Caso S1 (com veículos elétricos)","",*bullet_case(m1,"S1"),"",
        "## S0 × S1: todos os indicadores","",
        "⚠ marca piora em relação ao S0.",metrics_table(m0,m1),"",
        "## Limitações","",
        "- Um único dia representativo, uma semente de sorteio das sessões.",
        "- Desequilíbrio pela taxa PVUR (magnitudes das fases); o fator de desequilíbrio do PRODIST usa componentes de "
        "sequência e exigiria os ângulos, que não são salvos.",
        "- Harmônicos, flicker, transitórios e variações de frequência não são avaliados: o fluxo de potência é na frequência "
        "fundamental. Carregadores eletrônicos injetam harmônicos; isso exigiria um estudo harmônico próprio.",
        "- Rede de média tensão: a recarga em casa e a baixa tensão (transformadores de serviço) não estão representadas.",
        "- Os indicadores do PRODIST são usados como régua de comparação; os limites oficiais valem para medições de uma semana "
        "por unidade consumidora.",""])


def export_analysis(feeder_dir, config_dir="configs", evs=None):
    """Write feeder_dir/s0_vs_s1/ (figures + ANALISE.md + metrics.csv) from the saved S1 study."""
    import matplotlib
    matplotlib.use("Agg")
    from core.schemas import read_parameters
    from evcs.planning import from_config
    from integration.s1_study import load_fleets, base_overloads
    feeder_dir = Path(feeder_dir)
    s1_dir = feeder_dir/"s1"
    fleets_done = sorted(int(d.name.split("_")[1]) for d in s1_dir.glob("evs_*") if (d/"dados"/"voltages.csv").exists())
    if not fleets_done or not (s1_dir/"dados"/"s0_voltages.csv").exists():
        return None
    evs = evs or max(fleets_done)
    feeder = feeder_dir.name
    data = read_feeder_data(feeder)
    graph = feeder_graph(data); buses = bus_table(graph); slack = graph.graph["source"]
    three_phase = set(buses.bus[buses.phases=="ABC"])-{slack}
    loads = pd.DataFrame(data["loads"])[["bus","p_kw"]]
    f0, f1 = flow_from_csv(s1_dir/"dados","s0_"), flow_from_csv(s1_dir/f"evs_{evs}"/"dados")
    base_over = base_overloads(f0)
    m0,b0,u0 = case_metrics(f0,slack,three_phase,loads,base_over)
    m1,b1,u1 = case_metrics(f1,slack,three_phase,loads,base_over)
    fleets = load_fleets(feeder_dir/"evcs_screening")
    sites = fleets.loc[evs].sites
    params = from_config(read_parameters(Path(config_dir)/"evcs.yaml"))
    out = feeder_dir/"s0_vs_s1"
    save_figures(comparison_figures(f0,f1,m0,m1,b0,b1,u0,u1,buses,graph,slack,sites,params,
                                    fleets[fleets.index.isin(fleets_done)],evs,base_over),out,clean=True)
    pd.DataFrame({"S0": m0,"S1": m1}).to_csv(out/"metrics.csv")
    (out/"ANALISE.md").write_text(report(m0,m1,evs,sites,params,feeder),encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--results",default="results")
    parser.add_argument("--evs",type=int,help="S1 fleet to compare (default: the largest simulated)")
    args = parser.parse_args()
    print(export_analysis(Path(args.results)/study_feeder(args.configs),args.configs,args.evs))


if __name__ == "__main__":
    main()
