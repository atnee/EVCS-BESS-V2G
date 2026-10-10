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
import pandas as pd
from integration.scenarios import study_feeder

FIGURES = [  # (source relative to results/<feeder>, name in resumo/, what it shows)
    ("s1/sobrecarga_por_frota.png","1_sobrecarga_por_frota.png",
     "S0 × S1 em função da quantidade de carros: quanto cada indicador da rede piora à medida que a demanda de EV cresce."),
    ("s1/intraday_sensitivity.png","2_curvas_do_dia_por_frota.png",
     "Como o dia muda com 2000–5000 carros: demanda das estações, subestação, tensão mínima e linha mais carregada, a cada 15 min."),
    ("s1/impact_sensitivity.png","3_tensao_por_frota.png",
     "O que as estações fazem com a tensão em cada frota: queda causada por elas, barras afetadas, tensão mínima com e sem os reguladores."),
    ("s1/evs_{evs}/network_state.png","4_rede_S0_x_S1_{evs}_carros.png",
     "A rede no S0 e no S1 lado a lado: mapa de tensão, carregamento das linhas com as estações, equipamentos mais carregados."),
    ("integration/network_state_S1.png","5_integracao_rede_S0_x_S1.png",
     "O mesmo na integração S0–S4 (passo de 1 h), que é o ponto de partida do S2 (BESS) e do S3 (V2G)."),
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


def plot_overload(t):
    """Network indicators against the number of cars (S0 = 0): how the added EV demand loads the feeder."""
    import matplotlib.pyplot as plt
    from integration.s0_study import _style, INK, MUTED, SURFACE, V_LIMITS
    from integration.impact import REGULATED, FROZEN, BASE_GRAY
    x = t.carros.to_numpy()
    labels = ["S0"]+[f"{int(c)}" for c in x[1:]]
    fig,axes = plt.subplots(2,3,figsize=(18,9.5),layout="constrained")
    fig.suptitle("Sobrecarga da rede em função da quantidade de carros elétricos (S0 = 0 carros; S1 a cada 15 min)",
                 fontsize=13,color=INK,x=.01,ha="left")
    def finish(ax, ylabel):
        ax.set_xticks(x,labels); ax.set_xlabel("carros elétricos",color=MUTED); ax.set_ylabel(ylabel,color=MUTED)
    def tag(ax, xs, ys, fmt, dy=6):
        for a,b in zip(xs,ys):
            ax.annotate(fmt(b),(a,b),xytext=(0,dy),textcoords="offset points",ha="center",fontsize=8,color=INK)

    ax = axes[0,0]; _style(ax,"Ponta na subestação: carga do S0 + EV")
    base = t.ponta_kw.iloc[0]
    ax.bar(x,[base]*len(x),width=700,color=BASE_GRAY,label="ponta do S0")
    ax.bar(x,t.ponta_kw-base,bottom=base,width=700,color=REGULATED,label="acréscimo com EV")
    tag(ax,x,t.ponta_kw,lambda v: f"{v:,.0f}".replace(",","."))
    ax.set_ylim(base*.9,t.ponta_kw.max()*1.03); ax.legend(frameon=False,fontsize=9,loc="upper left"); finish(ax,"kW")

    ax = axes[0,1]; _style(ax,"Demanda de EV na ponta (15 min) e energia no dia")
    ax.plot(x,t.ev_pico_kw,color=REGULATED,marker="o",lw=2,label="pico de EV (kW)")
    tag(ax,x,t.ev_pico_kw,lambda v: f"{v:,.0f} kW".replace(",","."))
    twin = ax.twinx(); twin.plot(x,t.ev_mwh,color=BASE_GRAY,marker="s",lw=2,ls=(0,(4,2)),label="energia (MWh/dia)")
    twin.set_ylabel("MWh/dia",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8); twin.spines["top"].set_visible(False)
    ax.legend(handles=ax.get_lines()+twin.get_lines(),frameon=False,fontsize=9,loc="upper left"); finish(ax,"kW")

    ax = axes[0,2]; _style(ax,"Linha mais carregada (sem sobrecargas que já existiam)")
    ax.plot(x,t.linha_max_pct,color=FROZEN,marker="o",lw=2)
    tag(ax,x,t.linha_max_pct,lambda v: f"{v:.1f} %")
    ax.axhline(100,color=MUTED,lw=1,ls=(0,(4,3))); ax.annotate("limite",(x[0],100),xytext=(2,3),textcoords="offset points",fontsize=8,color=MUTED)
    for a,h in zip(x,t.horas_linha_95):
        ax.annotate(f"{h:.2f} h ≥ 95 %".replace(".",","),(a,t.linha_max_pct.min()-.6),ha="center",fontsize=8,color=MUTED)
    ax.set_ylim(t.linha_max_pct.min()-1,101.5); finish(ax,"%")

    ax = axes[1,0]; _style(ax,"Tensão mínima do dia")
    ax.plot(x,t.vmin_sem_reg_pu,color=FROZEN,marker="o",lw=2,label="sem a ação dos reguladores (taps do S0)")
    ax.plot(x,t.vmin_pu,color=REGULATED,marker="s",lw=2,label="com os reguladores atuando")
    tag(ax,x,t.vmin_sem_reg_pu,lambda v: f"{v:.3f}",dy=-12)
    ax.axhline(V_LIMITS[0],color=MUTED,lw=1,ls=(0,(4,3))); ax.annotate("limite 0,95 pu",(x[-1],V_LIMITS[0]),xytext=(-2,3),
                                                                        textcoords="offset points",ha="right",fontsize=8,color=MUTED)
    ax.legend(frameon=False,fontsize=9,loc="lower left"); finish(ax,"pu")

    ax = axes[1,1]; _style(ax,"Queda de tensão causada pelas estações (taps do S0)")
    ax.bar(x,t.barras_queda_1pct,width=700,color=FROZEN,alpha=.35,label="barras com queda > 1 %")
    tag(ax,x,t.barras_queda_1pct,lambda v: f"{int(v)}")
    twin = ax.twinx(); twin.plot(x,t.queda_max_pct,color=FROZEN,marker="o",lw=2,label="maior queda (%)")
    twin.set_ylabel("maior queda (%)",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8); twin.spines["top"].set_visible(False)
    ax.legend(handles=[*ax.containers[:1],*twin.get_lines()],frameon=False,fontsize=9,loc="upper left"); finish(ax,"barras")

    ax = axes[1,2]; _style(ax,"Perdas no dia e transformador/regulador mais carregado")
    ax.plot(x,t.perdas_mwh,color=INK,marker="o",lw=2,label="perdas (MWh/dia)")
    tag(ax,x,t.perdas_mwh,lambda v: f"{v:.1f}".replace(".",","))
    twin = ax.twinx(); twin.plot(x,t.equip_max_pct,color=BASE_GRAY,marker="s",lw=2,ls=(0,(4,2)),label="regulador mais carregado (%)")
    twin.set_ylim(0,100); twin.set_ylabel("%",color=MUTED); twin.tick_params(colors=MUTED,labelsize=8); twin.spines["top"].set_visible(False)
    ax.legend(handles=ax.get_lines()+twin.get_lines(),frameon=False,fontsize=9,loc="upper left"); finish(ax,"MWh")
    return fig


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
        import matplotlib.pyplot as plt
        from integration.s0_study import SURFACE
        overload = overload_table(s1_dir)
        overload.to_csv(s1_dir/"dados"/"sobrecarga_por_frota.csv",index=False)
        fig = plot_overload(overload)
        fig.savefig(s1_dir/"sobrecarga_por_frota.png",dpi=170,facecolor=SURFACE)
        plt.close(fig)
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
