"""Build the paper folder next to the repository: docs, configs, compact CSV tables, figures and one xlsx."""
import shutil, subprocess, sys
from pathlib import Path
import pandas as pd

REPO, OUT = Path(sys.argv[1]), Path(sys.argv[2])
R = REPO/"results"/"ieee8500"
I = R/"integration"
sys.path.insert(0, str(REPO/"src"))
from network.feeder_loader import load_feeder

OUT.mkdir(exist_ok=True)


def copy(src, dst):
    dst = OUT/dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


# 01 methodology and documentation
docs = {"docs/metodologia.md": "metodologia.md", "docs/S4_integracao.md": "S4_integracao.md",
        "docs/S4_equipo_es.md": "S4_equipo_es.md", "docs/S2_BESS_Kenia.md": "S2_BESS_Kenia.md",
        "docs/S3_V2G_Cesar.md": "S3_V2G_Cesar.md", "docs/validation.md": "validacao.md",
        "data/ieee8500/README.md": "rede_ieee8500_README.md", "src/evcs/README.md": "modulo_evcs_README.md",
        "src/bess/README.md": "modulo_bess_README.md", "src/v2g/README.md": "modulo_v2g_README.md",
        "docs/data_dictionary.md": "dicionario_de_dados.md", "docs/sensibilidade.md": "sensibilidade.md"}
for s, d in docs.items():
    copy(REPO/s, f"01_metodologia/{d}")

# 02 parameters
for y in sorted((REPO/"configs").glob("*.yaml")):
    copy(y, f"02_parametros_yaml/{y.name}")
copy(I/"manifest.json", "02_parametros_yaml/manifest_integracao.json")

# 03 CSV tables (summary tables only)
csv = {
    "s0_rede/inventario_totais.csv": R/"s0/dados/inventory_totals.csv",
    "s0_rede/inventario_reguladores.csv": R/"s0/dados/inventory_regulators.csv",
    "s0_rede/inventario_capacitores.csv": R/"s0/dados/inventory_capacitors.csv",
    "s0_rede/inventario_transformador.csv": R/"s0/dados/inventory_transformer.csv",
    "s0_rede/inventario_cargas_por_fase.csv": R/"s0/dados/inventory_loads_by_phase.csv",
    "s0_rede/codigos_de_linha.csv": R/"s0/dados/linecodes.csv",
    "s1_evcs/frotas_e_estacoes.csv": R/"evcs_screening/dados/fleet_scenarios.csv",
    "s1_evcs/capacidade_de_hospedagem.csv": R/"evcs_screening/dados/hosting_capacity.csv",
    "s1_evcs/sobrecarga_por_frota_15min.csv": R/"s1/dados/sobrecarga_por_frota.csv",
    "s1_evcs/s0_vs_s1_qualidade_3000.csv": R/"s0_vs_s1/metrics.csv",
    "s1_evcs/s1_intradiario_3000.csv": R/"s1/evs_3000/dados/intraday.csv",
    "s1_evcs/s0_intradiario.csv": R/"s1/dados/s0_intraday.csv",
    "s1_evcs/potencia_estacoes_15min_3000.csv": R/"s1/evs_3000/dados/station_power_15min.csv",
    "s1_evcs/sessoes_3000.csv": R/"s1/evs_3000/dados/sessions.csv",
    "s1_evcs/transformadores_servico_3000.csv": R/"s1/evs_3000/dados/service_transformers.csv",
    "s1_evcs/consumidores_zoneamento.csv": R/"zoneamento/dados/customers.csv",
    "s4_integracao/resumo_cenarios.csv": I/"summary.csv",
    "s4_integracao/impacto_vs_s0.csv": I/"impact.csv",
    "s4_integracao/criterio_de_sucesso.csv": I/"criterio.csv",
    "s4_integracao/bess_calibracao_linhas.csv": R/"s4_calibration/dados/bess_calibration.csv",
}
for d, s in csv.items():
    copy(s, f"03_tabelas_csv/{d}")
SENS = R/"sensitivity"
if (SENS/"sensitivity.csv").exists():
    copy(SENS/"sensitivity.csv", "03_tabelas_csv/sensibilidade/sensibilidade_S0_S4.csv")
    copy(SENS/"RELATORIO.md", "01_metodologia/sensibilidade_relatorio_gerado.md")
    copy(REPO/"configs/sensitivity.yaml", "02_parametros_yaml/sensitivity.yaml")
for sc in ("S2", "S4"):
    copy(I/f"modules/bess/{sc}/dispatch.csv", f"03_tabelas_csv/s4_integracao/bess_despacho_{sc}.csv")
    copy(I/f"modules/bess/{sc}/soc.csv", f"03_tabelas_csv/s4_integracao/bess_soc_{sc}.csv")

# Hourly series per scenario, derived from integration/dados (the raw per-bus tables are 230 MB)
slack = load_feeder("ieee8500").slack_bus


def lines(path):
    b = pd.read_csv(path, usecols=["time", "line", "element_type", "physical_phase", "loading_pct"])
    b = b[b.physical_phase & (b.element_type == "line")]
    return b.groupby(["time", "line"]).loading_pct.max().unstack()


def vmin(path):
    v = pd.read_csv(path)
    return v[v.bus != slack].groupby("time").v_pu.min()


def total(module, sc):
    p = pd.read_csv(I/f"modules/{module}/{sc}/profile.csv")
    return p.groupby("time").p_kw.sum()


s0_lines = lines(I/"dados/S0_branches.csv")
counted = s0_lines.columns[s0_lines.max() <= 100]  # lines of the success criterion
rows = []
for sc in ("S0", "S1", "S2", "S3", "S4"):
    src = pd.read_csv(I/f"dados/{sc}_source.csv").set_index("time")
    L = lines(I/f"dados/{sc}_branches.csv")
    frozen = I/f"dados/{sc}_frozen_taps_voltages.csv"
    t = pd.DataFrame({
        "cenario": sc, "hora": range(24),
        "potencia_subestacao_kw": src.p_kw.to_numpy(),
        "tensao_min_pu": vmin(I/f"dados/{sc}_voltages.csv").to_numpy(),
        "tensao_min_taps_s0_pu": vmin(frozen if frozen.exists() else I/"dados/S0_voltages.csv").to_numpy(),
        "linha_max_pct": L.max(axis=1).to_numpy(),
        "linha_max_criterio_pct": L[counted].max(axis=1).to_numpy(),
        "linhas_criterio_acima_100": (L[counted] > 100).sum(axis=1).to_numpy(),
        "ev_kw": (-total("evcs", sc)).reindex(src.index).fillna(0).to_numpy(),
        "v2g_frota_kw": total("v2g", sc).reindex(src.index).fillna(0).to_numpy(),
        "bess_kw": total("bess", sc).reindex(src.index).fillna(0).to_numpy(),
    })
    for c in [c for c in src.columns if c.startswith("tap_")]:
        t[c] = src[c].to_numpy()
    rows.append(t)
hourly = pd.concat(rows, ignore_index=True)
hourly_path = OUT/"03_tabelas_csv/s4_integracao/series_horarias_S0_S4.csv"
hourly.to_csv(hourly_path, index=False)

# 04 figures
figs = {f"00_resumo_{f.name}": f for f in sorted((R/"resumo").glob("*.png"))}
figs.update({
    "01_s0_topologia.png": R/"s0/network_topology.png",
    "02_s0_perfil_tensao_ponta.png": R/"s0/voltage_profile_peak.png",
    "03_capacidade_hospedagem.png": R/"evcs_screening/hosting_capacity_map.png",
    "04_zoneamento_estacoes.png": R/"zoneamento/zones_with_stations.png",
    "05_s1_transformadores_mais_menos.png": R/"s1/evs_3000/transformers_loading_selected.png",
    "06_s1_transformadores_intradiario.png": R/"s1/evs_3000/transformers_intraday_most_loaded.png",
    "07_s4_potencia_subestacao_cenarios.png": I/"demand_substation_power_per_scenario.png",
    "10_S4_taps_reguladores.png": I/"S4/impact_regulator_taps.png",
    "11_S4_queda_tensao_mapa.png": I/"S4/impact_voltage_drop_map.png",
})
figs["12_sensibilidade_S4_linhas_novas.png"] = R/"sensitivity/sensitivity_s4_new_overloads.png"
figs["13_sensibilidade_S4_tensao_minima.png"] = R/"sensitivity/sensitivity_s4_min_voltage.png"
for sc in ("S1", "S2", "S3", "S4"):
    figs[f"08_{sc}_carregamento_linhas.png"] = I/f"{sc}/network_line_loading_{sc}.png"
    figs[f"09_{sc}_mapa_tensao.png"] = I/f"{sc}/network_voltage_map_{sc}.png"
for d, s in figs.items():
    if s.exists():
        copy(s, f"04_figuras/{d}")
    else:
        print("missing figure", s)

# xlsx with one sheet per table and an index of sources
sheets = [
    ("S0_rede", R/"s0/dados/inventory_totals.csv", "Inventário do IEEE 8500 (caso base)"),
    ("S1_frotas", R/"evcs_screening/dados/fleet_scenarios.csv", "Frotas, estações, potência e CAPEX por frota"),
    ("S1_sobrecarga", R/"s1/dados/sobrecarga_por_frota.csv", "Indicadores da rede por frota, passo de 15 min"),
    ("S0xS1_qualidade", R/"s0_vs_s1/metrics.csv", "Qualidade de energia S0 x S1, 3.000 veículos"),
    ("S1_transformadores", R/"s1/evs_3000/dados/service_transformers.csv", "Transformadores de serviço, 3.000 veículos"),
    ("S4_cenarios", I/"summary.csv", "Indicadores S0-S4, passo de 1 h"),
    ("S4_criterio", I/"criterio.csv", "Critério de sucesso por cenário"),
    ("S4_impacto", I/"impact.csv", "Impacto de cada cenário contra o S0 (reguladores e taps travados)"),
    ("S4_horario", hourly_path, "Séries horárias por cenário (derivada de integration/dados)"),
    ("BESS_calibracao", R/"s4_calibration/dados/bess_calibration.csv", "Calibração da estratégia line_limit"),
    ("Sensibilidade", R/"sensitivity/sensitivity.csv", "29 casos de sensibilidade, S0-S4 de cada um (docs/sensibilidade.md)"),
]
source = lambda p: str(p.relative_to(REPO)) if REPO in p.parents else str(p.relative_to(OUT))
with pd.ExcelWriter(OUT/"tabelas_paper.xlsx", engine="openpyxl") as x:
    pd.DataFrame({"aba": [s[0] for s in sheets], "conteudo": [s[2] for s in sheets],
                  "fonte": [source(s[1]) for s in sheets]}).to_excel(x, sheet_name="Indice", index=False)
    for name, path, _ in sheets:
        pd.read_csv(path).to_excel(x, sheet_name=name, index=False)

commit = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h %ad", "--date=short"],
                        capture_output=True, text=True).stdout.strip()
print("commit", commit, "| files", sum(1 for p in OUT.rglob("*") if p.is_file()))
print(hourly[hourly.hora == 18][["cenario", "potencia_subestacao_kw", "tensao_min_pu", "linha_max_criterio_pct",
                                 "bess_kw", "v2g_frota_kw"]].round(3).to_string(index=False))
