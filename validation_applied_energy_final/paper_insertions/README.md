# Complemento rastreável para o manuscrito Applied Energy

Este pacote acompanha o manuscrito `EVCS_BESS_V2G_Applied_Energy_Elsevier_Template_v6.docx` sem alterar o Word nem o retrato curado em `paper/`. As Figuras 1–8 e Tabelas 1–12 existentes pertencem à campanha histórica **pandapower / redução MT / BESS única**. As Figuras 9–11, Tabelas 13–16 e Tabelas S1–S6 aqui produzidas apresentam **validação posterior no OpenDSS** e a alternativa distribuída como experimento distinto. A inserção no manuscrito exige atualizar resumo, método, discussão e conclusão para que cada número indique seu modelo elétrico. Não atribua os resultados distribuídos às 63 configurações históricas.

## Arquivos prontos para revisão

| Papel | Arquivos | Fonte |
|---|---|---|
| Fig. 9, erro elétrico pareado no S0 | `figures/fig9_model_voltage_discrepancy.{pdf,png,tif}` | arquivos brutos de 3h/18h em `results/validacao_fases_s0_ae` do projeto original |
| Fig. 10, operação horária S0/S1/S4 | `figures/fig10_native_operational_replay.{pdf,png,tif}` | novo piloto físico e replay distribuído, todos no OpenDSS |
| Fig. 11, compromisso controles/violações | `figures/fig11_thermal_voltage_control_tradeoff.{pdf,png,tif}` | mesmos replays físicos; controles e taps indicados |
| Tabela 13, resultados OpenDSS | `tables/table_13_opendss_operational_comparison.csv` | fonte, tensões e correntes de cada caso |
| Tabela 14, controles e taps fixos | `tables/table_14_controls_and_frozen_taps.csv` | replays de 24 h |
| Tabelas S1 e S2, erro do modelo | `tables/table_S1_paired_voltage_error.csv`, `tables/table_S2_paired_source_losses.csv` | comparação pareada histórica |
| Tabelas S3 e S4, recursos distribuídos | `tables/table_S3_distributed_bess_allocation.csv`, `tables/table_S4_distributed_v2g_allocation.csv` | despacho V3.0.1 reproduzido |
| Tabelas 15 e 16, S0–S4 distribuídos | `tables/table_15_distributed_s0_s4_native.csv`, `tables/table_16_distributed_s0_s4_changed.csv` | S2/S3 separados, 24 h no OpenDSS, controles nativos ou política C |
| Tabela S5, escopo e lacunas da conexão | `tables/table_S5_connection_scope_and_missing_costs.csv` | capacidades por barra, custos incrementais vazios por falta de orçamento |
| Tabela S6, S2/S3 históricos no OpenDSS | `tables/table_S6_historical_s2_s3_native_replay.csv` | reprodução da BESS única e estação V2G original |

`historical_figure_index.csv` aponta os PDFs/TIFFs de Figuras 1–8 e S1–S2 já presentes no retrato curado, com SHA-256 e rótulo de escopo. Esses arquivos não foram copiados ou modificados. `manuscript_edit_map.csv` associa cada bloco do Word que precisa ser revisado à evidência correspondente.

Os PDFs são vetoriais, com fonte incorporável; os TIFFs usam compressão LZW a 400 dpi; PNGs facilitam revisão. Cada figura ocupa um arquivo. `figure_data/` contém os pontos traçados, sem transformações ocultas. `manifest.json` registra caminhos absolutos e SHA-256 do manuscrito e das 43 fontes de dados, além das tabelas e das nove imagens finais. Todos os PDFs têm uma página e foram renderizados para inspeção visual.

## Legendas em inglês para o manuscrito

**Fig. 9.** Paired physical-phase voltage differences between the historical pandapower medium-voltage approximation and an OpenDSS medium-voltage equivalent in S0 at 03:00 and 18:00. Both use the modified study source, fixed reference taps, connected capacitors and primary-equivalent loads. Boxes show the interquartile range, central lines the median and whiskers 1.5 times the interquartile range; outliers are hidden for readability but included in the reported RMSE. Each hour has 3,820 matched physical bus-phase voltages. This comparison diagnoses model discrepancy and does not establish equivalence to the complete benchmark.

**Fig. 10.** Hourly substation import, number of newly overloaded line identities and minimum medium-voltage magnitude in the full OpenDSS feeder. S0, S1 and the historical single-BESS S4 replay use the same saved hourly injections; the later distributed S4 uses three BESS units with the same total 2.2 MW/7.2 MWh and 150 V2G vehicles. The changed-control trace additionally lowers the phase-C regulator targets by 0.5 V at the potential-transformer secondary. A newly overloaded line exceeds 100% in the plotted hour but was never overloaded in native-control S0 during the day. The 0.95-pu dotted line is the study's engineering screen, not a Honduran regulatory classification.

**Fig. 11.** Absolute daily thermal overload, undervoltage and overvoltage severity in the full OpenDSS feeder. Each metric sums positive phase-level excesses over the 24 one-hour intervals: thermal severity in percentage-point-hours and voltage severity in per-unit-hours. Native and changed regulator policies, together with their hourly S0-frozen-tap views, are reported separately. A zero count of newly overloaded lines does not imply zero absolute thermal excess or acceptable voltage.

**Table 13.** OpenDSS electrical outcomes for S0, S1 and S4 under common native controls. Single-BESS S4 reuses the historical commands; distributed S4 is the later three-BESS/V2G solution, not an additional member of the original 63-case campaign. Source import is positive; line loading uses assumed conductor ampacities. Excesses integrate with `dt_h=1` h.

**Table 14.** Native and modified-regulator operating views of the distributed S4, including taps fixed at the corresponding hourly S0 states. Capacitor controls remain automatic. The changed-control S0 is a separate reference for its frozen taps; the new-line count in the table is still measured against the original native-control S0 set.

**Tables S1–S2.** Physical-phase voltage discrepancies and substation active/reactive-power and loss comparisons for fixed-tap S0 at 03:00 and 18:00. The OpenDSS comparison circuit retains unloaded secondary elements and differs from the pandapower reduction; differences must not be interpreted as error from one component alone.

**Tables S3–S4.** Nameplate distribution of the later three-BESS solution and the 150 vehicles assigned across three V2G sites. Equal total BESS ratings do not imply equal spatial behavior or equal installed infrastructure cost.

**Tables 15–16.** S0–S4 phase-explicit replays for the existing distributed BESS/V2G design under native regulators and the separately saved phase-C regulator policy. S2 adds the three saved BESS schedules while the 150 vehicles retain their uncontrolled S1 charging; S3 replaces that charging with the distributed V2G schedules and adds no BESS. S4 combines both resources. The exact S4 injections reconstructed from S2 and S3 agree with the stored S4 injection profile to numerical precision. Native and changed-policy S0 references, and frozen-tap views, are retained in the underlying audit.

**Table S5.** Site-level active-power envelopes and unresolved monetary connection costs for the distributed design. The energy-based BESS and port-based V2G values are model assumptions, not quotes for transformers, bidirectional upgrades, protection, cabling, civil works or utility connection.

**Table S6.** Historic single-BESS and single-host V2G schedules replayed separately on the complete OpenDSS feeder with automatic controls and S0-frozen taps, preserving the original program of injections.

## Texto em inglês para inclusão após as seções 3.8 e 4.3

> **Electrical-fidelity check.** We subsequently compared the historical medium-voltage approximation with a phase-explicit OpenDSS representation under matched S0 conditions. At the 18:00 load peak, 3,820 bus-phase voltages gave an RMSE of 0.07277 pu and a maximum absolute discrepancy of 0.13751 pu; one line exceeded its study rating in pandapower versus 21 in the matched OpenDSS medium-voltage circuit. The models therefore cannot be treated as electrically equivalent. This experiment uses fixed regulator taps and primary-equivalent loading, whereas the operational replays retain native automatic controls and the complete secondary network. The comparisons identify a material fidelity limitation; they do not assign the entire discrepancy to a single phase, transformer or controller approximation.

> **Phase-explicit operational replay.** With the historical hourly injections replayed in the complete OpenDSS feeder, single-BESS S4 retained 23 newly overloaded lines relative to S0, a 120.80% maximum loading and a 0.92357-pu minimum voltage. The original claim of zero additional overloaded lines is thus confined to the historical pandapower approximation. A later spatially distributed arrangement of three BESS units and 150 V2G vehicles, with the same aggregate BESS ratings, produced zero newly overloaded lines in a native-control OpenDSS replay, but retained 108.51% maximum loading and a 0.93648-pu minimum voltage. Reducing the phase-C regulator targets by 0.5 V at the potential-transformer secondary raised the minimum to 0.93752 pu while producing 109.36% maximum loading. These are separate configurations, and neither demonstrates complete feeder adequacy. The observed differences motivate explicit model provenance, spatially resolved electrical validation and treatment of pre-existing violations.

> **Resource attribution and connection scope.** The later distributed design was decomposed on the same full-feeder model. With native automatic controls, BESS-only S2 left no new overloaded lines, with 114.57% maximum loading and 0.92438-pu minimum voltage; distributed V2G-only S3 left 32 new lines, with 129.70% maximum loading and 0.92558-pu minimum voltage. Their coordinated S4 reduced the maximum loading to 108.51% and raised the minimum to 0.93648 pu, but retained absolute violations. The 2.2-MW/7.2-MWh BESS and 150 vehicles were conserved across the isolated resource cases. Applying the original energy- and port-based cost rates gives the same USD 2.4048 million and USD 0.770 million component totals in single-site and distributed layouts, respectively; these figures exclude the unknown incremental costs of two new BESS connections and bidirectional upgrades at two existing AC charging sites. Equal modeled totals are not equal installed costs.

Esses parágrafos descrevem dados executados e servem como proposta de inclusão. Eles não substituem a revisão do resumo e das conclusões, que hoje se referem aos 63 casos da aproximação histórica. A seção 4.3 deve deixar de afirmar que **nenhuma** comparação OpenDSS foi feita, mas também não pode afirmar equivalência demonstrada. Números de normas de tensão são referências comparativas, não regras de Honduras.

## Reprodução

Na raiz do projeto Distributed-Stochastic, com os resultados históricos do projeto original no diretório irmão:

```powershell
python validation_applied_energy_final/build_paper_assets.py
```

O script recusa sobrescrever um pacote existente. `--refresh` só reescreve os arquivos do pacote se tabelas e figuras atuais ainda coincidirem com o manifesto SHA-256, para permitir correção editorial verificável sem alterar entradas históricas. Recalcula cada métrica diretamente das séries brutas, verifica 24 horas por caso e compara indicadores centrais com o relatório de auditoria. Não executa fluxo de potência nem otimização. Para reproduzir toda a evidência anterior, consulte `../README.md` e `../10_resultados_melhoria.md`.

Após a criação, execute `verify_paper_assets.py` e `build_editorial_index.py`, ambos no diretório pai deste pacote. O primeiro confirma os hashes, os formatos gráficos vetoriais e identidades essenciais das tabelas; o segundo só registra referências aos ativos históricos existentes.

## Limites de uso editorial

Figuras e tabelas aqui são candidatas à inclusão em versão **revisada** do artigo. O revisor deve decidir se a versão final permanece como estudo da aproximação histórica com validação limitante ou se incorpora uma seção específica da configuração distribuída. Nessa segunda opção, a comparação precisa de S2/S3 distribuídos, custos das conexões/estações, controles e testes de robustez coerentes com o novo objeto. Não substitua silenciosamente números das Tabelas 6–12 pelo replay distribuído. Nenhuma figura deste pacote prova conformidade regulatória ou probabilidade de sucesso em campo.
