# Impacto nos resultados e no manuscrito

## Alegações históricas verificadas

O recálculo usa `results/ieee8500/integration/dados/` do Projeto A, apenas linhas e fases físicas. Novas linhas são a diferença entre conjuntos de identidades sobrecarregadas ao longo do dia, não subtração de contagens.

| Cenário | Pico kW | Novas linhas versus S0 | Maior carregamento % |
|---|---:|---:|---:|
| S0 | 11.782,932140 | 0 | 108,727431 |
| S1 | 14.252,199291 | 49 | 129,745326 |
| S2 | 12.766,037655 | 8 | 113,203523 |
| S3 | 13.345,342232 | 21 | 129,673492 |
| S4 | 12.700,115264 | 0 | 117,746140 |

Redução de pico: `100 × (14252,199290747303 − 12700,115263575932) / 14252,199290747303 = 10,8901369923%`. Fontes e recálculo em [alegacoes_recalculadas.csv](alegacoes_recalculadas.csv). Os números são internamente reproduzíveis **no modelo histórico**. O S4 não elimina a sobrecarga absoluta e não demonstra solução do IEEE completo.

## Campanha de 63 configurações

[campanha_315_verificada.csv](campanha_315_verificada.csv) verifica os 315 registros S0–S4: todos os picos coincidem com séries da fonte e todas as contagens de linhas novas coincidem com as séries de ramo ou máximos por linha preservados. Isso demonstra consistência de pós-processamento, não validação física da campanha.

Há séries completas de ramos em três configurações; 60 mantêm apenas os produtos reduzidos. O recálculo usa máximos retidos sempre que disponíveis. Não é possível reconstruir de máximos diários as correntes por fase em todas as horas, o erro entre motores ou o balanço completo. A campanha inclui sementes e sensibilidades determinísticas; não deve ser descrita como 63 amostras Monte Carlo independentes.

## Piloto OpenDSS já existente

O piloto de 24 h S0/S1/S4 foi reaproveitado sem duplicar a simulação. [piloto_opendss_recalculado.csv](piloto_opendss_recalculado.csv) deriva diretamente das tensões, ramos e fonte salvos no replay:

| Cenário | Vmin pu | Vmax pu | Maior carregamento % | Novas linhas versus S0 |
|---|---:|---:|---:|---:|
| S0 | 0,937069 | 1,061048 | 111,028879 | 0 |
| S1 | 0,919151 | 1,061425 | 132,130503 | 41 |
| S4 | 0,923569 | 1,061393 | 120,178886 | 23 |

Esse replay usa rede completa, fonte/controles nativos, mesma resolução horária entre seus cenários. O manifesto declara surrogate histórico da BESS em S2/S4, e o despacho não é necessariamente o mesmo do artigo. Logo, ele **não é experimento isolando apenas a troca de solver**. É evidência de que a configuração/despacho testados no modelo físico não satisfazem os limites de referência. Não foi chamado de solução validada apenas porque convergiu.

## Piloto novo: exatamente as injeções históricas

Após a conferência dos snapshots nativos, foi executado [pilot.py](pilot.py), sem otimização: 24 h de S0, S1 e S4 com reguladores automáticos, e S1/S4 com os 12 taps fixados aos valores horários do S0. Os controles dos capacitores permanecem nativos/ativos em ambas as visões. Topologia completa, fonte IEEE nativa, resolução de 1 h e ampacidades do estudo são comuns aos cinco casos. Os perfis vêm diretamente dos CSV históricos, com SHA-256 registrado.

| Cenário/controle | Pico kW | Vmin pu | Vmax pu | Máximo % | Novas linhas vs S0 |
|---|---:|---:|---:|---:|---:|
| S0 automático | 11.985,822711 | 0,937069 | 1,061048 | 111,028879 | 0 |
| S1 automático | 14.463,783487 | 0,919151 | 1,061425 | 132,130503 | 41 |
| S4 automático | 12.909,004119 | 0,923569 | 1,061393 | 120,797968 | 23 |
| S1 taps S0 | 14.523,906191 | 0,894552 | 1,058174 | 131,475904 | 52 |
| S4 taps S0 | 12.937,991276 | 0,925005 | 1,057583 | 120,660078 | 28 |

Fonte: [exact_profile_pilot/summary.csv](exact_profile_pilot/summary.csv). São 120 snapshots novos, todos convergidos, com resíduos máximos de balanço de 0,217917 kW e 0,148687 kvar. Os taps travados foram conferidos após cada solução. O status é `COMPUTED_NOT_EXTERNALLY_VALIDATED`, não certificação operacional.

O teste confirma mitigação parcial e invalida a transferência do zero de linhas novas para a rede completa nativa. Ele mantém os comandos de injeção históricos; o OpenDSS mantém também sua lei nativa de carga dependente de tensão. Portanto registra a transição para esse modelo físico, não uma ablação exclusiva de um parâmetro nem garantia de potência terminal constante fora dos limites do modelo de carga.

O despacho salvo foi também submetido novamente aos validadores existentes: BESS S2/S4 conserva SOC 0,30 no início/fim, sem rejeição de comandos; V2G S4 passa potência, disponibilidade, portas, SOC e reserva/energia de saída para 150 veículos. Os estados recalculados coincidem com os salvos. Ver [energia_historica_verificada.json](energia_historica_verificada.json). Isso valida o perfil energético comandado; não prova que um conversor real entregaria esse mesmo perfil sob toda violação elétrica.

## Evolução V3.0.1 — evidência separada

O candidato distribuído é avaliado via OpenDSS completo, não pelo pandapower histórico. O recálculo de `best/AC` confirma severidade térmica **148,882482926 pp·h**, tensão **2,164440206 pu·h**, mínimo **0,937516579 pu** e máximo **1,057934307 pu**. São somas de excessos por linha-fase e barra-fase, multiplicadas por `dt_h=1`; não são valores por linha agregada.

O relatório da referência registra 150,560541149 pp·h e 2,146471598 pu·h. A melhoria térmica vem acompanhada de agravamento da severidade de tensão. O estado `AC_ELIGIBLE_NOT_CERTIFIED` é coerente: elegibilidade sob guardas relativos não é cumprimento de 0,95–1,05 pu ou 100%. Não transfira esses números à campanha de 63 casos ou à BESS única.

## Figuras e tabelas

| Conteúdo | Decisão |
|---|---|
| Fluxograma metodológico e definições dos cenários | manter conceitos, atualizar backend e fronteira do modelo |
| Frota, sessões, energia, configurações e contratos | reaproveitar após conferir novamente conservação, serviço e SOC no caso escolhido |
| Localizações EVCS e mapas de cobertura | preservar como solução histórica; sua elegibilidade elétrica precisa ser reavaliada |
| Figuras de tensão, carregamento, perdas, taps e sensitividade elétrica | manter apenas como resultados da aproximação histórica ou substituir por reprodução física |
| Ganho de 10,89%, zero novas linhas e tabela principal S0–S4 | não manter como resultado validado do IEEE completo |
| Figuras de transformadores de serviço | hipótese analítica de atribuição EVCS; confrontar com o secundário explícito antes de apresentar como fluxo validado |
| Resultados de três BESS | seção/campanha própria, com orçamento total e controles declarados |

O manuscrito v6 foi adicionado após esta classificação inicial. O [mapa editorial](paper_insertions/manuscript_edit_map.csv) relaciona as alegações examinadas às novas tabelas/figuras propostas. A inserção continua dependente de revisão dos autores, especialmente porque as 63 configurações são da aproximação histórica e o estudo distribuído é posterior.
