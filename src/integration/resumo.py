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
    ("s1/intraday_sensitivity.png","1_curvas_do_dia_por_frota.png",
     "Como o dia muda com 2000–5000 carros: demanda das estações, subestação, tensão mínima e linha mais carregada, a cada 15 min."),
    ("s1/impact_sensitivity.png","2_tensao_por_frota.png",
     "O que as estações fazem com a tensão em cada frota: queda causada por elas, barras afetadas, tensão mínima com e sem os reguladores."),
    ("s1/evs_{evs}/network_state.png","3_rede_S0_x_S1_{evs}_carros.png",
     "A rede no S0 e no S1 lado a lado: mapa de tensão, carregamento das linhas com as estações, equipamentos mais carregados."),
    ("integration/network_state_S1.png","4_integracao_rede_S0_x_S1.png",
     "O mesmo na integração S0–S4 (passo de 1 h), que é o ponto de partida do S2 (BESS) e do S3 (V2G)."),
]


def _num(x, digits=0):
    return f"{x:,.{digits}f}".replace(",","X").replace(".",",").replace("X",".")


def _peak(table):
    i = int(table.p_kw.idxmax())
    return table.p_kw.iloc[i], pd.Timestamp(table.time.iloc[i])


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
