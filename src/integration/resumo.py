"""One-page summary of the studies of a feeder: results/<feeder>/resumo/ with the main figures and LEIA-ME.md.

python -m integration.resumo                 # feeder of configs/network.yaml
python -m integration.resumo --evs 4000      # fleet shown in the S0 x S1 table (default: the largest)
Built from files already saved by the S1 study and the integration; runs in seconds.
Called automatically at the end of integration.s1_study and integration.coordinator.
"""
from pathlib import Path
import argparse
import json
import shutil
import numpy as np
import pandas as pd
from integration.scenarios import study_feeder

FIGURES = [  # (source relative to results/<feeder>, name in resumo/, what it shows) — one plot per file
    ("s1/overload_substation_peak.png","1_overload_substation_peak.png",
     "Ponta da subestação conforme a quantidade de carros (S0 = 0 carros)."),
    ("s1/overload_line_loading.png","2_overload_line_loading.png",
     "Linha mais carregada conforme a quantidade de carros, com as horas acima de 95 %."),
    ("s1/overload_min_voltage.png","3_overload_min_voltage.png",
     "Tensão mínima do dia conforme a quantidade de carros, com e sem a ação dos reguladores."),
    ("s1/sensitivity_line_loading.png","4_day_line_loading.png",
     "Linha mais carregada ao longo do dia, S0 e cada frota (15 min)."),
    ("s1/sensitivity_min_voltage.png","5_day_min_voltage.png",
     "Tensão mínima do alimentador ao longo do dia, S0 e cada frota (15 min)."),
    ("s1/evs_{evs}/stations_map.png","6_stations_map_{evs}_evs.png",
     "Onde ficam os hubs e os eletropostos (cobertura total, raio de 500 m)."),
    ("s1/evs_{evs}/network_voltage_map_S1.png","7_voltage_map_S1_{evs}_evs.png",
     "Tensão de cada barra na ponta do S1 (compare com s1/evs_<n>/network_voltage_map_S0.png)."),
    ("s1/evs_{evs}/network_line_loading_S1.png","8_line_loading_S1_{evs}_evs.png",
     "Carregamento das linhas na ponta do S1 (compare com s1/evs_<n>/network_line_loading_S0.png)."),
]


def _num(x, digits=0):
    return f"{x:,.{digits}f}".replace(",","X").replace(".",",").replace("X",".")


def _peak(table):
    i = int(table.p_kw.idxmax())
    return table.p_kw.iloc[i], pd.Timestamp(table.time.iloc[i])


def overload_table(s1_dir, dt_h=.25):
    """One row per fleet, S0 as 0 cars: EV demand added and how each network indicator responds."""
    s1_dir = Path(s1_dir)
    s0 = pd.read_csv(s1_dir/"dados"/"s0_intraday.csv")
    fleets = sorted(int(d.name.split("_")[1]) for d in s1_dir.glob("evs_*") if (d/"summary.json").exists())
    first = json.loads((s1_dir/f"evs_{fleets[0]}"/"summary.json").read_text(encoding="utf-8"))["impact"]
    def indicators(day):
        return dict(ponta_kw=day.p_kw.max(),hora_ponta=pd.Timestamp(day.time.iloc[int(day.p_kw.idxmax())]),
                    linha_max_pct=day.line_max_pct.max(),horas_linha_95=(day.line_max_pct >= 95).sum()*dt_h,
                    equip_max_pct=day.equipment_max_pct.max(),vmin_pu=day.v_min_pu.min(),
                    horas_v_abaixo=(day.v_min_pu < .95).sum()*dt_h)
    rows = [dict(carros=0,ev_pico_kw=0.,ev_mwh=0.,**indicators(s0),vmin_sem_reg_pu=first["v_min_pu_s0"],queda_max_pct=0.,
                 barras_queda_1pct=0,perdas_mwh=first["losses_kwh_s0"]/1000,taps=sum(first["tap_operations_s0"].values()),
                 atendido=float("nan"))]
    for evs in fleets:
        d = s1_dir/f"evs_{evs}"
        summary = json.loads((d/"summary.json").read_text(encoding="utf-8"))
        m = summary["impact"]
        ev = pd.read_csv(next((d/"dados").glob("station_power_*min.csv"))).drop(columns="hour").sum(axis=1)
        energy = sum(t["energy_kwh"] for t in summary["types"].values())
        unserved = sum(t["unserved_kwh"] for t in summary["types"].values())
        rows.append(dict(carros=evs,ev_pico_kw=ev.max(),ev_mwh=ev.sum()*dt_h/1000,**indicators(pd.read_csv(d/"dados"/"intraday.csv")),
                         vmin_sem_reg_pu=m["frozen_taps"]["v_min_pu"],queda_max_pct=m["frozen_taps"]["max_drop_pct"],
                         barras_queda_1pct=m["frozen_taps"]["buses_drop_over_1pct"],perdas_mwh=m["losses_kwh"]/1000,
                         taps=sum(m["tap_operations"].values()),atendido=1-unserved/energy))
    return pd.DataFrame(rows)


def overload_markdown(t):
    s0 = t.iloc[0]
    cols = ["S0"]+[f"{int(c)} carros" for c in t.carros[1:]]
    def line(label, values):
        return f"| {label} | "+" | ".join(values)+" |"
    pct = lambda x,ref: "" if x == ref else f" ({'+' if x >= ref else ''}{_num(100*(x/ref-1),1)} %)"
    out = ["| | "+" | ".join(cols)+" |","|---|"+"---|"*len(cols),
           line("Demanda de EV na ponta (15 min)",[f"{_num(x)} kW" for x in t.ev_pico_kw]),
           line("Energia de EV no dia",[f"{_num(x,1)} MWh" for x in t.ev_mwh]),
           line("EV na ponta / ponta do S0",[f"{_num(100*x/s0.ponta_kw,1)} %" for x in t.ev_pico_kw]),
           line("**Ponta na subestação**",[f"{_num(x)} kW{pct(x,s0.ponta_kw)}" for x in t.ponta_kw]),
           line("Hora da ponta",[f"{h:%H:%M}" for h in t.hora_ponta]),
           line("**Linha mais carregada**",[f"{_num(x,1)} %" for x in t.linha_max_pct]),
           line("Horas com linha ≥ 95 %",[f"{_num(x,2)} h" for x in t.horas_linha_95]),
           line("Transformador/regulador mais carregado",[f"{_num(x,1)} %" for x in t.equip_max_pct]),
           line("**Tensão mínima sem a ação dos reguladores**",[f"{_num(x,3)} pu" for x in t.vmin_sem_reg_pu]),
           line("Tensão mínima com os reguladores",[f"{_num(x,3)} pu" for x in t.vmin_pu]),
           line("Maior queda causada pelas estações",[f"{_num(x,2)} %" for x in t.queda_max_pct]),
           line("Barras com queda > 1 %",[str(int(x)) for x in t.barras_queda_1pct]),
           line("Perdas no dia",[f"{_num(x,1)} MWh{pct(x,s0.perdas_mwh)}" for x in t.perdas_mwh]),
           line("Operações de tap no dia",[str(int(x)) for x in t.taps]),
           line("Recargas atendidas",["—"]+[f"{_num(100*x)} %" for x in t.atendido[1:]])]
    return "\n".join(out)


def overload_figures(t):
    """Network indicators against the number of cars (S0 = 0 cars), one plot each: {file name: Figure}."""
    from integration.s0_study import _style, new_axes, INK, MUTED, V_LIMITS
    from integration.impact import REGULATED, FROZEN, BASE_GRAY
    x = t.carros.to_numpy()
    labels = ["S0"]+[f"{int(c)}" for c in x[1:]]
    width = .35*float(min(np.diff(x))) if len(x) > 1 else 700
    def finish(ax, ylabel):
        ax.set_xticks(x,labels); ax.set_xlabel("number of electric vehicles (S0 = none)",color=MUTED)
        ax.set_ylabel(ylabel,color=MUTED)
    def tag(ax, xs, ys, fmt, dy=6):
        for a,b in zip(xs,ys):
            ax.annotate(fmt(b),(a,b),xytext=(0,dy),textcoords="offset points",ha="center",fontsize=8,color=INK)
    figures = {}

    ax = new_axes(); _style(ax,"Substation peak: S0 load plus the EV demand")
    base = t.ponta_kw.iloc[0]
    ax.bar(x,[base]*len(x),width=width,color=BASE_GRAY,label="S0 peak")
    ax.bar(x,t.ponta_kw-base,bottom=base,width=width,color=REGULATED,label="added by EVs")
    tag(ax,x,t.ponta_kw,lambda v: f"{v:,.0f}")
    ax.set_ylim(base*.9,t.ponta_kw.max()*1.03); ax.legend(frameon=False,fontsize=9,loc="upper left"); finish(ax,"kW")
    figures["overload_substation_peak"] = ax.figure

    ax = new_axes(); _style(ax,"EV demand at its peak (15 min) and EV energy per day")
    ax.plot(x,t.ev_pico_kw,color=REGULATED,marker="o",lw=2,label="EV peak (kW)")
    tag(ax,x,t.ev_pico_kw,lambda v: f"{v:,.0f} kW")
    twin = ax.twinx(); twin.plot(x,t.ev_mwh,color=BASE_GRAY,marker="s",lw=2,ls=(0,(4,2)),label="EV energy (MWh/day)")
    twin.set_ylabel("MWh/day",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8); twin.spines["top"].set_visible(False)
    ax.legend(handles=ax.get_lines()+twin.get_lines(),frameon=False,fontsize=9,loc="upper left"); finish(ax,"kW")
    figures["overload_ev_demand"] = ax.figure

    ax = new_axes(); _style(ax,"Most loaded line (base-case overloads left out)")
    ax.plot(x,t.linha_max_pct,color=FROZEN,marker="o",lw=2)
    tag(ax,x,t.linha_max_pct,lambda v: f"{v:.1f} %")
    ax.axhline(100,color=MUTED,lw=1,ls=(0,(4,3)))
    ax.annotate("limit",(x[0],100),xytext=(2,3),textcoords="offset points",fontsize=8,color=MUTED)
    low = t.linha_max_pct.min()
    for a,h in zip(x,t.horas_linha_95):
        ax.annotate(f"{h:.2f} h ≥ 95 %",(a,low-.6),ha="center",fontsize=8,color=MUTED)
    ax.set_ylim(low-1,max(101.5,t.linha_max_pct.max()+1)); finish(ax,"loading (%)")
    figures["overload_line_loading"] = ax.figure

    ax = new_axes(); _style(ax,"Lowest voltage of the day")
    ax.plot(x,t.vmin_sem_reg_pu,color=FROZEN,marker="o",lw=2,label="without regulator action (S0 taps)")
    ax.plot(x,t.vmin_pu,color=REGULATED,marker="s",lw=2,label="with the regulators acting")
    tag(ax,x,t.vmin_sem_reg_pu,lambda v: f"{v:.3f}",dy=-12)
    ax.axhline(V_LIMITS[0],color=MUTED,lw=1,ls=(0,(4,3)))
    ax.annotate("limit 0.95 pu",(x[-1],V_LIMITS[0]),xytext=(-2,3),textcoords="offset points",ha="right",fontsize=8,color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="lower left"); finish(ax,"voltage (pu)")
    figures["overload_min_voltage"] = ax.figure

    ax = new_axes(); _style(ax,"Voltage drop caused by the stations (S0 taps)")
    ax.bar(x,t.barras_queda_1pct,width=width,color=FROZEN,alpha=.35,label="buses dropping more than 1 %")
    tag(ax,x,t.barras_queda_1pct,lambda v: f"{int(v)}")
    twin = ax.twinx(); twin.plot(x,t.queda_max_pct,color=FROZEN,marker="o",lw=2,label="largest drop (%)")
    twin.set_ylabel("largest drop (%)",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8); twin.spines["top"].set_visible(False)
    ax.legend(handles=[*ax.containers[:1],*twin.get_lines()],frameon=False,fontsize=9,loc="upper left"); finish(ax,"buses")
    figures["overload_voltage_drop"] = ax.figure

    ax = new_axes(); _style(ax,"Daily losses")
    ax.plot(x,t.perdas_mwh,color=INK,marker="o",lw=2)
    tag(ax,x,t.perdas_mwh,lambda v: f"{v:.1f} MWh")
    finish(ax,"MWh/day")
    figures["overload_losses"] = ax.figure

    ax = new_axes(); _style(ax,"Most loaded transformer or regulator")
    ax.plot(x,t.equip_max_pct,color=BASE_GRAY,marker="s",lw=2)
    tag(ax,x,t.equip_max_pct,lambda v: f"{v:.0f} %")
    ax.set_ylim(0,100); finish(ax,"loading (%)")
    figures["overload_equipment_loading"] = ax.figure
    return figures


def s0_s1_table(s1_dir, evs):
    s0 = pd.read_csv(s1_dir/"dados"/"s0_intraday.csv")
    s1 = pd.read_csv(s1_dir/f"evs_{evs}"/"dados"/"intraday.csv")
    m = json.loads((s1_dir/f"evs_{evs}"/"summary.json").read_text(encoding="utf-8"))["impact"]
    (p0,t0),(p1,t1) = _peak(s0),_peak(s1)
    line0,line1 = s0.line_max_pct.max(),s1.line_max_pct.max()
    eq0,eq1 = s0.equipment_max_pct.max(),s1.equipment_max_pct.max()
    ops0,ops1 = sum(m["tap_operations_s0"].values()),sum(m["tap_operations"].values())
    rows = [
        ("Ponta na subestação",f"{_num(p0)} kW às {t0:%H:%M}",f"**{_num(p1)} kW às {t1:%H:%M}**",f"+{_num(100*(p1/p0-1),1)} %"),
        ("Linha mais carregada (sem sobrecargas que já existiam)",f"{_num(line0)} %",f"**{_num(line1)} %**",
         "no limite" if line1 >= 99.5 else "abaixo do limite"),
        ("Transformador/regulador mais carregado",f"{_num(eq0)} %",f"{_num(eq1)} %","não limita" if eq1 < 90 else "perto do limite"),
        ("Tensão mínima, com os reguladores atuando",f"{_num(m['v_min_pu_s0'],3)} pu",f"{_num(m['regulated']['v_min_pu'],3)} pu",
         "dentro de 0,95" if m["regulated"]["v_min_pu"] >= .95 else "**abaixo de 0,95**"),
        ("Tensão mínima sem a ação dos reguladores (taps do S0)",f"{_num(m['v_min_pu_s0'],3)} pu",
         f"**{_num(m['frozen_taps']['v_min_pu'],3)} pu**","o efeito real das estações"),
        ("Maior queda de tensão causada pelas estações","—",f"{_num(m['frozen_taps']['max_drop_pct'],2)} %",
         f"{m['frozen_taps']['buses_drop_over_1pct']} barras caem mais de 1 %"),
        ("Perdas no dia",f"{_num(m['losses_kwh_s0']/1000,1)} MWh",f"{_num(m['losses_kwh']/1000,1)} MWh",
         f"+{_num(100*(m['losses_kwh']/m['losses_kwh_s0']-1),1)} %"),
        ("Operações de tap no dia (todos os reguladores)",str(ops0),str(ops1),""),
    ]
    lines = ["| | S0 | S1 | |","|---|---|---|---|"]+[f"| {a} | {b} | {c} | {d} |" for a,b,c,d in rows]
    return "\n".join(lines)


def integration_table(integration_dir):
    s = pd.read_csv(integration_dir/"summary.csv").set_index("scenario")
    impact = integration_dir/"impact.csv"
    frozen = pd.read_csv(impact).set_index("scenario").get("frozen_taps_v_min_pu") if impact.exists() else None
    lines = ["| Cenário | Ponta (kW) | Perdas (MWh) | Tensão mínima | Sem a ação dos reguladores | EV atendido (MWh) | V2G devolvido (kWh) | BESS (kWh no dia) |",
             "|---|---|---|---|---|---|---|---|"]
    for name,r in s.iterrows():
        f = "—" if frozen is None or name not in frozen.index else f"{_num(frozen[name],3)} pu"
        lines.append(f"| {name} | {_num(r.peak_import_kw)} | {_num(r.loss_kwh/1000,1)} | {_num(r.v_min_pu,3)} pu | {f} | "
                     f"{_num(r.evcs_served_kwh/1000,1)} | {_num(r.v2g_delivered_kwh,1)} | {_num(r.bess_throughput_ac_kwh,0)} |")
    return "\n".join(lines)


def export_resumo(feeder_dir, evs=None):
    """Writes feeder_dir/resumo/ from what exists; missing studies are listed as not run."""
    feeder_dir = Path(feeder_dir)
    s1_dir, integration_dir = feeder_dir/"s1", feeder_dir/"integration"
    fleets = sorted(int(d.name.split("_")[1]) for d in s1_dir.glob("evs_*") if (d/"summary.json").exists())
    evs = evs or (max(fleets) if fleets else None)
    out = feeder_dir/"resumo"
    out.mkdir(parents=True,exist_ok=True)
    overload = None
    if fleets and (s1_dir/"dados"/"s0_intraday.csv").exists():
        import matplotlib
        matplotlib.use("Agg")
        overload = overload_table(s1_dir)
        overload.to_csv(s1_dir/"dados"/"sobrecarga_por_frota.csv",index=False)
        from integration.s0_study import save_figures
        for old in s1_dir.glob("sobrecarga_por_frota.png"):
            old.unlink()
        save_figures(overload_figures(overload),s1_dir)
    for old in out.glob("*.png"):
        old.unlink()
    shown = []
    for source,name,text in FIGURES:
        src = feeder_dir/source.format(evs=evs)
        if evs is not None and src.exists():
            shutil.copyfile(src,out/name.format(evs=evs))
            shown.append((name.format(evs=evs),text))
    doc = [f"# Resumo — {feeder_dir.name.upper()}","",
           "Comece por aqui. Figuras numeradas na ordem de leitura; as tabelas completas estão nas pastas de cada estudo "
           "(`s0/`, `evcs_screening/`, `s1/`, `integration/`), em subpastas `dados/`.",""]
    if overload is not None:
        doc += ["## Sobrecarga: S0 × S1 conforme a quantidade de carros (passo de 15 min)","",
                "A quantidade de carros é a demanda de EV acrescentada à rede; S0 é o caso sem carros.","",
                overload_markdown(overload),"",
                "Linhas em negrito: ponta da subestação, linha mais carregada e tensão mínima sem a ação dos reguladores "
                "são os indicadores que mais respondem à demanda de EV.",""]
    if evs is not None and (s1_dir/"dados"/"s0_intraday.csv").exists():
        doc += [f"## S0 × S1 com {evs} carros (estudo do EVCS, passo de 15 min)","",s0_s1_table(s1_dir,evs),"",
                "Os reguladores sobem o tap e escondem boa parte do efeito na tensão; a linha \"sem a ação dos reguladores\" "
                "mostra o que as estações fazem sozinhas.",""]
    else:
        doc += ["## S0 × S1","","Estudo S1 ainda não rodado: `python -m integration.s1_study`.",""]
    if (integration_dir/"summary.csv").exists():
        doc += ["## Integração S0–S4 (passo de 1 h)","",integration_table(integration_dir),"",
                "S2 = S1 + BESS, S3 = S1 com V2G, S4 = tudo junto. Escopo do S2 e do S3: `docs/escopo_S2_S3.md`.",""]
    doc += ["## Figuras",""]+[f"{i}. **`{n}`** — {t}" for i,(n,t) in enumerate(shown,1)]+[""]
    (out/"LEIA-ME.md").write_text("\n".join(doc),encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs",default="configs")
    parser.add_argument("--results",default="results")
    parser.add_argument("--evs",type=int)
    args = parser.parse_args()
    print(export_resumo(Path(args.results)/study_feeder(args.configs),args.evs))


if __name__ == "__main__":
    main()
