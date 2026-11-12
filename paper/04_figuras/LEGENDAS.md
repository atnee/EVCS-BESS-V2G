# Figuras do artigo: arquivos, tamanho e legendas

Gerado à mão junto com as figuras (não é reescrito por `gerar_pasta_paper.py`). As figuras são desenhadas por módulos de `src/integration/` a partir de `results/` e `configs/`, sem simulação; os números nelas são os das tabelas indicadas.

## Como inserir no Word

- Inserir cada figura **a 100 %** (Inserir > Imagens, sem arrastar os cantos). O tamanho abaixo já é o da página; redimensionar distorce o texto e, se a proporção não estiver travada, estica a imagem (o problema da Fig. 2 na versão anterior).
- Para a submissão, enviar o PDF (vetorial) ou o TIFF. O PNG serve só para visualizar.
- Manter figura e legenda na mesma página: legenda logo abaixo da imagem, com "Manter com o próximo" no parágrafo da imagem (Parágrafo > Quebras de linha e de página), ou figura e legenda numa tabela de uma célula sem bordas. A Elsevier diagrama de novo na prova, mas o manuscrito deve chegar sem legenda partida.

| Fig. | Arquivo | Tamanho (mm) | Fonte dos números | Módulo |
|---|---|---|---|---|
| 1 | `fig1_framework` | 190 × 82 | `configs/` | `integration.framework_figure` |
| 2 | `fig2_station_siting` | 140 × 142 | `evcs_screening/dados/fleet_scenarios.csv` (3.000 VEs) | `integration.paper_s1_figures` |
| 3 | `fig3_ev_penetration` | 190 × 62 | `s1_evcs/sobrecarga_por_frota_15min.csv` | `integration.paper_s1_figures` |
| 4 | `fig4_service_transformers` | 190 × 80 | `s1_evcs/transformadores_servico_3000.csv` | `integration.paper_s1_figures` |
| 5 | `fig5_substation_demand` | 140 × 70 | `s4_integracao/series_horarias_S0_S4.csv` | `integration.paper_s1_figures` |
| 6 | `fig6_line_loading_maps` | 190 × 196 | `s4_integracao/criterio_de_sucesso.csv` | `integration.loading_maps` |
| 7 | `fig7_regulators_voltage` | 190 × 118 | `s4_integracao/criterio_de_sucesso.csv`, `series_horarias_S0_S4.csv` | `integration.regulator_figure` |
| 8 | `fig8_sensitivity_s4_seeds` | 190 × 80 | `sensibilidade/sensibilidade_S0_S4.csv` | `integration.sensitivity_figure` |
| S1 | `figS1_regulator_taps` | 190 × 62 | `s4_integracao/series_horarias_S0_S4.csv` (taps) | `integration.regulator_figure` |
| S2 | `figS2_sensitivity_s4_one_at_a_time` | 190 × 96 | `sensibilidade/sensibilidade_S0_S4.csv` | `integration.sensitivity_figure` |

## Legendas sugeridas (inglês)

**Fig. 1.** Modelling pipeline. (a) EVCS planning and S1 impact study at 15-min resolution; (b) integrated S0–S4 scenarios at 1-h resolution. Results of (a) and (b) are reported separately and never combined in one indicator. Dashed boxes: BESS dispatch calibration and sensitivity reruns. Every parameter is in the configuration files, marked as data (with source) or hypothesis. Box colours identify the modules, as in the network maps.

**Fig. 2.** Public charging stations sited by the planning model for 3,000 EVs on the IEEE 8500 test feeder: 4 DC hubs (H1–H4), 9 three-phase and 62 single-phase AC stations, with their coverage radii. The case is synthetic: customer load buses of the test feeder stand for where vehicles are, and no real locations are used.

**Fig. 3.** EV penetration against network constraints (EVCS study, 15-min step): (a) substation peak, with the increase relative to the S0 peak at 3,000 EVs; (b) feeder minimum voltage with the regulators acting and with the taps frozen at their S0 positions; (c) highest line loading. Dotted lines: 0.95 pu (ANSI C84.1 range A) and 100 % loading, references of the study, not Honduran standards.

**Fig. 4.** Service transformers with 3,000 EVs (S1, 15-min step). (a) Peak apparent-power loading, as a percentage of the kVA rating, of the five most and the three least loaded transformers in S0 and S1; * transformers that also feed a single-phase AC station (hypothesis: the station is connected to the customer's transformer). (b) Active power of the five most loaded transformers over the day; dotted lines give the kVA ratings as a reference.

**Fig. 5.** Substation active power over the day in S0–S4 (integration, 1-h step).

**Fig. 6.** Daily maximum loading of each line (regulators acting, 1-h step) in (a) S1, (b) S2, (c) S3 and (d) S4, on a common colour scale. C1–C3 (grey halo) are the three corridors overloaded in the 15-min S1 study: C1 the 397 ACSR exit trunk, C2 the #4 ACSR branch and C3 the 2/0 ACSR south trunk. New overloads count only lines at or below 100 % in S0; the ringed line (ln6351510-1, in C2) is already overloaded in S0. The V2G host station is open where its 150 EVs charge without control (S1, S2) and filled where V2G is active (S3, S4).

**Fig. 7.** Effect of the voltage regulators. (a)–(d) Daily minimum bus voltage in S1 and S4 with the regulators acting and with the taps frozen at their S0 positions, on a common colour scale. (e) Feeder minimum voltage over the day. 1-h step. Tap trajectories are in Fig. S1.

**Fig. 8.** Sensitivity of the S4 result to the random seed and to the time step. (a) Minimum voltage with the regulators acting; (b) highest loading of the lines at or below 100 % in that case's S0; shaded sides meet the success criterion. Segments join the current configuration (○) to the combined one (●, BESS × 0.5 + 1 V regulator band) for the same seed; the tick marks BESS × 0.5 alone. The 1 V band alone gives the same result as the combined configuration and is not drawn. The one-at-a-time cases are in Fig. S2.

**Fig. S1.** Tap positions of the four voltage regulators over the day in S0, S1 and S4 (1-h step). The S0 trajectory is the one held in the frozen-tap view. Numbers above each panel: tap operations per day in S0 / S1 / S4.

**Fig. S2.** Sensitivity of the S4 result to one change at a time from the base case: conventional dispatch rules, regulator set point and band, load level, BESS size and V2G fleet size. Axes as in Fig. 8. Loadings outside 97–103 % are drawn at the edge with their value. † With 0.9× load, ln6351510-1 is no longer overloaded in S0 and counts as a new overload.
